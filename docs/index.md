# twat-text

`twat-text` is the text-processing plugin for the [twat](https://github.com/twardoch/twat) ecosystem. It keeps deterministic text algorithms local and delegates LLM-powered operations to `twat_llm` through a narrow adapter.

## Design principles

- **Dependency-light deterministic core.** Functions like `clean_text`, `chunk_text`, and `estimate_tokens` rely only on the Python standard library so they stay fast, testable, and importable without network access.
- **Lazy LLM wiring.** `summarize`, `rewrite`, `extract_structured`, and `classify` import `twat_llm` only when called, keeping startup cost near zero for callers that use only the deterministic helpers.
- **twat plugin contract.** The package registers itself under the `twat.plugins` entry-point group so the host `twat` dispatcher can expose it as `twat text ...`.

## Installation

```bash
pip install twat-text
```

To enable LLM-backed functions:

```bash
pip install "twat-text[llm]"
```

## Quick start

```python
from twat_text import clean_text, chunk_text, estimate_tokens, convert_text

# normalize whitespace and smart-quotes
text = clean_text(' "Hello"\tworld ')
# 'Hello" world'  (smart quotes straightened)

# split a long document for an LLM context window
chunks = chunk_text(long_doc, max_chars=2000, overlap=100)

# convert HTML to plain text
plain = convert_text("<p>Hello &amp; world</p>", source="html", target="plain")

# rough token estimate (no external dependency)
n = estimate_tokens(plain)
```

### LLM-backed helpers

```python
from twat_text import summarize, rewrite, classify

summary = summarize(long_text)
formal  = rewrite(draft, instruction="Make this formal")
label   = classify(message, labels=["support", "sales", "spam"])
```

## Available functions

| Function | Kind | Description |
|---|---|---|
| `normalize_text` | deterministic | Unicode normalization + smart-quote replacement |
| `clean_text` | deterministic | Whitespace collapse, paragraph preservation |
| `chunk_text` | deterministic | Split text into sized chunks with optional overlap |
| `context_window_chunks` | deterministic | Chunk by estimated token budget |
| `extract_urls` | deterministic | Find HTTP(S) URLs |
| `extract_emails` | deterministic | Find email addresses |
| `extract_numbers` | deterministic | Extract integers and decimals |
| `markdown_to_plain` | deterministic | Strip Markdown syntax |
| `html_to_plain` | deterministic | Parse HTML to readable plain text |
| `plain_to_html` | deterministic | Wrap paragraphs in `<p>` tags |
| `convert_text` | deterministic | Convert between plain / markdown / HTML |
| `estimate_tokens` | deterministic | Character-based token count estimate |
| `summarize` | LLM-backed | Summarize via `twat_llm` |
| `rewrite` | LLM-backed | Rewrite with an instruction via `twat_llm` |
| `extract_structured` | LLM-backed | Extract JSON/structured data via `twat_llm` |
| `classify` | LLM-backed | Label text from a list via `twat_llm` |
