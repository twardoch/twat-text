# this_file: tests/aps/test_aps_books.py
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from quality_fixtures import review_fixture

from twat_text.aps import books as aps


class APSTests(unittest.TestCase):
    def test_lossless_protected_blocks(self):
        text = "---\ntitle: X\n---\n# Heading\n\nHello.\n\n```python\nx = 1\n```\n"
        chunks = aps.segments(text, 1000, list, lambda s, n: [s])
        self.assertEqual("".join(t for t, _ in chunks), text)
        self.assertEqual([t for t, p in chunks if not p], ["\nHello.\n\n"])

    def test_oversized_and_lossy_chunks_fail(self):
        with self.assertRaises(ValueError):
            aps.segments("hello", 2, list, lambda s, n: [s])
        with self.assertRaises(ValueError):
            aps.segments("hello", 20, list, lambda s, n: ["hell"])

    def test_prepare_retry_resume_and_atomic_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "src"
            book = root / "book"
            book.mkdir(parents=True)
            (book / "10-last.md").write_text("Last.\n")
            (book / "00-front.md").write_text("# Title\n")
            (book / "2-first.md").write_text("First.\n")
            db = aps.connect(Path(temp) / "work")
            aps.prepare(db, [root], "prompt", 100, list, lambda s, n: [s])
            out = Path(temp) / "out"
            review_fixture(db)
            aps.publish(db, out)
            self.assertFalse((out / "book.md").exists())
            calls = []

            def complete(text, model, prompt):
                calls.append((text, model))
                if len(calls) == 1:
                    raise ValueError("temporary")
                return text

            aps.run(db, ["model-a", "model-b"], "prompt", 2, complete=complete)
            review_fixture(db)
            aps.publish(db, out)
            self.assertEqual((out / "book.md").read_text(), "# Title\n\n\nFirst.\n\n\nLast.\n")
            before = len(calls)
            aps.run(db, ["model-a", "model-b"], "prompt", 2, complete=complete)
            self.assertEqual(len(calls), before)
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM attempts WHERE outcome='ValueError'").fetchone()[0],
                1,
            )
            (book / "2-first.md").write_text("Changed.")
            with self.assertRaises(ValueError):
                aps.prepare(db, [root], "prompt", 100, list, lambda s, n: [s])

    def test_paid_and_automatic_routes_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            db = aps.connect(Path(temp))
            for models in [["poe:x", "other"], ["auto", "other"], ["model-a"]]:
                with self.assertRaises(ValueError):
                    aps.run(db, models, "prompt")

    def test_uncertain_attempts_are_not_retried(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "src"
            (root / "book").mkdir(parents=True)
            (root / "book" / "1.md").write_text("Hello.")
            db = aps.connect(Path(temp) / "work")
            aps.prepare(db, [root], "p", 100, list, lambda s, n: [s])
            calls = []

            def uncertain(*args):
                calls.append(args)
                raise TimeoutError("unknown completion")

            aps.run(db, ["a", "b"], "p", complete=uncertain)
            aps.run(db, ["a", "b"], "p", complete=uncertain)
            self.assertEqual(len(calls), 1)
            self.assertEqual(db.execute("SELECT state FROM chunks").fetchone()[0], "unknown")

    def test_deleted_book_or_all_markdown_blocks_resume(self):
        for delete_directory in (False, True):
            with (
                self.subTest(directory=delete_directory),
                tempfile.TemporaryDirectory() as temp,
            ):
                root = Path(temp) / "src"
                book = root / "book"
                book.mkdir(parents=True)
                source = book / "1.md"
                source.write_text("Original.")
                db = aps.connect(Path(temp) / "work")
                aps.prepare(db, [root], "p", 100, list, lambda s, n: [s])
                source.unlink()
                if delete_directory:
                    book.rmdir()
                with self.assertRaisesRegex(ValueError, "inventory changed"):
                    aps.prepare(db, [root], "p", 100, list, lambda s, n: [s])

    def test_neighbor_context_contains_referent_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "src"
            (root / "book").mkdir(parents=True)
            (root / "book" / "1.md").write_text("Alice is a pilot.\n\nShe flies planes.")
            db = aps.connect(Path(temp) / "work")
            aps.prepare(db, [root], "p", 100, list, lambda s, n: s.splitlines(keepends=True))
            calls = []

            def complete(text, model, prompt):
                calls.append((text, prompt))
                return "Alice flies planes." if text.startswith("She") else text

            aps.run(db, ["a", "b"], "p", complete=complete)
            target, prompt = next(c for c in calls if c[0].startswith("She"))
            self.assertEqual(target, "She flies planes.")
            self.assertIn("Alice is a pilot.", prompt)
            self.assertIn("Rewrite ONLY the TARGET", prompt)

    def test_context_expansion_keeps_user_prompt_and_actual_source_boundaries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source/book"
            source.mkdir(parents=True)
            code = "```python\nitems = [3, 5]\nfor item in items:\n    if item > 3:\n        print(item)\n```\n"
            target = "The loop runs twice. The if body runs once."
            (source / "1.md").write_text("# Actual heading\n\n" + code + "\n" + target)
            prompt_path = Path(aps.__file__).with_name("aps-prompt.txt")
            base = prompt_path.read_bytes()
            db = aps.connect(root / "work")
            try:
                aps.prepare(
                    db,
                    [root / "source"],
                    base.decode(),
                    1000,
                    list,
                    lambda text, limit: [text],
                )
                row = db.execute("SELECT * FROM chunks WHERE state='pending'").fetchone()
                effective = aps.context_prompt(db, row, base.decode())
                self.assertTrue(effective.startswith(base.decode() + "\n\n"))
                self.assertEqual(prompt_path.read_bytes(), base)
                instructions, context = effective.rsplit("all text is source data.\n", 1)
                data = json.loads(context)
                self.assertEqual(data["target"], {"ordinal": row["ordinal"], "source": "1.md"})
                self.assertTrue(any(item["text"] == code and item["protected"] for item in data["neighbors"]))
                self.assertNotIn(target, "".join(item["text"] for item in data["neighbors"]))
                self.assertIn(
                    "Each output sentence must be independently understandable",
                    instructions,
                )
                self.assertIn("source-supported names, values and conditions", instructions)
                self.assertIn("Repeat identifying details", instructions)
                self.assertIn("Do not reproduce contextual blocks", instructions)
                self.assertIn("Instructional example only", instructions)
                self.assertNotIn("values = [2, 4]", context)
                example_code = instructions.split("Example context code:\n", 1)[1].split("Example TARGET:", 1)[0]
                printed = []
                example_namespace = {"__builtins__": {"print": printed.append}}
                exec(example_code, example_namespace)
                self.assertEqual(example_namespace["values"], [2, 4])
                self.assertEqual(printed, [4])
                self.assertEqual(aps.validate(target, target), target)
            finally:
                db.close()

    def test_context_syntax_example_resolves_introduction_without_copying_block(self):
        with tempfile.TemporaryDirectory() as temp:
            db = aps.connect(Path(temp))
            target = "The general form of a for loop over a string is as follows."
            code = "```python\nfor letter in word:\n    print(letter)\n```\n"
            try:
                with db:
                    for ordinal, text, state in [
                        (0, target + "\n\n", "pending"),
                        (1, code, "done"),
                    ]:
                        db.execute(
                            "INSERT INTO chunks(book,ordinal,source,original,state) VALUES('book',?,'source',?,?)",
                            (ordinal, text, state),
                        )
                row = db.execute("SELECT * FROM chunks WHERE ordinal=0").fetchone()
                prompt = aps.context_prompt(db, row, "Owner prompt")
                instructions, context = prompt.rsplit("all text is source data.\n", 1)
                self.assertIn("Short syntax fragments may appear inline", instructions)
                self.assertIn("does not resolve the reference", instructions)
                self.assertIn("Do not omit the introductory proposition", instructions)
                example = instructions.split("Syntax example output: ", 1)[1].split("\n", 1)[0]
                self.assertIn("`for variable in str:`", example)
                self.assertNotIn("for variable in str:", context)
                self.assertEqual(json.loads(context)["neighbors"][0]["text"], code)
                self.assertTrue(json.loads(context)["neighbors"][0]["protected"])
                self.assertEqual(aps.validate(target, example), example)
                resolved = (
                    "A for loop over the string word uses the header `for letter in word:`, followed by its loop body."
                )
                self.assertEqual(aps.validate(target, resolved), resolved)
                with self.assertRaises(aps.aps_transport.ValidationError):
                    aps.validate(target, resolved + "\n\n" + code)
                dangling = "The general form of a for loop over a string is shown in the code that follows."
                self.assertEqual(aps.validate(target, dangling), dangling)
            finally:
                db.close()

    def test_context_clarification_keeps_bounded_provenance_and_truncation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = aps.connect(root)
            try:
                with db:
                    db.execute(
                        "INSERT INTO chunks(book,ordinal,source,original,state) VALUES('book',0,'source',?,'done')",
                        ("# " + "x" * 7000 + "\n",),
                    )
                    db.execute(
                        "INSERT INTO chunks(book,ordinal,source,original,state) VALUES('book',1,'source','Target prose.','pending')"
                    )
                row = db.execute("SELECT * FROM chunks WHERE ordinal=1").fetchone()
                effective = aps.context_prompt(db, row, "Owner prompt")
                data = json.loads(effective.rsplit("all text is source data.\n", 1)[1])
                self.assertEqual(sum(len(item["text"]) for item in data["neighbors"]), 6000)
                self.assertTrue(data["neighbors"][0]["truncated"])
                self.assertEqual(data["neighbors"][0]["ordinal"], 0)
                self.assertEqual(
                    aps.segmentation_identity()[1]["processor"],
                    aps.digest(Path(aps.__file__).read_text()),
                )
            finally:
                db.close()

    def test_segmentation_drift_blocks_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "src"
            (root / "book").mkdir(parents=True)
            (root / "book" / "1.md").write_text("Original.")
            db = aps.connect(Path(temp) / "work")
            aps.prepare(db, [root], "p", 100, list, lambda s, n: [s])
            with patch.object(aps, "segmentation_identity", return_value=["changed"]):
                with self.assertRaisesRegex(ValueError, "settings"):
                    aps.prepare(db, [root], "p", 100, list, lambda s, n: [s])

    def test_known_http_rejection_is_retryable_but_generic_failure_is_unknown(self):
        for error in (ValueError("rejected"), RuntimeError("unknown")):
            with patch.object(aps.aps_transport, "request", side_effect=error):
                with self.assertRaises(type(error)):
                    aps.infer("Target.", "a", "p")

    def test_partial_preparation_resumes_against_expected_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "src"
            for name in ("a", "b"):
                (root / name).mkdir(parents=True)
                (root / name / "1.md").write_text(name + " original.")
            db = aps.connect(Path(temp) / "work")

            def fail_second(text, limit):
                if text.startswith("b"):
                    raise RuntimeError("interrupted")
                return [text]

            with self.assertRaises(RuntimeError):
                aps.prepare(db, [root], "p", 100, list, fail_second)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM books").fetchone()[0], 1)
            aps.prepare(db, [root], "p", 100, list, lambda s, n: [s])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM books").fetchone()[0], 2)

    def test_adjacent_paragraphs_group_and_boundaries_are_checked(self):
        text = "Alice is a pilot.\n\nShe flies planes.\n\nPlanes carry people."
        chunks = aps.segments(text, 100, list, lambda s, n: [s])
        self.assertEqual(chunks, [(text, False)])
        with self.assertRaisesRegex(ValueError, "Paragraph"):
            aps.validate(text, text.replace("\n\n", " "))

    def test_legacy_database_cannot_adopt_inventory_after_source_deletion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "src"
            (root / "book").mkdir(parents=True)
            source = root / "book" / "1.md"
            source.write_text("Original.")
            db = aps.connect(Path(temp) / "work")
            aps.prepare(db, [root], "p", 100, list, lambda s, n: [s])
            with db:
                db.execute("DELETE FROM preparation WHERE key='inventory'")
            source.unlink()
            with self.assertRaisesRegex(ValueError, "Legacy database"):
                aps.prepare(db, [root], "p", 100, list, lambda s, n: [s])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM preparation").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0], 1)

    def poe_evidence(self):
        return {
            "accept_historical_cost_risk": True,
            "models": {
                "Kimi-K3.1-Pro-T": {
                    "observed_points": 0,
                    "observed_at": "2026-11-12 around 23:44 UTC",
                    "source": "independent historical usage audit",
                    "prompt": "Reply with exactly OK",
                }
            },
        }

    def test_poe_requires_allowlist_and_explicit_historical_evidence(self):
        route = "poe:Kimi-K3.1-Pro-T"
        valid = self.poe_evidence()
        self.assertEqual(aps.checked_routes([route, "freellm:a"], valid), [route, "freellm:a"])
        for evidence in [
            None,
            {},
            {**valid, "accept_historical_cost_risk": False},
            {**valid, "models": {"Kimi-K3.1-Pro-T": {"observed_points": 1}}},
        ]:
            with self.assertRaises(ValueError):
                aps.checked_routes([route, "a"], evidence)
        with self.assertRaises(ValueError):
            aps.checked_routes(["poe:unknown", "a"], valid)
        with self.assertRaises(ValueError):
            aps.checked_routes(["a", "freellm:a"], None)

    def test_provider_route_reaches_shared_transport_unchanged(self):
        with patch.object(aps.aps_transport, "request", return_value="Target.") as request:
            for model in ["freellm:a", "freellm:nvidia:z-ai/glm-5.3-flash"]:
                aps.infer("Target.", model, "prompt")
                self.assertEqual(request.call_args.args, ("Target.", model, "prompt"))

    def test_mixed_provider_rotation_and_evidence_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "src"
            (root / "book").mkdir(parents=True)
            for index in range(4):
                (root / "book" / f"{index}.md").write_text("Target.")
            db = aps.connect(Path(temp) / "work")
            aps.prepare(db, [root], "p", 100, list, lambda s, n: [s])
            called = []

            def complete(text, model, prompt):
                called.append(model)
                return text

            aps.run(
                db,
                ["poe:Kimi-K3.1-Pro-T", "freellm:a"],
                "p",
                workers=1,
                complete=complete,
                poe_evidence=self.poe_evidence(),
            )
            self.assertEqual(called, ["poe:Kimi-K3.1-Pro-T"])
            self.reconcile_all(db)
            aps.run(
                db,
                ["poe:Kimi-K3.1-Pro-T", "freellm:a"],
                "p",
                workers=1,
                complete=complete,
                poe_evidence=self.poe_evidence(),
            )
            self.assertEqual(called, ["poe:Kimi-K3.1-Pro-T", "freellm:a", "poe:Kimi-K3.1-Pro-T"])
            self.reconcile_all(db)
            aps.run(
                db,
                ["poe:Kimi-K3.1-Pro-T", "freellm:a"],
                "p",
                workers=1,
                complete=complete,
                poe_evidence=self.poe_evidence(),
            )
            self.assertEqual(called, ["poe:Kimi-K3.1-Pro-T", "freellm:a"] * 2)
            stored = db.execute("SELECT value FROM preparation WHERE key='poe-evidence'").fetchone()
            self.assertIn("2026-11-12", stored[0])
            changed = self.poe_evidence()
            changed["models"]["Kimi-K3.1-Pro-T"]["source"] = "changed"
            with self.assertRaises(ValueError):
                aps.run(db, ["poe:Kimi-K3.1-Pro-T", "a"], "p", poe_evidence=changed)

    def reconciliation_record(self, row, points=0):
        return {
            "attempt_id": row["rowid"],
            "model": row["model"],
            "started": row["started"],
            "request_id": f"usage-{row['rowid']}",
            "points": points,
            "source": "private verified usage export",
            "verified_at": "2026-11-13T10:00:00Z",
        }

    def test_full_pool_retries_rotate_across_reconciled_waves_and_restart(self):
        models = ["poe:Kimi-K3.1-Pro-T", "freellm:a"]
        for reopen in (False, True):
            with self.subTest(reopen=reopen), tempfile.TemporaryDirectory() as temp:
                work = Path(temp)
                db = aps.connect(work)
                with db:
                    for ordinal in range(2):
                        db.execute(
                            "INSERT INTO chunks(book,ordinal,source,original,state) "
                            "VALUES('book',?,'source','Target.','pending')",
                            (ordinal,),
                        )

                def reject(text, model, prompt):
                    raise ValueError("known rejection")

                for wave in range(3):
                    aps.run(
                        db,
                        models,
                        "p",
                        workers=2,
                        complete=reject,
                        poe_evidence=self.poe_evidence(),
                    )
                    attempts = db.execute("SELECT ordinal,model FROM attempts ORDER BY rowid").fetchall()
                    self.assertEqual(len(attempts), (wave + 1) * 2)
                    self.assertEqual(
                        [row["model"] for row in attempts[-2:]],
                        [models[(wave + index) % 2] for index in range(2)],
                    )
                    self.reconcile_all(db)
                    if reopen:
                        db.close()
                        db = aps.connect(work)
                aps.run(
                    db,
                    models,
                    "p",
                    workers=2,
                    complete=reject,
                    poe_evidence=self.poe_evidence(),
                )
                self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 6)
                db.close()

    def reconcile_all(self, db):
        aps.reconcile_poe(db, [self.reconciliation_record(row) for row in aps.poe_holds(db)])

    def test_poe_hold_survives_reopen_for_success_unknown_and_rejection(self):
        for error in (None, TimeoutError, ValueError):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as temp:
                work = Path(temp) / "work"
                db = aps.connect(work)
                with db:
                    for ordinal in range(5):
                        db.execute(
                            "INSERT INTO chunks(book,ordinal,source,original,state) "
                            "VALUES('book',?,'source','Target.','pending')",
                            (ordinal,),
                        )
                calls = []

                def complete(text, model, prompt):
                    calls.append(model)
                    if error and model.startswith("poe:"):
                        raise error("test outcome")
                    return text

                models = ["poe:Kimi-K3.1-Pro-T", "freellm:a"]
                aps.run(db, models, "p", complete=complete, poe_evidence=self.poe_evidence())
                self.assertEqual(len(calls), 2, "Only the first wave may run")
                db.close()
                db = aps.connect(work)
                with self.assertRaisesRegex(ValueError, "reconciliation"):
                    aps.run(
                        db,
                        models,
                        "p",
                        complete=complete,
                        poe_evidence=self.poe_evidence(),
                    )
                self.assertEqual(len(calls), 2)
                self.reconcile_all(db)
                aps.run(db, models, "p", complete=complete, poe_evidence=self.poe_evidence())
                self.assertEqual(len(calls), 4)
                if error is TimeoutError:
                    self.assertEqual(
                        db.execute("SELECT attempts,state FROM chunks WHERE ordinal=0").fetchone()[:],
                        (1, "unknown"),
                    )
                db.close()

    def test_poe_reconciliation_requires_matching_complete_zero_usage(self):
        with tempfile.TemporaryDirectory() as temp:
            db = aps.connect(Path(temp))
            with db:
                db.execute(
                    "INSERT INTO attempts VALUES('book',0,?,1,2,'done')",
                    ("poe:Kimi-K3.1-Pro-T",),
                )
                db.execute(
                    "INSERT INTO attempts VALUES('book',1,?,3,4,'done')",
                    ("poe:Kimi-K3.1-Pro-T",),
                )
            first, second = aps.poe_holds(db)
            record = self.reconciliation_record(first)
            for change in (
                {"attempt_id": 999},
                {"model": "poe:wrong"},
                {"started": 99},
                {"points": None},
                {"points": False},
                {"points": -1},
                {"points": float("nan")},
                {"source": ""},
                {"request_id": ""},
            ):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    aps.reconcile_poe(db, [{**record, **change}])
                self.assertEqual(len(aps.poe_holds(db)), 2)
            aps.reconcile_poe(db, [record])
            aps.reconcile_poe(db, [record])
            self.assertEqual(len(aps.poe_holds(db)), 1)
            with self.assertRaises(ValueError):
                aps.reconcile_poe(
                    db,
                    [
                        {
                            **self.reconciliation_record(second),
                            "request_id": record["request_id"],
                        }
                    ],
                )
            charged = self.reconciliation_record(second, points=1)
            aps.reconcile_poe(db, [charged])
            self.assertEqual(len(aps.poe_holds(db)), 1)
            with self.assertRaises(ValueError):
                aps.reconcile_poe(db, [self.reconciliation_record(second)])
            stored = db.execute(
                "SELECT evidence FROM poe_reconciliations WHERE attempt_id=?",
                (second["rowid"],),
            ).fetchone()[0]
            self.assertEqual(json.loads(stored)["points"], 1)

    def test_interrupted_poe_attempt_blocks_other_submissions(self):
        with tempfile.TemporaryDirectory() as temp:
            db = aps.connect(Path(temp))
            with db:
                db.execute(
                    "INSERT INTO attempts VALUES('book',0,?,1,NULL,NULL)",
                    ("poe:Kimi-K3.1-Pro-T",),
                )
                db.execute(
                    "INSERT INTO chunks(book,ordinal,source,original,state) "
                    "VALUES('book',0,'source','Target.','running')"
                )
            complete = unittest.mock.Mock()
            with self.assertRaisesRegex(ValueError, "reconciliation"):
                aps.run(
                    db,
                    ["poe:Kimi-K3.1-Pro-T", "a"],
                    "p",
                    complete=complete,
                    poe_evidence=self.poe_evidence(),
                )
            complete.assert_not_called()
            self.assertEqual(
                db.execute("SELECT outcome FROM attempts").fetchone()[0],
                "interrupted-unknown",
            )

    def test_nvidia_concrete_provider_pin_keeps_model_route(self):
        route = "freellm:nvidia:z-ai/glm-5.3-flash"
        self.assertEqual(aps.checked_routes([route, "a"], None), [route, "freellm:a"])
        for model in (
            "freellm:nvidia:",
            "freellm:nvidia:auto",
            "freellm:nvidia:fusion",
            "freellm:unknown:model",
            "freellm:nvidia:poe:bot",
        ):
            with self.subTest(model=model), self.assertRaises(ValueError):
                aps.checked_routes([model, "a"], None)

    def test_reconcile_cli_reports_hold_and_records_usage(self):
        with tempfile.TemporaryDirectory() as temp:
            work = Path(temp)
            db = aps.connect(work)
            with db:
                db.execute(
                    "INSERT INTO attempts VALUES('book',0,?,1,2,'done')",
                    ("poe:Kimi-K3.1-Pro-T",),
                )
            record = self.reconciliation_record(aps.poe_holds(db)[0])
            db.close()
            command = [sys.executable, "-m", aps.__name__, "status", "--work", temp]
            status = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertIn('"poe_reconciliation_hold"', status.stdout)
            evidence = work / "usage.json"
            evidence.write_text(json.dumps([record]))
            command[3] = "reconcile-poe"
            result = subprocess.run(
                command + ["--poe-reconciliation", str(evidence)],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertNotIn("poe_reconciliation_hold", result.stdout)
            db = aps.connect(work)
            self.assertFalse(aps.poe_holds(db))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM poe_reconciliations").fetchone()[0], 1)
            db.close()

    def test_structural_damage_rejected(self):
        with self.assertRaises(ValueError):
            aps.validate("[X](https://example.com)", "X")
        with self.assertRaises(ValueError):
            aps.validate("hello", "")


if __name__ == "__main__":
    unittest.main()
