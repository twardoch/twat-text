# this_file: tests/aps/test_aps_markdown.py
"""Protect original Markdown bytes and reject model structure/code loss."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from quality_fixtures import review_fixture
from twat_text.aps import books as books
from twat_text.aps import semantics as semantics

TABLE = "\u200b | **for** item **in** word:  \n---|---  \n\u200b | print(item)  \n"
EXAMPLE = "\u200b | >>> word = 'A Bee'  \n---|---  \n\u200b | >>> print(word[0])  \n\u200b | A  \n"


class MarkdownTests(unittest.TestCase):
    def split(self, text):
        return books.segments(text, 10000, list, lambda text, limit: [text])

    def test_table_and_code_spans_are_exact_and_adjacent_prose_stays_grouped(self):
        blocks = [
            "# Title\n",
            TABLE,
            EXAMPLE,
            "    print('indented')\n",
            "~~~~python\nprint('fenced')\n~~~~\n",
        ]
        text = (
            blocks[0]
            + "\nOpening.\n\n"
            + TABLE
            + "\nOne statement.\n\nAnother statement.\n\n"
            + EXAMPLE
            + "\n"
            + blocks[3]
            + "\n"
            + blocks[4]
            + "\nClosing.\n"
        )
        for ending in ("\n", "\r\n", "\r"):
            source = text.replace("\n", ending)
            segments = self.split(source)
            self.assertEqual("".join(value for value, _ in segments), source)
            protected = [value for value, flag in segments if flag]
            for block in blocks:
                self.assertIn(block.replace("\n", ending), protected)
            prose = [value for value, flag in segments if not flag]
            self.assertTrue(any("One statement." in value and "Another statement." in value for value in prose))
            self.assertTrue(all("print(" not in value and "---|---" not in value for value in prose))

    def test_frontmatter_setext_and_unclosed_fences_preserve_original_slices(self):
        text = "---\r\ntitle: Hidden\r\n---\r\nHeading\r\n=======\r\n\r\nText with \u2028 a Unicode separator.\r\n\r\n```python\r\n# not a heading\r\n"
        segments = self.split(text)
        self.assertEqual("".join(value for value, _ in segments), text)
        self.assertIn(("---\r\ntitle: Hidden\r\n---\r\n", True), segments)
        self.assertIn(("Heading\r\n=======\r\n", True), segments)
        self.assertIn(("```python\r\n# not a heading\r\n", True), segments)
        self.assertTrue(any("Unicode separator" in value for value, flag in segments if not flag))

    def test_fence_with_trailing_text_does_not_close_code_early(self):
        text = "```python\n```not-a-close\nprint('still code')\n```\n\nProse.\n"
        segments = self.split(text)
        self.assertEqual(segments[0], (text[: text.index("\n\n") + 1], True))
        self.assertEqual("".join(value for value, _ in segments), text)

    def test_added_structures_and_missing_code_are_rejected_without_stripping(self):
        for output in (
            "## Context heading\n\nTarget prose.",
            "Target prose.\n\n```py\nx=1\n```",
            "- Target prose.",
            "    Target prose.",
            TABLE + "\nTarget prose.",
            "> Target prose.",
        ):
            with (
                self.subTest(output=output),
                self.assertRaisesRegex(ValueError, "structure"),
            ):
                books.validate("Target prose.", output)
        for source, output in (
            ("Use `word` to inspect a string.", "Use word to inspect a string."),
            (
                "Read word[0] first and word[1] second.",
                "Read the first and second character.",
            ),
        ):
            with (
                self.subTest(source=source),
                self.assertRaisesRegex(ValueError, "code|expression"),
            ):
                books.validate(source, output)
        self.assertEqual(
            books.validate("`word` has text and can change.", "`word` has text. `word` can change."),
            "`word` has text. `word` can change.",
        )
        self.assertEqual(
            books.validate("First.\r\n\r\nSecond.", "First.\n\nSecond."),
            "First.\n\nSecond.",
        )

    def test_echo_is_retained_as_rejected_raw_and_tables_publish_byte_exact(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source/book"
            source.mkdir(parents=True)
            text = "# Title\n\nOpening.\n\n" + TABLE + "\nRead word[0].\n"
            (source / "1.md").write_text(text)
            db = books.connect(root / "work")
            try:
                books.prepare(
                    db,
                    [root / "source"],
                    "prompt",
                    10000,
                    list,
                    lambda text, limit: [text],
                )
                complete = Mock(side_effect=lambda original, route, prompt: "# Title\n\n" + original)
                books.run(db, ["a", "b"], "prompt", max_attempts=1, complete=complete)
                for row in db.execute("SELECT raw,validation_reason FROM attempt_responses"):
                    self.assertTrue(row["raw"].startswith("# Title"))
                    self.assertEqual(row["validation_reason"], "Markdown structure changed")
                review_fixture(db)
                books.publish(db, root / "rejected")
                self.assertFalse((root / "rejected/book.md").exists())
                books.run(
                    db,
                    ["a", "b"],
                    "prompt",
                    complete=lambda original, route, prompt: original,
                )
                review_fixture(db)
                books.publish(db, root / "accepted")
                self.assertEqual((root / "accepted/book.md").read_bytes(), text.encode())
                self.assertTrue(all(TABLE not in call.args[0] for call in complete.call_args_list))
                prompt = complete.call_args_list[0].args[2]
                self.assertIn('"target"', prompt)
                self.assertIn('"neighbors"', prompt)
                self.assertIn('"protected": true', prompt)
                self.assertIn("Do not reproduce", prompt)
            finally:
                db.close()

    def test_task2_keeps_setext_heading_and_citable_table_and_indented_context(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source/book"
            source.mkdir(parents=True)
            text = "Heading\n=======\n\n" + TABLE + "\n    word = 'A Bee'\n\nRead word[0].\n"
            (source / "1.md").write_text(text)
            db = books.connect(root / "work")
            try:
                books.prepare(
                    db,
                    [root / "source"],
                    "prompt",
                    10000,
                    list,
                    lambda text, limit: [text],
                )
                books.run(
                    db,
                    ["a", "b"],
                    "prompt",
                    complete=lambda original, route, prompt: original,
                )
                review_fixture(db)
                books.publish(db, root / "output")
                run_id, manifest, fragments = semantics.inspect_task1(root / "work/aps.sqlite3", root / "output")
                target = next(row for row in fragments if not row["protected"])
                self.assertEqual(target["heading_path"], ["Heading"])
                payload = semantics.extraction_payload(target, semantics.source_locator(target, run_id), fragments)
                context = payload["context"]
                self.assertEqual(len(context), 2)
                self.assertTrue(any(TABLE in item["text"] for item in context))
                self.assertTrue(any("word =" in item["text"] for item in context))
                self.assertTrue(all(item["text_sha256"] == semantics.sha(item["text"]) for item in context))
                self.assertTrue(all(item["locator"]["source_sha256"] == target["source_sha256"] for item in context))
            finally:
                db.close()

    def test_nested_container_classification_survives_rewrite_and_semantic_build(self):
        text = "- Outer\n  - Inner\n\n    ```python\n    print(1)\n    ```\n\n    Continuation content.\n"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source/book"
            source.mkdir(parents=True)
            (source / "1.md").write_text(text)
            db = books.connect(root / "work")
            semantic = semantics.connect(root / "semantic")
            try:
                books.prepare(
                    db,
                    [root / "source"],
                    "prompt",
                    10000,
                    list,
                    lambda value, limit: [value],
                )

                def complete(original, route, prompt):
                    if "Continuation" in original:
                        return "Continuation content remains here."
                    return original

                books.run(db, ["a", "b"], "prompt", complete=complete)
                self.assertFalse(db.execute("SELECT 1 FROM chunks WHERE state!='done'").fetchone())
                review_fixture(db)
                books.publish(db, root / "output")
                self.assertEqual(
                    (root / "output/book.md").read_text(),
                    text.replace("Continuation content.", "Continuation content remains here."),
                )
                run_id, manifest, fragments = semantics.inspect_task1(root / "work/aps.sqlite3", root / "output")
                target = next(row for row in fragments if "Continuation" in row["original"])
                self.assertFalse(target["protected"])
                captured = []

                def extract(payload, route, prompt):
                    captured.append(payload)
                    return json.dumps(
                        {
                            "concepts": [],
                            "empty_reason": "Fixture has no extracted concepts",
                        }
                    )

                dataset = semantics.build(semantic, run_id, manifest, fragments, ["a", "b"], extract)
                self.assertTrue(any("Continuation content remains here." in row["aps"] for row in captured))
                self.assertEqual(
                    next(row for row in dataset["coverage"] if row["locator"]["ordinal"] == target["ordinal"])[
                        "disposition"
                    ],
                    "explicit empty finding",
                )
                self.assertIn("    print(1)\n", (root / "output/book.md").read_text())
            finally:
                db.close()
                semantic.close()

    def test_setext_headings_survive_prepare_publish_inspect_and_build_sections(self):
        text = "Main title\n==========\n\nFirst prose.\n\nSubheading\n----------\n\nSecond prose.\n"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source/book"
            source.mkdir(parents=True)
            (source / "1.md").write_text(text)
            db = books.connect(root / "work")
            semantic = semantics.connect(root / "semantic")
            try:
                books.prepare(
                    db,
                    [root / "source"],
                    "prompt",
                    10000,
                    list,
                    lambda value, limit: [value],
                )
                books.run(
                    db,
                    ["a", "b"],
                    "prompt",
                    complete=lambda original, route, prompt: original,
                )
                review_fixture(db)
                books.publish(db, root / "output")
                run_id, manifest, fragments = semantics.inspect_task1(root / "work/aps.sqlite3", root / "output")
                dataset = semantics.build(
                    semantic,
                    run_id,
                    manifest,
                    fragments,
                    ["a", "b"],
                    lambda *args: json.dumps({"concepts": [], "empty_reason": "Fixture"}),
                )
                self.assertEqual(
                    [section["heading_path"] for section in dataset["books"][0]["sections"]],
                    [["Main title"], ["Main title", "Subheading"]],
                )
                self.assertEqual((root / "output/book.md").read_text(), text)
            finally:
                db.close()
                semantic.close()


if __name__ == "__main__":
    unittest.main()
