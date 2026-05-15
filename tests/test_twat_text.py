"""Test suite for twat_text."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

import twat_text
from twat_text import (
    Config,
    chunk_text,
    clean_text,
    context_window_chunks,
    convert_text,
    estimate_tokens,
    extract_emails,
    extract_numbers,
    extract_urls,
    html_to_plain,
    main,
    markdown_to_plain,
    plain_to_html,
    process_data,
    rewrite,
    summarize,
)


def test_version() -> None:
    """Verify package exposes version."""

    assert twat_text.__version__


def test_clean_text_normalizes_whitespace_and_quotes() -> None:
    """clean_text handles typography and whitespace."""

    assert clean_text(" “Hello”\t\tworld\n\n\nNext ") == '"Hello" world\n\nNext'


def test_chunk_text_paragraphs_with_overlap() -> None:
    """chunk_text preserves paragraph boundaries when possible."""

    chunks = chunk_text("Alpha paragraph\n\nBeta paragraph\n\nGamma paragraph", max_chars=30, overlap=3)

    assert chunks == ["Alpha paragraph", "aph Beta paragraph", "aph Gamma paragraph"]


def test_chunk_text_rejects_bad_sizes() -> None:
    """chunk_text validates max_chars and overlap."""

    with pytest.raises(ValueError, match="max_chars"):
        chunk_text("text", max_chars=0)
    with pytest.raises(ValueError, match="overlap"):
        chunk_text("text", max_chars=5, overlap=5)


def test_extract_helpers() -> None:
    """Extraction helpers return URLs, emails, and numbers."""

    text = "Email ada@example.com, visit https://example.com, count 42 and -3.5."
    assert extract_emails(text) == ["ada@example.com"]
    assert extract_urls(text) == ["https://example.com"]
    assert extract_numbers(text) == [42.0, -3.5]


def test_format_conversions() -> None:
    """Markdown, HTML, and plain conversion boundaries work."""

    assert markdown_to_plain("# Hello [Ada](https://example.com)") == "Hello Ada"
    assert html_to_plain("<h1>Hello</h1><p>Ada &amp; Bob</p>") == "Hello\n\nAda & Bob"
    assert plain_to_html("Hello\n\nAda") == "<p>Hello</p>\n<p>Ada</p>"
    assert convert_text("**Hello**", source="markdown", target="html") == "<p>Hello</p>"


def test_context_token_utilities() -> None:
    """Token estimate and context chunks use deterministic heuristics."""

    assert estimate_tokens("12345", chars_per_token=4) == 2
    assert context_window_chunks("one two three four", max_tokens=2, chars_per_token=4)


def test_process_data_returns_real_text_summary() -> None:
    """process_data remains compatible while returning useful data."""

    result = process_data(["Contact ada@example.com", "Score 12"], Config("test", "value", {"max_chars": 20}))

    assert result["text"] == "Contact ada@example.com\nScore 12"
    assert result["emails"] == ["ada@example.com"]
    assert result["numbers"] == [12.0]
    assert result["chunks"]


def test_process_data_empty_raises() -> None:
    """Empty process_data input keeps the existing error contract."""

    with pytest.raises(ValueError, match="Input data cannot be empty"):
        process_data([])


@patch("twat_text.twat_text._llm_summarize_text", return_value="summary")
def test_summarize_uses_twat_llm_adapter(mock_summarize: Any) -> None:
    """summarize delegates LLM work to twat_llm."""

    assert summarize("Long text") == "summary"
    mock_summarize.assert_called_once_with("Long text", instruction=None, model_ids=None)


@patch("twat_text.twat_text._llm_rewrite_text", return_value="rewrite")
def test_rewrite_uses_twat_llm_adapter(mock_rewrite: Any) -> None:
    """rewrite delegates LLM work to twat_llm."""

    assert rewrite("hi", instruction="formal") == "rewrite"
    mock_rewrite.assert_called_once_with("hi", instruction="formal", model_ids=None)


def test_main_help_runs() -> None:
    """CLI help path exits successfully through argparse."""

    with patch("sys.argv", ["twat-text", "clean", "  Hello   world  "]):
        assert main() == 0
