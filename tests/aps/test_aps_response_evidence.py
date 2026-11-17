# this_file: tests/aps/test_aps_response_evidence.py
"""Rejected completion evidence survives validation, retries and interruptions."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from twat_text.aps import books as books
from twat_text.aps import semantics as semantics
from twat_text.aps import transport as transport
from test_aps_transport import endpoint


from quality_fixtures import review_fixture


class ResponseEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def book_db(self, original="Target."):
        db = books.connect(self.root / "books")
        source = self.root / "source" / "book"
        source.mkdir(parents=True)
        (source / "1.md").write_text(original)
        books.prepare(db, [self.root / "source"], "prompt", 3000, list, lambda text, limit: [text])
        return db

    def test_rejected_raw_and_controlled_reason_survive_reopen_and_never_publish(self):
        cases = [
            ("Target.", "", "Empty response"),
            (
                "[Target](https://example.com)",
                "Target without the original link.",
                "Markdown links changed",
            ),
            ("First.\n\nSecond.", "First. Second.", "Paragraph boundaries changed"),
            (
                "A sufficiently long original sentence.",
                "x",
                "Suspiciously short response",
            ),
        ]
        for original, raw, reason in cases:
            with self.subTest(reason=reason):
                work = self.root / reason
                db = books.connect(work)
                source = work / "source" / "book"
                source.mkdir(parents=True)
                (source / "1.md").write_text(original)
                books.prepare(
                    db,
                    [work / "source"],
                    "prompt",
                    3000,
                    list,
                    lambda text, limit: [text],
                )
                complete = Mock(return_value=transport.Completion(raw, {"id": "response-1"}))
                books.run(db, ["a", "b"], "prompt", max_attempts=1, complete=complete)
                db.close()
                db = books.connect(work)
                try:
                    row = db.execute("SELECT * FROM attempt_responses").fetchone()
                    self.assertEqual((row["raw"], row["validation_reason"]), (raw, reason))
                    self.assertIsNone(db.execute("SELECT response FROM chunks").fetchone()[0])
                    books.run(db, ["a", "b"], "prompt", max_attempts=1, complete=complete)
                    complete.assert_called_once()
                    review_fixture(db)
                    books.publish(db, work / "output")
                    self.assertFalse((work / "output/book.md").exists())
                finally:
                    db.close()

    def test_retry_preserves_each_raw_response_and_only_publishes_accepted_text(self):
        db = self.book_db("First.\n\nSecond.")
        self.addCleanup(db.close)
        responses = ["First. Second.", "  First.\n\nSecond.  "]
        books.run(db, ["a", "b"], "prompt", complete=Mock(side_effect=responses))
        rows = db.execute("SELECT raw,validation_reason FROM attempt_responses ORDER BY attempt_id").fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [(responses[0], "Paragraph boundaries changed"), (responses[1], None)],
        )
        review_fixture(db)
        books.publish(db, self.root / "output")
        self.assertEqual((self.root / "output/book.md").read_text(), "First.\n\nSecond.")

    def test_semantic_json_and_stage_failures_retain_precise_reasons(self):
        db = semantics.connect(self.root / "semantic")
        complete = Mock(side_effect=["not JSON", '{"ok":true}'])
        self.assertEqual(
            semantics.job(
                db,
                "fixture",
                {},
                "prompt",
                ["a", "b"],
                lambda result, payload: result,
                complete,
            ),
            {"ok": True},
        )
        with self.assertRaisesRegex(ValueError, "Unaccepted"):
            semantics.job(
                db,
                "hierarchy",
                {"children": [{"id": "a"}]},
                "prompt",
                ["a", "b"],
                semantics.group_valid,
                Mock(side_effect=["{}", "[]", "null"]),
            )
        db.close()
        db = semantics.connect(self.root / "semantic")
        try:
            rows = [
                tuple(row)
                for row in db.execute("SELECT raw,validation_reason FROM attempt_responses ORDER BY attempt_id")
            ]
            self.assertEqual(
                rows,
                [
                    ("not JSON", "Invalid JSON at line 1, column 1"),
                    ('{"ok":true}', None),
                    ("{}", "Invalid hierarchy"),
                    ("[]", "Invalid hierarchy"),
                    ("null", "Invalid hierarchy"),
                ],
            )
            self.assertEqual(db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 0)
        finally:
            db.close()

    def test_partial_and_uncertain_finish_retain_text_with_distinct_retry_semantics(
        self,
    ):
        for finish in ("length", "unrecognized", None):
            for runner in ("books", "semantics"):
                with self.subTest(finish=finish, runner=runner):
                    work = self.root / (runner + str(finish))
                    db = books.connect(work) if runner == "books" else semantics.connect(work)
                    raw = "Partial response text"
                    choice = {"message": {"content": raw}}
                    if finish is not None:
                        choice["finish_reason"] = finish
                    body = json.dumps({"id": "partial-1", "choices": [choice]})
                    try:
                        with endpoint(body=body) as requests:
                            if runner == "books":
                                with db:
                                    db.execute(
                                        "INSERT INTO chunks(book,ordinal,source,original,state) VALUES('book',0,'source','Target.','pending')"
                                    )
                                books.run(db, ["a", "b"], "prompt", max_attempts=1)
                                state = db.execute("SELECT state FROM chunks").fetchone()[0]
                            else:
                                with self.assertRaisesRegex(ValueError, "Unaccepted"):
                                    semantics.job(
                                        db,
                                        "fixture",
                                        {},
                                        "prompt",
                                        ["a", "b"],
                                        Mock(),
                                        semantics.infer,
                                    )
                                state = db.execute("SELECT state FROM jobs").fetchone()[0]
                            expected = 3 if finish == "length" and runner == "semantics" else 1
                            self.assertEqual(len(requests), expected)
                        rows = db.execute("SELECT raw,validation_reason FROM attempt_responses").fetchall()
                        self.assertEqual(len(rows), expected)
                        self.assertTrue(all(row["raw"] == raw for row in rows))
                        self.assertTrue(
                            all(
                                row["validation_reason"]
                                == ("Incomplete provider completion: length" if finish == "length" else None)
                                for row in rows
                            )
                        )
                        self.assertEqual(
                            state,
                            ("pending" if runner == "books" else "failed") if finish == "length" else "unknown",
                        )
                        metadata = json.loads(db.execute("SELECT metadata FROM attempt_metadata").fetchone()[0])
                        self.assertEqual(metadata["finish_reason"], finish)
                    finally:
                        db.close()

    def test_book_interruption_after_raw_commit_survives_reopen_without_resubmission(
        self,
    ):
        db = self.book_db()
        complete = Mock(return_value=transport.Completion("Target.", {"id": "retained"}))
        with (
            patch.object(books, "validate", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            books.run(db, ["a", "b"], "prompt", complete=complete)
        db.close()
        db = books.connect(self.root / "books")
        try:
            books.run(db, ["a", "b"], "prompt", complete=complete)
            complete.assert_called_once()
            self.assertEqual(db.execute("SELECT raw FROM attempt_responses").fetchone()[0], "Target.")
            self.assertEqual(db.execute("SELECT state FROM chunks").fetchone()[0], "unknown")
            self.assertEqual(
                db.execute("SELECT outcome FROM attempts").fetchone()[0],
                "interrupted-unknown",
            )
            review_fixture(db)
            books.publish(db, self.root / "output")
            self.assertFalse((self.root / "output/book.md").exists())
        finally:
            db.close()

    def test_semantic_interruption_after_raw_commit_survives_reopen_without_resubmission(
        self,
    ):
        db = semantics.connect(self.root / "semantic")
        complete = Mock(return_value='{"ok":true}')
        with self.assertRaises(KeyboardInterrupt):
            semantics.job(
                db,
                "fixture",
                {},
                "prompt",
                ["a", "b"],
                Mock(side_effect=KeyboardInterrupt),
                complete,
            )
        db.close()
        db = semantics.connect(self.root / "semantic")
        try:
            with self.assertRaisesRegex(ValueError, "Unresolved"):
                semantics.build(db, "fixture", {}, [], ["a", "b"], complete)
            complete.assert_called_once()
            self.assertEqual(
                db.execute("SELECT raw FROM attempt_responses").fetchone()[0],
                '{"ok":true}',
            )
            self.assertEqual(db.execute("SELECT state FROM jobs").fetchone()[0], "unknown")
            self.assertEqual(db.execute("SELECT state FROM attempts").fetchone()[0], "unknown")
        finally:
            db.close()

    def test_provider_exception_message_is_not_a_controlled_validation_reason(self):
        db = self.book_db()
        self.addCleanup(db.close)
        books.run(
            db,
            ["a", "b"],
            "prompt",
            complete=Mock(side_effect=TimeoutError("private-secret")),
        )
        self.assertEqual(db.execute("SELECT state FROM chunks").fetchone()[0], "unknown")
        self.assertNotIn("private-secret", "\n".join(db.iterdump()))
        self.assertEqual(db.execute("SELECT COUNT(*) FROM attempt_responses").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
