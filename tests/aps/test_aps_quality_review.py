# this_file: tests/aps/test_aps_quality_review.py
"""Regression cases derived from the independent review probes; no live calls."""

import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import test_aps_quality as fixtures
from quality_fixtures import METADATA, passing, calibrate_fixture, review_fixture
from twat_text.aps import quality as q
from twat_text.aps import books as books
from twat_text.aps import semantics as semantics
from twat_text.aps import transport as transport


class ReviewFixTests(unittest.TestCase):
    def fixture(self):
        value = fixtures.QualityTests()
        value.setUp()
        self.addCleanup(value.doCleanups)
        return value

    def test_imported_error_incomplete_and_missing_terminal_metadata_never_accept(self):
        for status, finish, outcome in [
            (503, "stop", "unknown"),
            (200, "length", "rejected"),
            (200, None, "unknown"),
            (None, "stop", "unknown"),
        ]:
            with self.subTest(status=status, finish=finish):
                f = self.fixture()
                candidate = q.register(f.db, f.row)
                records = {}
                for payload in q.payloads(f.row, candidate):
                    payload = q.bound_payload(f.db, payload, f.routes[0])
                    records[q.sha([payload, f.routes[0], q.AUDIT])] = {
                        "raw": str(passing(payload, f.routes[0], q.AUDIT)),
                        "metadata": {
                            **METADATA,
                            "http_status": status,
                            "finish_reason": finish,
                        },
                    }
                self.assertEqual(list(f.review(q.recorded_completions(records)).values()), [outcome])
                receipt = f.db.execute("SELECT * FROM quality_requests").fetchone()
                self.assertEqual(receipt["state"], outcome)
                self.assertIsNotNone(receipt["raw"])
                self.assertEqual(
                    f.db.execute("SELECT count(*) FROM quality_selections").fetchone()[0],
                    0,
                )
                if outcome == "unknown":
                    with self.assertRaisesRegex(ValueError, "hold"):
                        books.publish(f.db, f.root / "out")
                else:
                    self.assertEqual(books.publish(f.db, f.root / "out"), [])
                with self.assertRaises(ValueError):
                    semantics.inspect_task1(f.root / "work/aps.sqlite3", f.root / "out")

    def test_unknown_or_byok_hold_survives_route_reordering_and_blocks_repair(self):
        for failure in ("timeout", "byok"):
            with self.subTest(failure=failure):
                f = self.fixture()
                calls = []

                def complete(payload, model, prompt):
                    calls.append(model)
                    if failure == "timeout":
                        raise TimeoutError("uncertain")
                    return transport.Completion(
                        passing(payload, model, prompt),
                        {**METADATA, "usage": {"cost": 0, "is_byok": True}},
                    )

                self.assertEqual(list(f.review(complete).values()), ["unknown"])
                f.routes.reverse()
                self.assertEqual(
                    list(f.review(lambda *args: self.fail("hold bypassed")).values()),
                    ["unknown"],
                )
                candidate = q.register(f.db, f.row)
                _, state = q.request(
                    f.db,
                    candidate,
                    "repair",
                    {"source": f.row["original"]},
                    "Repair",
                    f.routes[0],
                    lambda *args: self.fail("held repair submitted"),
                    q.encode,
                    q.INPUT_TOKENS,
                )
                self.assertEqual(state, "unknown")
                self.assertEqual(len(calls), 1)
                with self.assertRaisesRegex(ValueError, "hold"):
                    books.publish(f.db, f.root / "out")

    def test_policy_drift_requires_new_calibration_and_new_bound_receipts(self):
        f = self.fixture()
        f.review()
        original = dict(f.db.execute("SELECT * FROM quality_selections").fetchone())
        revised = {**q.policy(), "implementation": "different-policy"}
        calls = []

        def complete(*args):
            calls.append(1)
            return passing(*args)

        with patch.object(q, "policy", return_value=revised):
            self.assertEqual(list(f.review(complete).values()), ["calibration-needed"])
            self.assertEqual(calls, [])
            self.assertEqual(
                f.db.execute("SELECT policy FROM quality_selections").fetchone()[0],
                original["policy"],
            )
            with self.assertRaisesRegex(ValueError, "policy"):
                q.verify_selection(f.db, f.current())
            calibrate_fixture(f.db, f.routes)
            self.assertEqual(list(f.review(complete).values()), ["accepted"])
            selected = f.db.execute("SELECT * FROM quality_selections").fetchone()
            self.assertNotEqual(selected["receipts"], original["receipts"])
            self.assertEqual(len(calls), 3)
            for identity in json.loads(selected["receipts"]):
                receipt = f.db.execute("SELECT * FROM quality_requests WHERE id=?", (identity,)).fetchone()
                self.assertEqual(json.loads(receipt["policy"]), revised)
                self.assertEqual(
                    json.loads(receipt["payload"])["calibration"],
                    receipt["calibration"],
                )

    def test_invalid_segment_types_are_durable_rejections_not_poisoned_received(self):
        f = self.fixture()
        candidate = q.register(f.db, f.row)
        calls = []
        for index, invalid in enumerate(([], {}, None, 3)):
            payload = q.payloads(f.row, candidate)[0] | {"probe": index}

            def complete(p, model, prompt):
                calls.append(1)
                data = json.loads(passing(p, model, prompt))
                data["units"][0]["supports"][0]["segment"] = invalid
                return transport.Completion(json.dumps(data), METADATA)

            for _ in range(2):
                _, state = q.request(
                    f.db,
                    candidate,
                    "audit",
                    payload,
                    q.AUDIT,
                    f.routes[0],
                    complete,
                    q.encode,
                    q.INPUT_TOKENS,
                )
                self.assertEqual(state, "rejected")
        self.assertEqual(len(calls), 4)
        self.assertEqual(
            {r[0] for r in f.db.execute("SELECT state FROM quality_requests")},
            {"rejected"},
        )
        self.assertTrue(
            all(r[0] == "Invalid evidence quote" for r in f.db.execute("SELECT reason FROM quality_requests"))
        )

    def test_whitespace_evidence_and_missing_semantic_dimensions_fail_closed(self):
        f = self.fixture()
        candidate = q.register(f.db, f.row)
        payload = q.payloads(f.row, candidate)[0]
        for variant in (
            "blank-evidence",
            "no-checks",
            "no-propositions",
            "unmapped-proposition",
            "blank-proposition",
            "contradictory-check",
        ):
            data = json.loads(passing(payload, "model", q.AUDIT))
            unit = data["units"][0]
            if variant == "blank-evidence":
                unit["supports"][0]["quote"] = " "
            elif variant == "no-checks":
                del unit["checks"]
            elif variant == "no-propositions":
                del unit["propositions"]
            elif variant == "unmapped-proposition":
                unit["propositions"].append("Another claim")
            elif variant == "blank-proposition":
                unit["propositions"] = [" "]
            else:
                unit["checks"]["independence"] = "revise"
            with (
                self.subTest(variant=variant),
                self.assertRaises(transport.ValidationError),
            ):
                q.audit_result(json.dumps(data), payload)

    def test_context_flags_and_identity_are_visible_and_hash_bound(self):
        f = self.fixture()
        candidate = dict(q.register(f.db, f.row))
        binding = json.loads(candidate["binding"])
        binding["context"]["neighbors"][0]["truncated"] = True
        candidate["binding"] = q.encoded(binding)
        for payload in q.payloads(f.row, candidate):
            self.assertEqual(payload["context"], binding["context"])
            self.assertTrue(payload["context"]["neighbors"][0]["truncated"])
            self.assertTrue(payload["context"]["neighbors"][0]["protected"])
            self.assertEqual(payload["context"]["target"]["source"], "1.md")
            self.assertEqual(payload["binding"], q.sha(candidate["binding"]))

    def test_deleted_protected_source_cannot_publish_partial_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source/book"
            source.mkdir(parents=True)
            (source / "1.md").write_text("# First\n\nA fictional device weighs three grams.\n")
            (source / "2.md").write_text("# Second\n\n```python\nvalue = 1\n```\n")
            db = books.connect(root / "work")
            try:
                books.prepare(db, [root / "source"], "prompt", 3000, list, lambda s, n: [s])
                books.run(
                    db,
                    ["writer-a", "writer-b"],
                    "prompt",
                    complete=lambda text, *args: transport.Completion(text, METADATA),
                )
                review_fixture(db)
                with db:
                    db.execute("DELETE FROM chunks WHERE source=?", (str(source / "2.md"),))
                with self.assertRaisesRegex(ValueError, "inventory source"):
                    books.publish(db, root / "out")
                self.assertFalse((root / "out/book.md").exists())
                with self.assertRaisesRegex(ValueError, "inventory source"):
                    semantics.inspect_task1(root / "work/aps.sqlite3", root / "out")
            finally:
                db.close()

    def test_cli_pending_generation_is_not_empty_success(self):
        f = self.fixture()
        with f.db:
            f.db.execute("UPDATE chunks SET state='pending' WHERE attempts>0")
        records = f.root / "records.json"
        records.write_text("{}")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                q.__name__,
                "review-recorded",
                "--work",
                str(f.root / "work"),
                "--models",
                *f.routes,
                "--records",
                str(records),
                "--output",
                str(f.root / "out"),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        status = json.loads(result.stdout)["status"]
        self.assertFalse(status["complete"])
        self.assertIn("generation-pending", status["remaining"].values())
        self.assertFalse((f.root / "out/book.md").exists())

    def test_incomplete_billing_hold_survives_reorder_and_reopen(self):
        for usage in (
            {"cost": 7, "is_byok": False},
            {"cost": 0, "is_byok": True},
            {"cost": 7, "is_byok": True},
        ):
            with self.subTest(usage=usage):
                f = self.fixture()

                def incomplete(payload, route, prompt):
                    return transport.Completion(
                        passing(payload, route, prompt),
                        {**METADATA, "finish_reason": "length", "usage": usage},
                    )

                self.assertEqual(list(f.review(incomplete).values()), ["unknown"])
                self.assertTrue(q.holds(f.db))
                f.db.close()
                f.db = books.connect(f.root / "work")
                f.routes.reverse()
                self.assertEqual(
                    list(f.review(lambda *args: self.fail("billing hold bypassed")).values()),
                    ["unknown"],
                )
                with self.assertRaisesRegex(ValueError, "hold"):
                    books.publish(f.db, f.root / "out")

    def test_known_rejection_without_raw_keeps_independent_billing_hold(self):
        f = self.fixture()

        def rejected(*args):
            failure = transport.ValidationError("Known incomplete completion")
            failure.metadata = {
                **METADATA,
                "finish_reason": "length",
                "usage": {"cost": 7, "is_byok": True},
            }
            failure.raw = None
            raise failure

        self.assertEqual(list(f.review(rejected).values()), ["rejected"])
        row = f.db.execute("SELECT state,raw,metadata FROM quality_requests").fetchone()
        self.assertEqual(row["state"], "rejected")
        self.assertIsNone(row["raw"])
        self.assertEqual(json.loads(row["metadata"])["usage"]["cost"], 7)
        self.assertTrue(q.holds(f.db))
        f.routes.reverse()
        self.assertEqual(
            list(f.review(lambda *args: self.fail("held route reused")).values()),
            ["unknown"],
        )

    def test_incomplete_calibration_charge_holds_even_a_different_calibrated_route(
        self,
    ):
        f = self.fixture()
        records = json.loads(
            f.db.execute("SELECT records FROM quality_calibrations WHERE route=?", (f.routes[0],)).fetchone()[0]
        )
        first = next(iter(records.values()))
        first["metadata"] = {
            **METADATA,
            "finish_reason": "length",
            "usage": {"cost": 7, "is_byok": True},
        }
        _, result = q.persist_calibration(f.db, q.CALIBRATION, f.routes[0], records)
        self.assertFalse(result["all_matched"])
        self.assertTrue(any("calibration" in hold for hold in q.holds(f.db)))
        f.routes.reverse()
        self.assertEqual(
            list(f.review(lambda *args: self.fail("calibration billing hold bypassed")).values()),
            ["unknown"],
        )

    def test_input_budget_counts_final_bound_payload_before_claim_or_callback(self):
        f = self.fixture()
        candidate = q.register(f.db, f.row)
        payload = q.payloads(f.row, candidate)[0]
        unbound_size = len(q.encode(q.encoded(payload) + q.AUDIT))
        bound = q.bound_payload(f.db, payload, f.routes[0])
        bound_size = len(q.encode(q.encoded(bound) + q.AUDIT))
        self.assertGreater(bound_size, unbound_size)
        for limit in (unbound_size, bound_size - 1):
            _, state = q.request(
                f.db,
                candidate,
                "audit",
                payload,
                q.AUDIT,
                f.routes[0],
                lambda *args: self.fail("oversized payload submitted"),
                q.encode,
                limit,
            )
            self.assertEqual(state, "input-budget")
            self.assertEqual(f.db.execute("SELECT count(*) FROM quality_requests").fetchone()[0], 0)
        sent = []

        def complete(actual, route, prompt):
            sent.append(len(q.encode(q.encoded(actual) + prompt)))
            return passing(actual, route, prompt)

        _, state = q.request(
            f.db,
            candidate,
            "audit",
            payload,
            q.AUDIT,
            f.routes[0],
            complete,
            q.encode,
            bound_size,
        )
        self.assertEqual(state, "completed")
        self.assertEqual(sent, [bound_size])

    def test_immutable_calibration_cache_reduces_revalidation_without_stale_pass(self):
        f = self.fixture()
        q.verified_calibration_record.cache_clear()
        original = q.audit_result
        calls = []

        def counted(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        with patch.object(q, "audit_result", side_effect=counted):
            self.assertEqual(list(f.review().values()), ["accepted"])
        self.assertEqual(len(calls), 36)  # 30 calibration cases/windows + 3 audits + 3 selection checks.
        inputs = q.calibration_inputs(q.CALIBRATION, f.routes[0])
        records = {
            job["key"]: {
                "raw": str(passing(job["payload"], job["route"], job["prompt"])),
                "metadata": METADATA,
            }
            for job in inputs["jobs"]
        }
        _, result = q.persist_calibration(f.db, q.CALIBRATION, f.routes[0], records)
        self.assertFalse(result["all_matched"])
        self.assertIsNone(q.calibrated(f.db, f.routes[0]))
        with self.assertRaises(ValueError):
            q.verify_selection(f.db, f.current())

    def test_http200_incomplete_missing_usage_holds_after_reopen_and_route_change(self):
        for usage in ("omitted", None, [], {}):
            for raw_present in (True, False):
                with self.subTest(usage=usage, raw_present=raw_present):
                    f = self.fixture()
                    metadata = {**METADATA, "finish_reason": "length"}
                    if usage == "omitted":
                        metadata.pop("usage")
                    else:
                        metadata["usage"] = usage

                    def incomplete(payload, route, prompt):
                        if raw_present:
                            return transport.Completion(passing(payload, route, prompt), metadata)
                        error = transport.ValidationError("Known incomplete response")
                        error.raw = None
                        error.metadata = metadata
                        raise error

                    self.assertEqual(
                        list(f.review(incomplete).values()),
                        ["unknown" if raw_present else "rejected"],
                    )
                    self.assertTrue(q.holds(f.db))
                    stored = json.loads(f.db.execute("SELECT metadata FROM quality_requests").fetchone()[0])
                    self.assertEqual(stored, metadata)
                    f.db.close()
                    f.db = books.connect(f.root / "work")
                    f.routes.reverse()
                    self.assertEqual(
                        list(f.review(lambda *args: self.fail("missing billing evidence bypassed")).values()),
                        ["unknown"],
                    )
                    with self.assertRaisesRegex(ValueError, "hold"):
                        books.publish(f.db, f.root / "out")

    def test_calibration_http200_incomplete_missing_usage_blocks_other_routes(self):
        for usage, raw_present in ((None, True), ("omitted", False), ([], True)):
            with self.subTest(usage=usage, raw_present=raw_present):
                f = self.fixture()
                records = json.loads(
                    f.db.execute(
                        "SELECT records FROM quality_calibrations WHERE route=?",
                        (f.routes[0],),
                    ).fetchone()[0]
                )
                record = next(iter(records.values()))
                metadata = {**METADATA, "finish_reason": "length"}
                if usage == "omitted":
                    metadata.pop("usage")
                else:
                    metadata["usage"] = usage
                record["metadata"] = metadata
                if not raw_present:
                    record["raw"] = None
                _, report = q.persist_calibration(f.db, q.CALIBRATION, f.routes[0], records)
                self.assertFalse(report["all_matched"])
                self.assertTrue(any("calibration" in hold for hold in q.holds(f.db)))
                f.db.close()
                f.db = books.connect(f.root / "work")
                f.routes.reverse()
                self.assertEqual(
                    list(f.review(lambda *args: self.fail("calibration missing billing evidence bypassed")).values()),
                    ["unknown"],
                )

    def test_definite_rejections_without_inference_do_not_invent_billing_hold(self):
        for metadata in (None, {"http_status": 400, "usage": None}):
            with self.subTest(metadata=metadata):
                f = self.fixture()

                def rejected(*args):
                    error = ValueError("Definite request rejection")
                    error.raw = None
                    error.metadata = metadata
                    raise error

                self.assertEqual(list(f.review(rejected).values()), ["rejected"])
                self.assertEqual(q.holds(f.db), [])
                f.routes.reverse()
                self.assertEqual(list(f.review().values()), ["accepted"])

    def test_calibration_must_be_persisted_passing_and_bound_to_route(self):
        f = self.fixture()
        f.routes = ["freellm:uncalibrated-a", "freellm:uncalibrated-b"]
        self.assertEqual(
            list(f.review(lambda *args: self.fail("uncalibrated submission")).values()),
            ["calibration-needed"],
        )
        inputs = q.calibration_inputs(q.CALIBRATION, f.routes[0])
        records = {
            job["key"]: {
                "raw": str(passing(job["payload"], job["route"], job["prompt"])),
                "metadata": METADATA,
            }
            for job in inputs["jobs"]
        }
        identity, report = q.persist_calibration(f.db, q.CALIBRATION, f.routes[0], records)
        self.assertFalse(report["all_matched"])
        self.assertIsNone(q.calibrated(f.db, f.routes[0]))
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable calibration"):
            f.db.execute("UPDATE quality_calibrations SET result='{}' WHERE id=?", (identity,))
        calibrate_fixture(f.db, f.routes)
        self.assertEqual(list(f.review().values()), ["accepted"])
        for receipt in f.db.execute("SELECT * FROM quality_requests"):
            self.assertEqual(receipt["calibration"], q.calibrated(f.db, receipt["model"]))
            self.assertEqual(json.loads(receipt["policy"]), q.policy())
