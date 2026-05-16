# this_file: tests/test_cli.py
"""CLI tests for twat-text Fire entry points."""

from __future__ import annotations

import subprocess
import sys


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "twat_text", *args],
        capture_output=True,
        text=True,
    )


def test_help_exits_zero():
    """twat-text --help should exit 0 and print help text."""
    result = _run("--help")
    assert result.returncode == 0, result.stderr
    # Fire prints help to stderr; accept either stdout or stderr
    help_text = result.stdout + result.stderr
    assert help_text, "Expected help text on stdout or stderr"
    assert "twat-text" in help_text or "COMMAND" in help_text


def test_version_subcommand():
    """twat-text version should print a semver string."""
    result = _run("version")
    assert result.returncode == 0, result.stderr
    out = result.stdout.strip()
    assert out, "Expected version string"
    parts = out.split(".")
    assert len(parts) >= 2, f"Unexpected version format: {out!r}"


def test_tokenize_help():
    """twat-text tokenize --help should exit 0."""
    result = _run("tokenize", "--help")
    assert result.returncode == 0, result.stderr


def test_clean_help():
    """twat-text clean --help should exit 0."""
    result = _run("clean", "--help")
    assert result.returncode == 0, result.stderr


def test_markdown_help():
    """twat-text markdown --help should exit 0."""
    result = _run("markdown", "--help")
    assert result.returncode == 0, result.stderr


def test_summarize_help():
    """twat-text summarize --help should exit 0."""
    result = _run("summarize", "--help")
    assert result.returncode == 0, result.stderr


def test_tokenize_basic():
    """twat-text tokenize should return an integer count."""
    result = _run("tokenize", "Hello world")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().isdigit(), f"Expected integer, got: {result.stdout!r}"


def test_clean_basic():
    """twat-text clean should return cleaned text."""
    result = _run("clean", "  hello   world  ")
    assert result.returncode == 0, result.stderr
    assert "hello" in result.stdout


def test_markdown_basic():
    """twat-text markdown should convert markdown to plain text."""
    result = _run("markdown", "**bold** text", "--source=markdown", "--target=plain")
    assert result.returncode == 0, result.stderr
    assert "bold" in result.stdout or result.stdout.strip()
