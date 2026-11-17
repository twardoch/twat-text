# this_file: tests/aps/test_aps_paths.py
"""Private defaults follow the checkout; corpus inputs must be explicit."""

import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from twat_text.aps import books as aps_books


class APSPathTests(unittest.TestCase):
    def test_private_base_defaults_to_cwd_or_env(self):
        script = "from twat_text.aps import books; print(books.BASE)"
        with tempfile.TemporaryDirectory() as temp:
            default = subprocess.run(
                [sys.executable, "-c", script],
                cwd=temp,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            self.assertEqual(
                Path(default.stdout.strip()).resolve(),
                (Path(temp) / "_private").resolve(),
            )
            import os

            env = dict(os.environ, TWAT_TEXT_APS_BASE=str(Path(temp) / "x"))
            custom = subprocess.run(
                [sys.executable, "-c", script],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            self.assertEqual(custom.stdout.strip(), str(Path(temp) / "x"))

    def test_generation_commands_require_root_before_creating_work(self):
        with tempfile.TemporaryDirectory() as temp:
            for command in ("prepare", "run"):
                work = Path(temp) / command
                result = subprocess.run(
                    [sys.executable, "-m", aps_books.__name__, command, "--work", str(work)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("requires at least one --root", result.stderr)
                self.assertFalse(work.exists())

    def test_status_accepts_explicit_work_without_corpus_root(self):
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run(
                [sys.executable, "-m", aps_books.__name__, "status", "--work", temp],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "{}")
