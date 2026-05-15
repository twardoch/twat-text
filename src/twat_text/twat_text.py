#!/usr/bin/env python3
"""User-facing text processing utilities for the twat ecosystem.

# this_file: src/twat_text/twat_text.py
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from importlib import import_module
from typing import Any, ClassVar, Literal, Protocol, cast

WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
URL_RE = re.compile(r"https?://[^\s)>\"]+")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
NUMBER_RE = re.compile(r"(?<!\w)[+-]?(?:\d+\.\d+|\d+)(?!\w)")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
MARKDOWN_DECORATION_RE = re.compile(r"[`*_~>#-]+")


@dataclass
class Config:
    """Compatibility configuration for process_data."""

    name: str
    value: str | int | float
    options: dict[str, Any] | None = None


class _HTMLTextExtractor(HTMLParser):
    """Small HTML-to-text parser that keeps paragraph boundaries."""

    block_tags: ClassVar[set[str]] = {
        "p",
        "div",
        "section",
        "article",
        "br",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.block_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.block_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        """Return extracted text."""

        return "".join(self.parts)


UnicodeForm = Literal["NFC", "NFD", "NFKC", "NFKD"]


def normalize_text(text: str, *, unicode_form: UnicodeForm = "NFC", smart_quotes: bool = True) -> str:
    """Normalize Unicode and common typography while preserving words."""

    normalized = unicodedata.normalize(unicode_form, text)
    if smart_quotes:
        translation = str.maketrans(
            {"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'", "\u2014": "-", "\u2013": "-", "\u2026": "..."}
        )
        normalized = normalized.translate(translation)
    return normalized


def clean_text(text: str, *, lowercase: bool = False, keep_paragraphs: bool = True) -> str:
    """Clean whitespace and normalize typography in plain text."""

    cleaned = normalize_text(text).replace("\r\n", "\n").replace("\r", "\n")
    cleaned = "\n".join(WHITESPACE_RE.sub(" ", line).strip() for line in cleaned.split("\n"))
    cleaned = MULTI_NEWLINE_RE.sub("\n\n", cleaned).strip()
    if not keep_paragraphs:
        cleaned = WHITESPACE_RE.sub(" ", cleaned.replace("\n", " ")).strip()
    if lowercase:
        cleaned = cleaned.lower()
    return cleaned


def chunk_text(
    text: str, *, max_chars: int = 2000, overlap: int = 0, by: Literal["paragraph", "word"] = "paragraph"
) -> list[str]:
    """Split text into chunks with optional character overlap."""

    if max_chars < 1:
        msg = "max_chars must be greater than zero."
        raise ValueError(msg)
    if overlap < 0 or overlap >= max_chars:
        msg = "overlap must be >= 0 and smaller than max_chars."
        raise ValueError(msg)

    source = clean_text(text)
    if not source:
        return []
    units = source.split() if by == "word" else [part.strip() for part in source.split("\n\n") if part.strip()]
    separator = " " if by == "word" else "\n\n"
    chunks: list[str] = []
    current = ""
    for unit in units:
        candidate = unit if not current else f"{current}{separator}{unit}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(unit) <= max_chars:
            current = _overlap_prefix(chunks[-1], overlap) + unit if overlap and chunks else unit
            continue
        chunks.extend(_split_long_unit(unit, max_chars, overlap))
        current = ""
    if current:
        chunks.append(current)
    return chunks


def _overlap_prefix(previous: str, overlap: int) -> str:
    if overlap <= 0:
        return ""
    return previous[-overlap:] + " "


def _split_long_unit(unit: str, max_chars: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    step = max_chars - overlap
    start = 0
    while start < len(unit):
        chunks.append(unit[start : start + max_chars])
        start += step
    return chunks


def extract_urls(text: str) -> list[str]:
    """Extract HTTP(S) URLs from text."""

    return [url.rstrip(".,;:!?)]}") for url in URL_RE.findall(text)]


def extract_emails(text: str) -> list[str]:
    """Extract email addresses from text."""

    return EMAIL_RE.findall(text)


def extract_numbers(text: str) -> list[float]:
    """Extract integer and decimal numbers as floats."""

    return [float(match) for match in NUMBER_RE.findall(text)]


def markdown_to_plain(text: str) -> str:
    """Convert common Markdown inline/block syntax to plain text."""

    plain = MARKDOWN_IMAGE_RE.sub(r"\1", text)
    plain = MARKDOWN_LINK_RE.sub(r"\1", plain)
    plain = MARKDOWN_DECORATION_RE.sub("", plain)
    return clean_text(plain)


def html_to_plain(text: str) -> str:
    """Convert HTML to readable plain text."""

    parser = _HTMLTextExtractor()
    parser.feed(text)
    return clean_text(html.unescape(parser.text()))


def plain_to_html(text: str) -> str:
    """Convert plain text paragraphs to minimal HTML paragraphs."""

    paragraphs = [html.escape(part) for part in clean_text(text).split("\n\n") if part]
    return "\n".join(f"<p>{paragraph}</p>" for paragraph in paragraphs)


def convert_text(
    text: str, *, source: Literal["plain", "markdown", "html"] = "plain", target: Literal["plain", "html"] = "plain"
) -> str:
    """Convert between simple plain, Markdown, and HTML boundaries."""

    plain = (
        html_to_plain(text)
        if source == "html"
        else markdown_to_plain(text)
        if source == "markdown"
        else clean_text(text)
    )
    return plain_to_html(plain) if target == "html" else plain


def estimate_tokens(text: str, *, chars_per_token: int = 4) -> int:
    """Estimate token count using a dependency-light character heuristic."""

    if chars_per_token < 1:
        msg = "chars_per_token must be greater than zero."
        raise ValueError(msg)
    cleaned = clean_text(text, keep_paragraphs=False)
    return (len(cleaned) + chars_per_token - 1) // chars_per_token


def context_window_chunks(
    text: str, *, max_tokens: int, chars_per_token: int = 4, overlap_tokens: int = 0
) -> list[str]:
    """Chunk text using an estimated token budget."""

    return chunk_text(text, max_chars=max_tokens * chars_per_token, overlap=overlap_tokens * chars_per_token)


def summarize(text: str, *, instruction: str | None = None, model_ids: list[str] | None = None) -> str:
    """Summarize text through the narrow twat_llm adapter."""

    return _llm_summarize_text(text, instruction=instruction, model_ids=model_ids)


def rewrite(text: str, *, instruction: str, model_ids: list[str] | None = None) -> str:
    """Rewrite text through the narrow twat_llm adapter."""

    return _llm_rewrite_text(text, instruction=instruction, model_ids=model_ids)


def extract_structured(text: str, *, schema_hint: str = "Return JSON.", model_ids: list[str] | None = None) -> str:
    """Extract structured data through the narrow twat_llm adapter."""

    return _llm_extract_structured_data(text, schema_hint=schema_hint, model_ids=model_ids)


def classify(text: str, *, labels: list[str], model_ids: list[str] | None = None) -> str:
    """Classify text through the narrow twat_llm adapter."""

    return _llm_classify_text(text, labels=labels, model_ids=model_ids)


class _TwatLlmModule(Protocol):
    def summarize_text(
        self, text: str, *, instruction: str | None = None, model_ids: list[str] | None = None
    ) -> str: ...

    def rewrite_text(self, text: str, *, instruction: str, model_ids: list[str] | None = None) -> str: ...

    def extract_structured_data(self, text: str, *, schema_hint: str, model_ids: list[str] | None = None) -> str: ...

    def classify_text(self, text: str, *, labels: list[str], model_ids: list[str] | None = None) -> str: ...


def _twat_llm() -> _TwatLlmModule:
    return cast(_TwatLlmModule, import_module("twat_llm"))


def _llm_summarize_text(text: str, *, instruction: str | None = None, model_ids: list[str] | None = None) -> str:
    return _twat_llm().summarize_text(text, instruction=instruction, model_ids=model_ids)


def _llm_rewrite_text(text: str, *, instruction: str, model_ids: list[str] | None = None) -> str:
    return _twat_llm().rewrite_text(text, instruction=instruction, model_ids=model_ids)


def _llm_extract_structured_data(text: str, *, schema_hint: str, model_ids: list[str] | None = None) -> str:
    return _twat_llm().extract_structured_data(text, schema_hint=schema_hint, model_ids=model_ids)


def _llm_classify_text(text: str, *, labels: list[str], model_ids: list[str] | None = None) -> str:
    return _twat_llm().classify_text(text, labels=labels, model_ids=model_ids)


def process_data(data: list[Any], config: Config | None = None, *, debug: bool = False) -> dict[str, Any]:
    """Compatibility API that cleans, chunks, and extracts basics from list data."""

    if not data:
        msg = "Input data cannot be empty"
        raise ValueError(msg)
    text = "\n".join(str(item) for item in data)
    options = config.options if config else {}
    max_chars = int(options.get("max_chars", 2000)) if options else 2000
    cleaned = clean_text(text)
    return {
        "text": cleaned,
        "chunks": chunk_text(cleaned, max_chars=max_chars),
        "urls": extract_urls(cleaned),
        "emails": extract_emails(cleaned),
        "numbers": extract_numbers(cleaned),
        "tokens_estimate": estimate_tokens(cleaned),
        "debug": debug,
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the twat-text CLI parser."""

    parser = argparse.ArgumentParser(description="twat-text deterministic and LLM-backed text utilities")
    subparsers = parser.add_subparsers(dest="command")
    clean_parser = subparsers.add_parser("clean", help="clean and normalize text")
    clean_parser.add_argument("text")
    clean_parser.add_argument("--lowercase", action="store_true")

    chunk_parser = subparsers.add_parser("chunk", help="split text into chunks")
    chunk_parser.add_argument("text")
    chunk_parser.add_argument("--max-chars", type=int, default=2000)
    chunk_parser.add_argument("--overlap", type=int, default=0)

    convert_parser = subparsers.add_parser("convert", help="convert markdown/html/plain boundaries")
    convert_parser.add_argument("text")
    convert_parser.add_argument("--source", choices=["plain", "markdown", "html"], default="plain")
    convert_parser.add_argument("--target", choices=["plain", "html"], default="plain")

    summarize_parser = subparsers.add_parser("summarize", help="summarize via twat_llm")
    summarize_parser.add_argument("text")
    summarize_parser.add_argument("--instruction")

    rewrite_parser = subparsers.add_parser("rewrite", help="rewrite via twat_llm")
    rewrite_parser.add_argument("text")
    rewrite_parser.add_argument("--instruction", required=True)
    return parser


def main() -> int:
    """Run the twat-text command-line interface."""

    parser = build_parser()
    args = parser.parse_args()
    if args.command == "clean":
        print(clean_text(args.text, lowercase=args.lowercase))
    elif args.command == "chunk":
        for part in chunk_text(args.text, max_chars=args.max_chars, overlap=args.overlap):
            print(part)
    elif args.command == "convert":
        print(convert_text(args.text, source=args.source, target=args.target))
    elif args.command == "summarize":
        print(summarize(args.text, instruction=args.instruction))
    elif args.command == "rewrite":
        print(rewrite(args.text, instruction=args.instruction))
    else:
        parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
