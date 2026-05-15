# twat-text

`twat-text` is the user-facing text utility package in the twat ecosystem.

It keeps deterministic text algorithms local and delegates LLM-powered operations to `twat_llm` through a narrow adapter. Generated images, video, audio, and speech belong in `twat_genai` and media domain packages, not here.

## Install

```bash
pip install twat-text
```

## Deterministic API

```python
from twat_text import clean_text, chunk_text, extract_emails, convert_text, estimate_tokens

text = clean_text(" “Hello”\tworld ")
chunks = chunk_text(long_document, max_chars=2000, overlap=100)
emails = extract_emails("Ada <ada@example.com>")
plain = convert_text("<p>Hello &amp; welcome</p>", source="html", target="plain")
tokens = estimate_tokens(plain)
```

Available deterministic helpers include:

- `normalize_text` and `clean_text`
- `chunk_text` and `context_window_chunks`
- `extract_urls`, `extract_emails`, and `extract_numbers`
- `markdown_to_plain`, `html_to_plain`, `plain_to_html`, and `convert_text`
- `estimate_tokens`

## LLM-backed API

```python
from twat_text import summarize, rewrite, extract_structured, classify

summary = summarize(long_text)
rewrite = rewrite(draft, instruction="Make this friendlier")
data = extract_structured(note, schema_hint="Return JSON with date and total")
label = classify(message, labels=["support", "sales", "spam"])
```

These functions import `twat_llm` only when called, so deterministic utilities stay dependency-light and testable.

## CLI

```bash
twat-text clean "  Hello   world  "
twat-text chunk "long text..." --max-chars 500 --overlap 50
twat-text convert "<p>Hello</p>" --source html --target plain
twat-text summarize "long text..."
twat-text rewrite "draft text" --instruction "Make it concise"
```

Through the host plugin dispatcher, the same package is available as `twat text ...` once installed.

## Development

```bash
hatch run test
hatch run lint
hatch run type-check
```

The compatibility `Config` and `process_data` API remains available, but `process_data` now returns cleaned text, chunks, simple extracted values, and an estimated token count instead of an empty placeholder.
