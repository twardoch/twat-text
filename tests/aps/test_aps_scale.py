# this_file: tests/aps/test_aps_scale.py
"""Operation-scoped acceptance reuse, with mutation and concurrent-write regressions."""

import json
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from quality_fixtures import METADATA, passing, calibrate_fixture, review_fixture
from twat_text.aps import books as books
from twat_text.aps import quality as quality
from twat_text.aps import semantics as semantics
from twat_text.aps import transport as transport


def add_hold(db, identity="new-hold"):
    db.execute(
        "INSERT INTO quality_requests(id,candidate,stage,payload,prompt,model,state,started) VALUES(?,?,?,?,?,?,?,?)",
        (identity, "fiction", "audit", "{}", "prompt", "fixture", "unknown", 0),
    )


class ScaleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source" / "book" / "1.md"
        self.source.parent.mkdir(parents=True)
        self.text = "".join(f"# Item {i}\n\nFictional item {i} has a blue label.\n\n" for i in range(8))
        self.source.write_text(self.text)
        self.db = books.connect(self.root / "work")
        self.addCleanup(self.db.close)
        books.prepare(self.db, [self.root / "source"], "prompt", 3000, list, lambda t, n: [t])
        books.run(
            self.db,
            ["writer-a", "writer-b"],
            "prompt",
            complete=lambda t, m, p: transport.Completion(t, METADATA),
        )
        review_fixture(self.db)
        self.rows = self.db.execute("SELECT * FROM chunks WHERE attempts>0 ORDER BY ordinal").fetchall()
        self.out = self.root / "out"

    def test_publish_and_task2_share_one_hold_scan_and_source_annotation(self):
        for operation in (
            lambda: books.publish(self.db, self.out),
            lambda: semantics.inspect_task1(self.root / "work/aps.sqlite3", self.out),
        ):
            with (
                patch.object(quality, "holds", wraps=quality.holds) as holds,
                patch.object(books, "annotate_source", wraps=books.annotate_source) as annotate,
                patch.object(books, "markdown_source", wraps=books.markdown_source) as parse,
            ):
                operation()
                self.assertEqual(holds.call_count, 1)
                self.assertEqual(annotate.call_count, 1)
                self.assertEqual(parse.call_count, 3 + len(self.rows))
        self.assertEqual((self.out / "book.md").read_text(), self.text)

    def test_snapshot_expires_and_rejects_same_connection_data_changes(self):
        with quality.acceptance_snapshot(self.db) as snapshot:
            quality.verify_selection(self.db, self.rows[0], snapshot)
        with self.assertRaisesRegex(ValueError, "expired"):
            quality.verify_selection(self.db, self.rows[0], snapshot)
        with self.assertRaisesRegex(ValueError, "changed"):
            with quality.acceptance_snapshot(self.db) as snapshot:
                quality.verify_selection(self.db, self.rows[0], snapshot)
                self.db.execute(
                    "UPDATE chunks SET response='changed' WHERE book=? AND ordinal=?",
                    (self.rows[0]["book"], self.rows[0]["ordinal"]),
                )
                quality.verify_selection(self.db, self.rows[0], snapshot)
        with self.assertRaises(ValueError):
            quality.verify_selection(self.db, self.rows[0])

    def test_snapshot_rechecks_source_and_policy_at_operation_boundary(self):
        with self.assertRaisesRegex(ValueError, "source changed"):
            with quality.acceptance_snapshot(self.db) as snapshot:
                quality.verify_selection(self.db, self.rows[0], snapshot)
                self.source.write_text(self.text + "Changed source.")
        with self.assertRaisesRegex(ValueError, "source hash mismatch"):
            books.publish(self.db, self.out)
        self.source.write_text(self.text)
        current = quality.policy()
        with quality.acceptance_snapshot(self.db) as snapshot:
            with patch.object(quality, "policy", return_value={**current, "version": "changed"}):
                with self.assertRaisesRegex(ValueError, "policy changed"):
                    snapshot.boundary()
        self.assertEqual(books.publish(self.db, self.out), ["book"])

    def test_publication_does_not_commit_or_publish_an_open_caller_transaction(self):
        self.db.execute("INSERT INTO preparation VALUES('owner-note','pending')")
        with self.assertRaisesRegex(ValueError, "caller transaction"):
            books.publish(self.db, self.out)
        self.assertTrue(self.db.in_transaction)
        self.assertFalse(self.out.exists())
        self.db.rollback()
        self.assertIsNone(self.db.execute("SELECT value FROM preparation WHERE key='owner-note'").fetchone())

    def test_readonly_task2_rejects_concurrent_receipt_commit(self):
        books.publish(self.db, self.out)
        original = quality.verify_selection
        inserted = []

        def verify(db, row, snapshot=None):
            value = original(db, row, snapshot)
            if not inserted:
                with (
                    closing(sqlite3.connect(self.root / "work/aps.sqlite3")) as writer,
                    writer,
                ):
                    add_hold(writer)
                inserted.append(True)
            return value

        with patch.object(quality, "verify_selection", side_effect=verify):
            with self.assertRaisesRegex(ValueError, "changed externally"):
                semantics.inspect_task1(self.root / "work/aps.sqlite3", self.out)
        self.assertTrue(inserted)
        with self.assertRaisesRegex(ValueError, "Unreconciled"):
            books.publish(self.db, self.out)

    def test_publication_reserves_writer_lock_through_replace_and_next_operation_is_fresh(
        self,
    ):
        original = Path.replace
        outcomes = []

        def replace(path, target):
            if path.suffix == ".new":

                def writer():
                    with (
                        closing(sqlite3.connect(self.root / "work/aps.sqlite3", timeout=0)) as db,
                        db,
                    ):
                        try:
                            add_hold(db)
                            outcomes.append("committed")
                        except sqlite3.OperationalError as error:
                            outcomes.append(str(error))

                thread = threading.Thread(target=writer)
                thread.start()
                thread.join(3)
                self.assertFalse(thread.is_alive())
            return original(path, target)

        with patch.object(Path, "replace", replace):
            self.assertEqual(books.publish(self.db, self.out), ["book"])
        self.assertEqual(outcomes, ["database is locked"])
        with closing(sqlite3.connect(self.root / "work/aps.sqlite3")) as writer, writer:
            add_hold(writer)
        with self.assertRaisesRegex(ValueError, "Unreconciled"):
            books.publish(self.db, self.out)
        self.assertEqual((self.out / "book.md").read_text(), self.text)

    def test_source_change_during_replace_preserves_backup_and_reports_occupied_output(
        self,
    ):
        original = Path.replace
        for existing in (False, True):
            with self.subTest(existing=existing):
                output = self.root / str(existing)
                output.mkdir()
                target = output / "book.md"
                if existing:
                    target.write_text("Prior owner artifact.")

                def replace(path, destination):
                    result = original(path, destination)
                    if path.suffix == ".new":
                        self.source.write_text(self.text + "Changed during replacement.")
                    return result

                with patch.object(Path, "replace", replace):
                    with self.assertRaisesRegex(RuntimeError, "rollback incomplete") as caught:
                        books.publish(self.db, output)
                self.assertRegex(str(caught.exception.__cause__), "source changed")
                self.assertEqual(target.read_text(), self.text)
                (staging,) = output.glob(".aps-publish-*")
                if existing:
                    self.assertEqual((staging / "0.old").read_text(), "Prior owner artifact.")
                self.assertIn("naccepted output may remain", "\n".join(caught.exception.__notes__))
                self.source.write_text(self.text)

    def test_absent_destination_recovers_prior_artifact_without_overwrite(self):
        original_replace = Path.replace
        for prior in (False, True):
            with self.subTest(prior=prior):
                output = self.root / str(prior)
                output.mkdir()
                target = output / "book.md"
                if prior:
                    target.write_text("Prior owner artifact.")

                def replace(path, destination):
                    result = original_replace(path, destination)
                    if path.suffix == ".new":
                        target.unlink()  # An owner removed the published file.
                        self.source.write_text("Changed source.")
                    return result

                with patch.object(Path, "replace", replace):
                    with self.assertRaisesRegex(ValueError, "source changed"):
                        books.publish(self.db, output)
                if prior:
                    self.assertEqual(target.read_text(), "Prior owner artifact.")
                else:
                    self.assertFalse(target.exists())
                self.assertEqual(list(output.glob(".aps-publish-*")), [])
                self.source.write_text(self.text)

    def test_source_alias_retargeted_during_replace_cannot_use_cached_source(self):
        alias = self.root / "alias"
        alias.symlink_to(self.source.parent, target_is_directory=True)
        alternate = self.root / "alternate"
        alternate.mkdir()
        (alternate / "1.md").write_text("Different source.")
        with self.db:
            self.db.execute("UPDATE chunks SET source=? WHERE source!=''", (str(alias / "1.md"),))
        original = Path.replace

        def replace(path, target):
            result = original(path, target)
            if path.suffix == ".new":
                alias.unlink()
                alias.symlink_to(alternate, target_is_directory=True)
            return result

        with patch.object(Path, "replace", replace):
            with self.assertRaisesRegex(RuntimeError, "rollback incomplete") as caught:
                books.publish(self.db, self.out)
        self.assertRegex(str(caught.exception.__cause__), "source path changed")
        self.assertEqual((self.out / "book.md").read_text(), self.text)

    def test_same_connection_hold_during_replace_reports_output_and_persists_hold(
        self,
    ):
        original = Path.replace

        def replace(path, target):
            result = original(path, target)
            if path.suffix == ".new":
                add_hold(self.db)
            return result

        with patch.object(Path, "replace", replace):
            with self.assertRaisesRegex(RuntimeError, "rollback incomplete") as caught:
                books.publish(self.db, self.out)
        self.assertRegex(str(caught.exception.__cause__), "snapshot changed")
        self.assertEqual((self.out / "book.md").read_text(), self.text)
        self.assertTrue(quality.holds(self.db))
        with self.assertRaisesRegex(ValueError, "Unreconciled"):
            books.publish(self.db, self.out)

    def test_final_source_read_hold_is_rejected_before_snapshot_finishes(self):
        original_read, original_finish = (
            Path.read_bytes,
            quality.AcceptanceSnapshot.finish,
        )
        final_read = []

        def finish(snapshot):
            final_read.append(True)
            return original_finish(snapshot)

        def read(path):
            content = original_read(path)
            if final_read and path.name == "1.md":
                final_read.clear()
                add_hold(self.db)
            return content

        self.out.mkdir()
        target = self.out / "book.md"
        target.write_text("Prior owner artifact.")
        with (
            patch.object(quality.AcceptanceSnapshot, "finish", finish),
            patch.object(Path, "read_bytes", read),
        ):
            with self.assertRaisesRegex(RuntimeError, "rollback incomplete") as caught:
                books.publish(self.db, self.out)
        self.assertRegex(str(caught.exception.__cause__), "snapshot changed")
        (staging,) = self.out.glob(".aps-publish-*")
        self.assertEqual((staging / "0.old").read_text(), "Prior owner artifact.")
        self.assertTrue(quality.holds(self.db))

    def test_final_source_read_policy_drift_is_rejected(self):
        original_read, original_finish = (
            Path.read_bytes,
            quality.AcceptanceSnapshot.finish,
        )
        current = quality.policy()
        final_read, drift = [], []

        def finish(snapshot):
            final_read.append(True)
            return original_finish(snapshot)

        def read(path):
            content = original_read(path)
            if final_read and path.name == "1.md":
                drift.append(True)
            return content

        with (
            patch.object(quality.AcceptanceSnapshot, "finish", finish),
            patch.object(Path, "read_bytes", read),
            patch.object(
                quality,
                "policy",
                side_effect=lambda: {**current, "version": "changed"} if drift else current,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "rollback incomplete") as caught:
                books.publish(self.db, self.out)
        self.assertRegex(str(caught.exception.__cause__), "policy changed")
        self.assertEqual((self.out / "book.md").read_text(), self.text)

    def test_task2_final_source_read_external_hold_is_rejected(self):
        books.publish(self.db, self.out)
        original_read, original_boundary = (
            Path.read_bytes,
            quality.AcceptanceSnapshot.boundary,
        )
        final_read, inserted = [], []

        def boundary(snapshot, book=None):
            final_read.append(True)
            return original_boundary(snapshot, book)

        def read(path):
            content = original_read(path)
            if final_read and not inserted and path.name == "1.md":
                with (
                    closing(sqlite3.connect(self.root / "work/aps.sqlite3")) as writer,
                    writer,
                ):
                    add_hold(writer)
                inserted.append(True)
            return content

        with (
            patch.object(quality.AcceptanceSnapshot, "boundary", boundary),
            patch.object(Path, "read_bytes", read),
        ):
            with self.assertRaisesRegex(ValueError, "changed externally"):
                semantics.inspect_task1(self.root / "work/aps.sqlite3", self.out)
        self.assertTrue(inserted)
        self.assertTrue(quality.holds(self.db))

    def test_failed_restore_preserves_backup_and_attempts_remaining_restores(self):
        second = self.root / "source" / "book2" / "1.md"
        second.parent.mkdir()
        second.write_text("# Second book\n\nThe fictional label is green.\n")
        db = books.connect(self.root / "two-work")
        self.addCleanup(db.close)
        books.prepare(db, [self.root / "source"], "prompt", 3000, list, lambda t, n: [t])
        books.run(
            db,
            ["writer-a", "writer-b"],
            "prompt",
            complete=lambda t, m, p: transport.Completion(t, METADATA),
        )
        review_fixture(db)
        self.out.mkdir()
        for book in ("book", "book2"):
            (self.out / f"{book}.md").write_text(f"Prior {book} artifact.")
        original_replace, original_link = Path.replace, os.link
        restored = []

        def replace(path, target):
            result = original_replace(path, target)
            if path.suffix == ".new" and Path(target).name == "book2.md":
                second.write_text("Changed during replacement.")
                (self.out / "book.md").unlink()  # Simulate an owner removing one output.
            return result

        def link(source, target, **kwargs):
            if Path(source).suffix == ".old":
                restored.append(Path(target).name)
                if Path(target).name == "book2.md":
                    raise OSError("injected restore failure")
            return original_link(source, target, **kwargs)

        with patch.object(Path, "replace", replace), patch.object(os, "link", link):
            with self.assertRaisesRegex(RuntimeError, "rollback incomplete") as caught:
                books.publish(db, self.out)
        self.assertCountEqual(restored, ["book.md", "book2.md"])
        self.assertEqual((self.out / "book.md").read_text(), "Prior book artifact.")
        self.assertEqual(
            (self.out / "book2.md").read_text(),
            "# Second book\n\nThe fictional label is green.\n",
        )
        (staging,) = self.out.glob(".aps-publish-*")
        self.assertIn(str(staging), str(caught.exception))
        self.assertRegex(str(caught.exception.__cause__), "source changed")
        self.assertCountEqual(
            [p.read_text() for p in staging.glob("*.old")],
            ["Prior book artifact.", "Prior book2 artifact."],
        )
        self.assertFalse(list(staging.glob("*.rejected")))
        self.assertIn("injected restore failure", "\n".join(caught.exception.__notes__))

    def test_publication_writer_lock_remains_held_during_final_source_read(self):
        original_read, original_finish = (
            Path.read_bytes,
            quality.AcceptanceSnapshot.finish,
        )
        final_read, observed = [], []

        def finish(snapshot):
            final_read.append(True)
            return original_finish(snapshot)

        def read(path):
            content = original_read(path)
            if final_read and path.name == "1.md":
                with (
                    closing(sqlite3.connect(self.root / "work/aps.sqlite3", timeout=0)) as writer,
                    writer,
                ):
                    with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                        add_hold(writer)
                observed.append(True)
            return content

        with (
            patch.object(quality.AcceptanceSnapshot, "finish", finish),
            patch.object(Path, "read_bytes", read),
        ):
            self.assertEqual(books.publish(self.db, self.out), ["book"])
        self.assertEqual(observed, [True])
        self.assertFalse(quality.holds(self.db))

    def test_occupied_output_is_not_withdrawn_and_backup_is_preserved(self):
        self.out.mkdir()
        target = self.out / "book.md"
        target.write_text("Prior owner artifact.")
        original_replace = Path.replace

        def replace(path, destination):
            if path.suffix == ".old" or Path(destination).suffix == ".rejected":
                raise AssertionError("Rollback must not replace or withdraw an occupied path")
            result = original_replace(path, destination)
            if path.suffix == ".new":
                self.source.write_text("Changed source.")
            return result

        with patch.object(Path, "replace", replace):
            with self.assertRaisesRegex(RuntimeError, "rollback incomplete") as caught:
                books.publish(self.db, self.out)
        (staging,) = self.out.glob(".aps-publish-*")
        self.assertEqual((staging / "0.old").read_text(), "Prior owner artifact.")
        self.assertEqual(target.read_text(), self.text)
        self.assertIn(
            f"unaccepted output may remain at {target}",
            "\n".join(caught.exception.__notes__),
        )

    def test_rollback_preserves_concurrent_owner_replacement_and_previous_backup(self):
        self.out.mkdir()
        target = self.out / "book.md"
        target.write_text("Prior owner artifact.")
        original_replace = Path.replace

        def replace(path, destination):
            result = original_replace(path, destination)
            if path.suffix == ".new":
                owner = self.out / "owner.new"
                owner.write_text("Concurrent owner artifact.")
                original_replace(owner, destination)
                self.source.write_text("Changed source.")
            return result

        with patch.object(Path, "replace", replace):
            with self.assertRaisesRegex(RuntimeError, "rollback incomplete"):
                books.publish(self.db, self.out)
        (staging,) = self.out.glob(".aps-publish-*")
        self.assertEqual((staging / "0.old").read_text(), "Prior owner artifact.")
        self.assertEqual(target.read_text(), "Concurrent owner artifact.")

    def test_inventory_resolves_each_unique_source_once_and_context_order_is_unchanged(
        self,
    ):
        source = self.root / "many" / "book"
        source.mkdir(parents=True)
        for i in range(30):
            (source / f"{i}.md").write_text("# Heading\n\nFictional prose.\n")
        db = books.connect(self.root / "many-work")
        try:
            books.prepare(db, [self.root / "many"], "prompt", 3000, list, lambda t, n: [t])
            original = Path.resolve
            with patch.object(Path, "resolve", autospec=True, side_effect=original) as resolve:
                books.inventory_rows(db)
                paths = {item["path"] for item in json.loads(db.execute("SELECT manifest FROM books").fetchone()[0])}
                paths.update(r[0] for r in db.execute("SELECT DISTINCT source FROM chunks WHERE source!=''"))
                self.assertEqual(resolve.call_count, len(paths))
                self.assertLessEqual(resolve.call_count, 60)
        finally:
            db.close()
        for row in self.rows:
            _, fragments = books.source_fragments(self.db, row)
            old = sorted(
                (f for f in fragments if f["ordinal"] != row["ordinal"]),
                key=lambda f: abs(f["ordinal"] - row["ordinal"]),
            )[:6]
            self.assertEqual(
                [f["ordinal"] for f in books.source_context(self.db, row, fragments)["neighbors"]],
                sorted(f["ordinal"] for f in old),
            )

    def test_processor_drift_rejects_resume_without_rewriting_existing_fingerprints(
        self,
    ):
        prior = [tuple(row) for row in self.db.execute("SELECT * FROM books ORDER BY id")]
        inventory = self.db.execute("SELECT value FROM preparation WHERE key='inventory'").fetchone()[0]
        changed = [*books.segmentation_identity(), "fixture_processor_drift"]
        with patch.object(books, "segmentation_identity", return_value=changed):
            with self.assertRaisesRegex(ValueError, "use a new work directory"):
                books.prepare(
                    self.db,
                    [self.root / "source"],
                    "prompt",
                    3000,
                    list,
                    lambda t, n: [t],
                )
        self.assertEqual(
            [tuple(row) for row in self.db.execute("SELECT * FROM books ORDER BY id")],
            prior,
        )
        self.assertEqual(
            self.db.execute("SELECT value FROM preparation WHERE key='inventory'").fetchone()[0],
            inventory,
        )

    def test_each_remote_admission_rechecks_holds_after_a_previous_callback(self):
        with self.db:
            self.db.execute("DELETE FROM quality_selections")
        routes = ["freellm:fresh-reviewer", "freellm:fresh-repairer"]
        calibrate_fixture(self.db, routes)
        calls = []

        def complete(payload, route, prompt):
            calls.append(route)
            with self.db:
                add_hold(self.db)
            return passing(payload, route, prompt)

        result = quality.review(self.db, routes, "prompt", complete=complete)
        self.assertIn("unknown", result.values())
        self.assertEqual(len(calls), 1)
        self.assertTrue(quality.holds(self.db))


class PublicationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.staging = self.root / "staging"
        self.staging.mkdir()

    def record(self, name, prior=True):
        target = self.root / (name + ".md")
        target.write_text("Unaccepted output.")
        backup = self.staging / (name + ".old")
        if prior:
            backup.write_text("Prior owner artifact.")
        return target, backup if prior else None

    def test_rollback_avoids_ownership_reads_that_allow_atomic_replacement_races(
        self,
    ):
        for prior in (False, True):
            with self.subTest(prior=prior):
                record = self.record(str(prior), prior)
                target, backup = record[:2]
                original_read = Path.read_bytes
                owner = self.root / "owner.next"
                owner.write_text("Concurrent owner artifact.")

                def read(path):
                    data = original_read(path)
                    if path == target and owner.exists():
                        owner.replace(target)
                    return data

                with patch.object(Path, "read_bytes", read):
                    failures = books._restore_publication([record], self.staging)
                self.assertTrue(owner.exists(), "Rollback must not inspect target contents")
                self.assertEqual(target.read_text(), "Unaccepted output.")
                self.assertTrue(failures)
                if prior:
                    self.assertEqual(backup.read_text(), "Prior owner artifact.")

    def test_exclusive_restore_cannot_clobber_a_racing_owner_creation(self):
        record = self.record("race")
        target, backup = record[:2]
        target.unlink()
        link = os.link
        called = []

        def race(source, destination, **kwargs):
            called.append(True)
            owner = self.root / "racing-owner.next"
            owner.write_text("Concurrent owner artifact.")
            owner.replace(destination)
            return link(source, destination, **kwargs)

        with patch.object(os, "link", race):
            failures = books._restore_publication([record], self.staging)
        self.assertEqual(called, [True])
        self.assertEqual(target.read_text(), "Concurrent owner artifact.")
        self.assertEqual(backup.read_text(), "Prior owner artifact.")
        self.assertTrue(failures)

    def test_no_prior_output_owner_replacement_during_presence_check_is_untouched(self):
        record = self.record("new-output", prior=False)
        target = record[0]
        original_exists = Path.exists
        replaced = []

        def exists(path):
            result = original_exists(path)
            if path == target and not replaced:
                owner = self.root / "owner.next"
                owner.write_text("Concurrent owner artifact.")
                owner.replace(target)
                replaced.append(True)
            return result

        with patch.object(Path, "exists", exists):
            failures = books._restore_publication([record], self.staging)
        self.assertEqual(replaced, [True])
        self.assertEqual(target.read_text(), "Concurrent owner artifact.")
        self.assertTrue(failures)
        self.assertIn("Unaccepted output may remain", failures[0])

    def test_conflict_does_not_prevent_absent_destination_restore(self):
        absent = self.record("absent")
        occupied = self.record("occupied")
        absent[0].unlink()
        occupied[0].write_text("Concurrent owner artifact.")
        failures = books._restore_publication([absent, occupied], self.staging)
        self.assertEqual(len(failures), 1)
        self.assertEqual(absent[0].read_text(), "Prior owner artifact.")
        self.assertEqual(occupied[0].read_text(), "Concurrent owner artifact.")
        self.assertEqual(occupied[1].read_text(), "Prior owner artifact.")

    def test_dangling_owner_symlink_is_an_occupied_destination(self):
        record = self.record("symlink")
        target, backup = record[:2]
        target.unlink()
        target.symlink_to(self.root / "owner-missing")
        failures = books._restore_publication([record], self.staging)
        self.assertTrue(target.is_symlink())
        self.assertEqual(target.readlink(), self.root / "owner-missing")
        self.assertEqual(backup.read_text(), "Prior owner artifact.")
        self.assertTrue(failures)


if __name__ == "__main__":
    unittest.main()
