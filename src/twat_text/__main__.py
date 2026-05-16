# this_file: src/twat_text/__main__.py
"""Fire CLI entry point for twat-text."""

from __future__ import annotations

import fire


def _version() -> str:
    """Print the installed twat-text version."""
    from twat_text.__version__ import __version__

    return __version__


def _tokenize(text: str, *, chars_per_token: int = 4) -> int:
    """Estimate token count for TEXT using a character heuristic."""
    from twat_text.twat_text import estimate_tokens

    return estimate_tokens(text, chars_per_token=chars_per_token)


def _clean(text: str, *, lowercase: bool = False, keep_paragraphs: bool = True) -> str:
    """Clean whitespace and normalize typography in TEXT."""
    from twat_text.twat_text import clean_text

    return clean_text(text, lowercase=lowercase, keep_paragraphs=keep_paragraphs)


def _markdown(
    text: str,
    *,
    source: str = "markdown",
    target: str = "plain",
) -> str:
    """Convert TEXT between markdown/html/plain formats (source→target)."""
    from twat_text.twat_text import convert_text

    return convert_text(text, source=source, target=target)  # type: ignore[arg-type]


def _detect_lang(text: str) -> str:
    """Detect the language of TEXT (returns ISO 639-1 code via twat_llm)."""
    from twat_text.twat_text import _twat_llm  # type: ignore[attr-defined]

    llm = _twat_llm()
    return llm.classify_text(text, labels=["language detection"])


def _summarize(text: str, *, instruction: str | None = None) -> str:
    """Summarize TEXT via twat_llm (optional INSTRUCTION prompt)."""
    from twat_text.twat_text import summarize

    return summarize(text, instruction=instruction)


COMMANDS: dict[str, object] = {
    "version": _version,
    "tokenize": _tokenize,
    "clean": _clean,
    "markdown": _markdown,
    "detect-lang": _detect_lang,
    "summarize": _summarize,
}


def main() -> None:
    """twat-text: text processing utilities."""
    fire.Fire(COMMANDS, name="twat-text")


def cmd_version() -> None:
    """twat-text version."""
    fire.Fire(_version, name="twat-text-version")


def cmd_tokenize() -> None:
    """Estimate token count for text."""
    fire.Fire(_tokenize, name="twat-text-tokenize")


def cmd_clean() -> None:
    """Clean and normalize text."""
    fire.Fire(_clean, name="twat-text-clean")


def cmd_markdown() -> None:
    """Convert text between markdown/html/plain formats."""
    fire.Fire(_markdown, name="twat-text-markdown")


def cmd_detect_lang() -> None:
    """Detect language of text."""
    fire.Fire(_detect_lang, name="twat-text-detect-lang")


def cmd_summarize() -> None:
    """Summarize text via twat_llm."""
    fire.Fire(_summarize, name="twat-text-summarize")


if __name__ == "__main__":
    main()
