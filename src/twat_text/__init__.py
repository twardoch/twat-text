"""twat-text: Text processing utilities for the twat ecosystem."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("twat-text")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"

from .twat_text import (
    Config,
    chunk_text,
    classify,
    clean_text,
    context_window_chunks,
    convert_text,
    estimate_tokens,
    extract_emails,
    extract_numbers,
    extract_structured,
    extract_urls,
    html_to_plain,
    main,
    markdown_to_plain,
    normalize_text,
    plain_to_html,
    process_data,
    rewrite,
    summarize,
)

__all__ = [
    "Config",
    "__version__",
    "chunk_text",
    "classify",
    "clean_text",
    "context_window_chunks",
    "convert_text",
    "estimate_tokens",
    "extract_emails",
    "extract_numbers",
    "extract_structured",
    "extract_urls",
    "html_to_plain",
    "main",
    "markdown_to_plain",
    "normalize_text",
    "plain_to_html",
    "process_data",
    "rewrite",
    "summarize",
]
