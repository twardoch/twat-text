# this_file: tests/aps/test_aps_semantics.py
"""Offline two-book provenance, bounded synthesis and recovery checks."""

import json
import re
import random
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from twat_text.aps import books as aps_books
from twat_text.aps import semantics as semantics


from quality_fixtures import review_fixture


class SemanticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sources = self.root / "sources"
        self.output = self.root / "output"
        self.work = self.root / "semantic"
        for name, text in {
            "A": "# Classes\n\nA class is a type.\n\nA class is a CSS selector.\n\n```python\nclass A: pass\n```\n",
            "B": "# Empty teaching content\n\nPublication notice.\n",
        }.items():
            folder = self.sources / name
            folder.mkdir(parents=True)
            (folder / "00.md").write_text(text)
        self.task1 = aps_books.connect(self.root / "task1")
        self.addCleanup(self.task1.close)
        aps_books.prepare(
            self.task1,
            [self.sources],
            "prompt",
            1000,
            list,
            lambda text, limit: text.splitlines(keepends=True),
        )
        for row in self.task1.execute("SELECT * FROM chunks WHERE state='pending'").fetchall():
            self.task1.execute(
                "UPDATE chunks SET response=original,state='done',model='freellm:one',attempts=1 WHERE book=? AND ordinal=?",
                (row["book"], row["ordinal"]),
            )
            self.task1.execute(
                "INSERT INTO attempts VALUES(?,?,?,1,2,'done')",
                (row["book"], row["ordinal"], "freellm:one"),
            )
        self.task1.commit()
        review_fixture(self.task1)
        aps_books.publish(self.task1, self.output)
        self.db = semantics.connect(self.work)
        self.addCleanup(self.db.close)
        self.routes = ["freellm:one", "freellm:two"]
        self.calls = 0

    def inspect(self):
        return semantics.inspect_task1(self.root / "task1" / "aps.sqlite3", self.output)

    def complete(self, payload, route, prompt):
        self.calls += 1
        if prompt == semantics.EXTRACT:
            text = payload["aps"]
            concepts = []
            if "class" in text:
                concepts = [
                    {
                        "label": "class",
                        "domain": "CSS" if "CSS" in text else "Python",
                        "version": "",
                        "definition": text.strip(),
                        "aliases": ["class construct"],
                        "keywords": ["classes"],
                        "uncertainties": [],
                        "evidence": [
                            {
                                "field": field,
                                "start": 0,
                                "end": len(payload[field]),
                                "text": payload[field],
                            }
                            for field in ("aps", "original")
                        ],
                    }
                ]
            return json.dumps(
                {
                    "concepts": concepts,
                    "empty_reason": "No teachable concept" if not concepts else "",
                }
            )
        if prompt == semantics.RELATE:
            return json.dumps(
                {
                    "relations": [],
                    "uncertainties": ["Different domains remain separate"],
                }
            )
        return json.dumps(
            {
                "sections": [
                    {
                        "title": payload["scope"],
                        "outcomes": ["Study supplied concepts"],
                        "child_ids": [c["id"] for c in payload["children"]],
                        "prerequisite_ids": [],
                        "uncertainties": [],
                    }
                ]
            }
        )

    def build(self):
        return semantics.build(self.db, *self.inspect(), self.routes, self.complete)

    def test_all_views_preserve_empty_book_and_distinct_meanings_and_resume(self):
        dataset = self.build()
        self.assertEqual(len(dataset["concepts"]), 2)
        self.assertEqual(len(dataset["books"]), 2)
        self.assertEqual(dataset["books"][1]["concept_ids"], [])
        self.assertEqual(
            {r["disposition"] for r in dataset["coverage"]},
            {
                "structural context",
                "blank separator",
                "extracted records",
                "explicit empty finding",
            },
        )
        self.assertEqual(
            len(dataset["coverage"]),
            self.task1.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        )
        calls = self.calls
        self.assertEqual(self.build(), dataset)
        self.assertEqual(calls, self.calls)
        target = semantics.render(self.db, self.work, dataset)
        for name in (
            "books",
            "toc",
            "semantic-map",
            "keywords",
            "glossary",
            "index",
            "coverage",
        ):
            self.assertTrue((target / (name + ".json")).is_file())
            self.assertTrue((target / (name + ".md")).is_file())
        manifest = json.loads((target / "manifest.json").read_text())
        self.assertIn("awaiting", manifest["status"])
        self.assertTrue(
            all(semantics.file_sha(target / name) == digest for name, digest in manifest["artifacts"].items())
        )

    def test_pending_prose_blocks_even_when_other_fragments_done(self):
        self.task1.execute("UPDATE chunks SET state='pending' WHERE model IS NOT NULL")
        self.task1.commit()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.inspect()

    def test_done_without_generated_attempt_is_not_aps(self):
        self.task1.execute("DELETE FROM attempts")
        self.task1.commit()
        with self.assertRaisesRegex(ValueError, "successful APS attempt"):
            self.inspect()

    def test_source_and_output_drift_are_rejected(self):
        path = self.sources / "A" / "00.md"
        original = path.read_text()
        path.write_text("changed")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.inspect()
        path.write_text(original)
        (self.output / "A.md").write_text("changed")
        with self.assertRaisesRegex(ValueError, "assembly"):
            self.inspect()

    def test_missing_book_identity_and_source_join_fail(self):
        self.task1.execute("DELETE FROM books WHERE id='B'")
        self.task1.commit()
        with self.assertRaisesRegex(ValueError, "identities"):
            self.inspect()

    def test_bad_evidence_retains_raw_response_and_blocks_resume(self):
        def invalid(payload, route, prompt):
            result = json.loads(self.complete(payload, route, prompt))
            if result["concepts"]:
                result["concepts"][0]["evidence"][0]["text"] = "invented"
            return json.dumps(result)

        with self.assertRaisesRegex(ValueError, "Unaccepted"):
            semantics.build(self.db, *self.inspect(), self.routes, invalid)
        row = self.db.execute("SELECT * FROM jobs WHERE state='failed'").fetchone()
        self.assertIn("invented", row["raw"])
        with self.assertRaisesRegex(ValueError, "Unresolved"):
            self.build()

    def test_unknown_completion_is_not_automatically_resubmitted(self):
        def timeout(*args):
            raise TimeoutError("private secret should not be stored")

        with self.assertRaisesRegex(ValueError, "Unaccepted"):
            semantics.build(self.db, *self.inspect(), self.routes, timeout)
        row = self.db.execute("SELECT * FROM jobs").fetchone()
        self.assertEqual(row["state"], "unknown")
        self.assertEqual(row["error"], "TimeoutError")
        with self.assertRaisesRegex(ValueError, "Unresolved"):
            self.build()
        self.assertEqual(self.calls, 0)

    def test_interrupted_running_becomes_unknown(self):
        run_id, manifest, fragments = self.inspect()
        self.db.execute("INSERT INTO jobs(id,stage,input,state) VALUES('interrupted','extract','{}','running')")
        self.db.commit()
        with self.assertRaisesRegex(ValueError, "Unresolved"):
            semantics.build(self.db, run_id, manifest, fragments, self.routes, self.complete)
        self.assertEqual(self.db.execute("SELECT state FROM jobs").fetchone()[0], "unknown")

    def test_prompt_or_input_changes_cannot_reuse_acceptance(self):
        self.build()
        with patch.object(semantics, "EXTRACT", semantics.EXTRACT + "changed"):
            with self.assertRaisesRegex(ValueError, "Changed input"):
                self.build()
        run_id, manifest, fragments = self.inspect()
        with self.assertRaisesRegex(ValueError, "Changed input"):
            semantics.build(self.db, "changed", manifest, fragments, self.routes, self.complete)

    def test_hierarchy_cannot_drop_children_or_invent_targets(self):
        payload = {"children": [{"id": "a"}, {"id": "b"}]}
        result = {
            "sections": [
                {
                    "title": "Test",
                    "outcomes": [],
                    "child_ids": ["a"],
                    "prerequisite_ids": [],
                    "uncertainties": [],
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "lost"):
            semantics.group_valid(result, payload)
        result["sections"][0]["child_ids"] = ["a", "b"]
        result["sections"][0]["prerequisite_ids"] = ["a"]
        with self.assertRaisesRegex(ValueError, "Cyclic"):
            semantics.group_valid(result, payload)

    def test_explicit_rejections_rotate_bounded_routes(self):
        routes = []

        def reject(payload, route, prompt):
            routes.append(route)
            raise ValueError("rejected")

        with self.assertRaisesRegex(ValueError, "Unaccepted"):
            semantics.build(self.db, *self.inspect(), self.routes, reject)
        self.assertEqual(routes, ["freellm:one", "freellm:two", "freellm:one"])
        self.assertEqual(self.db.execute("SELECT state FROM jobs").fetchone()[0], "failed")

    def test_hierarchy_is_bounded_and_preserves_leaf_records(self):
        children = [{"id": str(i), "label": "Topic " + "x" * 600} for i in range(60)]
        nodes = []
        roots = semantics.hierarchy(self.db, "Many topics", children, self.routes, self.complete, nodes)
        self.assertEqual(len(roots), 1)
        leaves = {child["id"] for child in children}
        represented = {cid for node in nodes for cid in node["child_ids"] if cid in leaves}
        self.assertEqual(represented, leaves)
        self.assertGreater(len(nodes), 1)

    def test_reconcile_unknown_then_resume_without_losing_attempt(self):
        def timeout(*args):
            raise TimeoutError()

        with self.assertRaises(ValueError):
            semantics.build(self.db, *self.inspect(), self.routes, timeout)
        row = self.db.execute("SELECT * FROM jobs").fetchone()
        semantics.reconcile(
            self.db,
            row["id"],
            "confirmed-no-completion",
            "Provider usage record checked; no request accepted",
        )
        dataset = self.build()
        self.assertEqual(len(dataset["concepts"]), 2)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) FROM attempts WHERE state='unknown'").fetchone()[0],
            1,
        )
        self.assertIn(
            "Provider usage",
            self.db.execute("SELECT reconciliation FROM jobs WHERE id=?", (row["id"],)).fetchone()[0],
        )

    def test_recovered_response_is_validated_before_acceptance(self):
        def timeout(*args):
            raise TimeoutError()

        with self.assertRaises(ValueError):
            semantics.build(self.db, *self.inspect(), self.routes, timeout)
        row = self.db.execute("SELECT * FROM jobs").fetchone()
        with self.assertRaises(ValueError):
            semantics.reconcile(self.db, row["id"], "recovered-response", "Usage record", "{}")
        raw = self.complete(json.loads(row["input"]), row["route"], semantics.EXTRACT)
        semantics.reconcile(self.db, row["id"], "recovered-response", "Provider response archive", raw)
        self.assertEqual(
            self.db.execute("SELECT state FROM jobs WHERE id=?", (row["id"],)).fetchone()[0],
            "done",
        )
        self.build()

    def test_conflicting_version_claims_remain_separate(self):
        original_complete = self.complete

        def versioned(payload, route, prompt):
            result = json.loads(original_complete(payload, route, prompt))
            if prompt == semantics.EXTRACT and result["concepts"]:
                first = result["concepts"][0]
                first["version"] = "Python 2"
                second = dict(first, version="Python 3", definition="A distinct scoped claim")
                result["concepts"].append(second)
            return json.dumps(result)

        dataset = semantics.build(self.db, *self.inspect(), self.routes, versioned)
        self.assertEqual(len(dataset["concepts"]), 4)
        self.assertEqual({c["version"] for c in dataset["concepts"]}, {"Python 2", "Python 3"})
        self.assertTrue(dataset["ambiguities"])

    def test_render_rejects_missing_leaf_and_keeps_current_snapshot(self):
        dataset = self.build()
        semantics.render(self.db, self.work, dataset)
        pointer = (self.work / "current.json").read_bytes()
        dataset["coverage"] = []
        with self.assertRaisesRegex(ValueError, "coverage mismatch"):
            semantics.render(self.db, self.work, dataset)
        self.assertEqual((self.work / "current.json").read_bytes(), pointer)

    def test_render_failure_cannot_mutate_previously_selected_snapshot(self):
        dataset = self.build()
        target = semantics.render(self.db, self.work, dataset)
        previous = {path.name: path.read_bytes() for path in target.iterdir()}
        pointer = (self.work / "current.json").read_bytes()
        original_write = semantics.write
        calls = 0

        def failing_write(path, value):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated disk failure")
            original_write(path, value + "changed renderer output")

        with patch.object(semantics, "write", failing_write):
            with self.assertRaises(OSError):
                semantics.render(self.db, self.work, dataset)
        self.assertEqual((self.work / "current.json").read_bytes(), pointer)
        self.assertEqual({path.name: path.read_bytes() for path in target.iterdir()}, previous)

    def test_changed_renderer_creates_new_snapshot_without_changing_dataset(self):
        dataset = self.build()
        first = semantics.render(self.db, self.work, dataset)
        first_manifest = (first / "manifest.json").read_bytes()
        original = semantics.markdown
        with patch.object(
            semantics,
            "markdown",
            lambda *args: original(*args) + "\nRenderer revision.\n",
        ):
            second = semantics.render(self.db, self.work, dataset)
        self.assertNotEqual(first, second)
        self.assertEqual((first / "manifest.json").read_bytes(), first_manifest)
        self.assertEqual(
            json.loads(first_manifest)["dataset_sha256"],
            json.loads((second / "manifest.json").read_text())["dataset_sha256"],
        )
        self.assertEqual(
            second,
            self.work / json.loads((self.work / "current.json").read_text())["snapshot"],
        )

    def test_globally_cyclic_proposals_remain_reviewable_and_resumable(self):
        calls = 0

        def conflicting(payload, route, prompt):
            nonlocal calls
            if prompt != semantics.RELATE:
                return self.complete(payload, route, prompt)
            calls += 1
            left, right = [child["id"] for child in payload["children"]]
            if calls % 2 == 0:
                left, right = right, left
            return json.dumps(
                {
                    "relations": [
                        {
                            "from": left,
                            "to": right,
                            "kind": "prerequisite",
                            "reason": "A locally proposed dependency",
                        }
                    ],
                    "uncertainties": [],
                }
            )

        dataset = semantics.build(self.db, *self.inspect(), self.routes, conflicting)
        self.assertTrue(dataset["prerequisite_conflicts"])
        self.assertTrue(dataset["relations"])
        semantics.acyclic([(edge["from"], edge["to"]) for edge in dataset["accepted_prerequisites"]])
        target = semantics.render(self.db, self.work, dataset)
        self.assertIn("Unresolved prerequisite", (target / "coverage.md").read_text())
        before = calls
        resumed = semantics.build(self.db, *self.inspect(), self.routes, conflicting)
        self.assertEqual(dataset, resumed)
        self.assertEqual(calls, before)

    def test_malformed_nested_results_are_known_invalid_with_bounded_retries(self):
        payload = {"children": [{"id": "a"}, {"id": "b"}]}
        cases = [
            (semantics.GROUP, semantics.group_valid, {"sections": [None]}),
            (
                semantics.RELATE,
                semantics.relate_valid,
                {"relations": [None], "uncertainties": []},
            ),
            (
                semantics.RELATE,
                semantics.relate_valid,
                {
                    "relations": [
                        {
                            "from": [],
                            "to": "b",
                            "kind": "related",
                            "reason": "bad endpoint",
                        }
                    ],
                    "uncertainties": [],
                },
            ),
        ]
        for index, (prompt, validator, response) in enumerate(cases):
            with self.subTest(response=response):
                with self.assertRaisesRegex(ValueError, "Unaccepted"):
                    semantics.job(
                        self.db,
                        "invalid-" + str(index),
                        payload,
                        prompt,
                        self.routes,
                        validator,
                        lambda *args: json.dumps(response),
                    )
                row = self.db.execute("SELECT * FROM jobs WHERE stage=?", ("invalid-" + str(index),)).fetchone()
                self.assertEqual(row["state"], "failed")
                self.assertEqual(
                    self.db.execute("SELECT COUNT(*) FROM attempts WHERE job=?", (row["id"],)).fetchone()[0],
                    3,
                )
                self.assertEqual(
                    self.db.execute(
                        "SELECT COUNT(*) FROM attempts WHERE job=? AND state='unknown'",
                        (row["id"],),
                    ).fetchone()[0],
                    0,
                )

    def test_toc_and_keywords_resolve_labels_and_order_from_global_root(self):
        dataset = self.build()
        target = semantics.render(self.db, self.work, dataset)
        toc = (target / "toc.md").read_text()
        self.assertLess(toc.index("All books: proposed learning order"), toc.index("Domain:"))
        self.assertIn("[class — Python]", toc)
        keywords = (target / "keywords.md").read_text()
        self.assertIn("[class — Python]", keywords)
        self.assertIn("[class — CSS]", keywords)
        for text in (toc, keywords):
            self.assertNotRegex(text, r"`[a-f0-9]{64}`")
            links = re.findall(r"\]\(([^)]+)\)", text)
            self.assertTrue(links)
            for link in links:
                filename, anchor = link.split("#")
                document = (target / (filename or "toc.md")).read_text()
                self.assertIn('id="' + anchor + '"', document)

    def test_incoming_index_relation_links_other_endpoint_and_preserves_direction(self):
        dataset = self.build()
        first, second = dataset["concepts"]
        dataset["relations"] = [
            {
                "from": first["id"],
                "to": second["id"],
                "kind": "prerequisite",
                "reason": "Proposed prerequisite",
            }
        ]
        target = semantics.render(self.db, self.work, dataset)
        index = (target / "index.md").read_text()
        incoming = index.split("## " + second["label"] + " — " + second["domain"])[1]
        self.assertIn("Requires: [" + first["label"] + " — " + first["domain"] + "]", incoming)
        self.assertNotIn("Requires: [" + second["label"] + " — " + second["domain"] + "]", incoming)
        outgoing = index.split("## " + first["label"] + " — " + first["domain"])[1].split("## ")[0]
        self.assertIn("Prerequisite for:", outgoing)

    def test_neighboring_code_is_bounded_citable_and_not_target_evidence(self):
        observed = []

        def using_code(payload, route, prompt):
            result = json.loads(self.complete(payload, route, prompt))
            if prompt == semantics.EXTRACT and result["concepts"] and "CSS" not in payload["aps"]:
                context = payload["context"]
                self.assertTrue(context)
                self.assertLessEqual(sum(len(item["text"]) for item in context), semantics.CONTEXT_LIMIT)
                code = context[0]
                self.assertEqual(code["locator"]["book"], payload["locator"]["book"])
                self.assertNotEqual(code["locator"]["ordinal"], payload["locator"]["ordinal"])
                self.assertTrue(code["evidence_eligible"])
                self.assertIn("class A: pass", code["text"])
                result["concepts"][0]["definition"] = "The example declares class A."
                result["concepts"][0]["evidence"].append(
                    {
                        "field": code["field"],
                        "start": 0,
                        "end": len(code["text"]),
                        "text": code["text"],
                    }
                )
                observed.append((payload, result))
            return json.dumps(result)

        dataset = semantics.build(self.db, *self.inspect(), self.routes, using_code)
        self.assertTrue(observed)
        concept = next(c for c in dataset["concepts"] if c["domain"] == "Python")
        occurrence = concept["occurrences"][0]
        self.assertTrue(occurrence["context_sources"])
        payload, result = observed[0]
        result["concepts"][0]["evidence"] = result["concepts"][0]["evidence"][-1:]
        with self.assertRaisesRegex(ValueError, "APS and original"):
            semantics.extract_valid(result, payload)
        result["concepts"][0]["evidence"][0]["text"] = "invented code"
        with self.assertRaisesRegex(ValueError, "does not match"):
            semantics.extract_valid(result, payload)

    def test_identical_rerender_reuses_verified_snapshot_without_rewriting(self):
        dataset = self.build()
        first = semantics.render(self.db, self.work, dataset)
        timestamps = {path.name: path.stat().st_mtime_ns for path in first.iterdir()}
        second = semantics.render(self.db, self.work, dataset)
        self.assertEqual(first, second)
        self.assertEqual({path.name: path.stat().st_mtime_ns for path in first.iterdir()}, timestamps)
        self.assertEqual(list((self.work / "snapshots").glob(".staging-*")), [])
        (first / "toc.md").write_text("external corruption")
        pointer = (self.work / "current.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "immutable snapshot"):
            semantics.render(self.db, self.work, dataset)
        self.assertEqual((self.work / "current.json").read_bytes(), pointer)
        self.assertEqual((first / "toc.md").read_text(), "external corruption")

    def test_hierarchy_and_relationship_prerequisites_share_global_cycle_check(self):
        relation = {
            "from": "a",
            "to": "b",
            "kind": "prerequisite",
            "reason": "Proposed dependency",
        }
        node = {
            "id": "node",
            "sections": [{"child_ids": ["a"], "prerequisite_ids": ["b"]}],
        }
        accepted, conflicts = semantics.prerequisite_review([relation], [node])
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["origin"], {"node": "node", "section": 0})
        self.assertEqual(
            {(edge["from"], edge["to"]) for edge in accepted + conflicts},
            {("a", "b"), ("b", "a")},
        )

    def test_code_context_limits_and_same_source_boundary(self):
        run_id, manifest, fragments = self.inspect()
        row = next(row for row in fragments if not row["protected"] and "class" in row["original"])
        code = next(row for row in fragments if row["protected"] and "```" in row["original"])
        long_code = dict(code, original="```python\n" + "print('example')\n" * 4000 + "```\n")
        unrelated = dict(
            code,
            book="different-book",
            source="different-source",
            ordinal=row["ordinal"] + 1,
        )
        payload = semantics.extraction_payload(row, semantics.source_locator(row, run_id), [unrelated, long_code])
        self.assertEqual(len(payload["context"]), 1)
        context = payload["context"][0]
        self.assertTrue(context["truncated"])
        self.assertLessEqual(len(context["text"]), semantics.CONTEXT_LIMIT)
        self.assertEqual(context["text"], long_code["original"][: context["end"]])
        self.assertEqual(context["text_sha256"], semantics.sha(context["text"]))
        self.assertLessEqual(
            len(semantics.encoded(payload)) + len(semantics.EXTRACT),
            semantics.INPUT_LIMIT,
        )

    def test_global_toc_roots_must_reach_every_concept(self):
        dataset = self.build()
        dataset["roots"] = []
        with self.assertRaisesRegex(ValueError, "Global roots omit"):
            semantics.render(self.db, self.work, dataset)

    def test_long_duplicate_chain_preserves_origins_without_whole_graph_rechecks(self):
        count = 2000
        relations = [
            {
                "from": f"n{index:05}",
                "to": f"n{index + 1:05}",
                "kind": "prerequisite",
                "reason": f"Source {copy}",
            }
            for index in range(count)
            for copy in range(3)
        ]
        closing = {
            "from": f"n{count:05}",
            "to": "n00000",
            "kind": "prerequisite",
            "reason": "Cycle-closing source",
        }
        relations.extend([closing, closing])
        with patch.object(
            semantics,
            "acyclic",
            side_effect=AssertionError("Do not recheck the entire graph per proposal"),
        ):
            accepted, conflicts = semantics.prerequisite_review(relations, [])
        self.assertEqual(len(accepted), count * 3)
        self.assertEqual(len(conflicts), 2)
        self.assertEqual(
            {edge["origin"]["relation"] for edge in accepted + conflicts},
            set(range(len(relations))),
        )
        semantics.acyclic([(edge["from"], edge["to"]) for edge in accepted])
        self.assertEqual(
            {(edge["from"], edge["to"]) for edge in conflicts},
            {(f"n{count:05}", "n00000")},
        )

    def test_incremental_cycle_decisions_match_original_order_and_duplicate_provenance(
        self,
    ):
        for seed in range(5):
            generator = random.Random(seed)
            relations = [
                {
                    "from": str(generator.randrange(8)),
                    "to": str(generator.randrange(8)),
                    "kind": "prerequisite",
                    "reason": "Source-backed proposal",
                }
                for _ in range(30)
            ]
            relations.extend([dict(relations[0]), dict(relations[1])])
            proposals = [{**edge, "origin": {"relation": index}} for index, edge in enumerate(relations)]
            accepted, conflicts, edges = [], [], []
            for proposal in sorted(proposals, key=semantics.encoded):
                candidate = (proposal["from"], proposal["to"])
                try:
                    semantics.acyclic([*edges, candidate])
                except ValueError:
                    conflicts.append(
                        {
                            **proposal,
                            "disposition": "Unresolved prerequisite cycle; proposal retained, not used for ordering",
                        }
                    )
                else:
                    accepted.append(proposal)
                    edges.append(candidate)
            with self.subTest(seed=seed):
                self.assertEqual(semantics.prerequisite_review(relations, []), (accepted, conflicts))


if __name__ == "__main__":
    unittest.main()
