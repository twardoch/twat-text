# CLI Reference

`twat-text` ships a [Fire](https://github.com/google/python-fire)-based CLI. After installation every subcommand is also available as a standalone entry-point (`twat-text-clean`, `twat-text-tokenize`, …).

## Entry point

```
twat-text [COMMAND] [ARGS]
```

Or via the twat host dispatcher once the plugin is installed:

```
twat text [COMMAND] [ARGS]
```

---

## Commands

### `version`

Print the installed package version.

```bash
twat-text version
# 1.2.3
```

---

### `tokenize`

Estimate the token count for a text string using a character heuristic (default: 4 chars per token).

```bash
twat-text tokenize "Hello world"
# 3

twat-text tokenize "Hello world" --chars-per-token 3
# 4
```

**Options**

| Flag | Default | Description |
|---|---|---|
| `--chars-per-token` | `4` | Characters per token for the estimate |

---

### `clean`

Normalize whitespace and typography in a text string.

```bash
twat-text clean "  Hello   world  "
# Hello world

twat-text clean "Hello World" --lowercase
# hello world

twat-text clean "Para one\n\n\nPara two"
# Para one
#
# Para two

twat-text clean "Para one\n\nPara two" --no-keep-paragraphs
# Para one Para two
```

**Options**

| Flag | Default | Description |
|---|---|---|
| `--lowercase` | `False` | Convert output to lowercase |
| `--keep-paragraphs` | `True` | Preserve paragraph boundaries |

---

### `markdown`

Convert text between plain, Markdown, and HTML formats.

```bash
twat-text markdown "**bold** text" --source markdown --target plain
# bold text

twat-text markdown "<p>Hello</p>" --source html --target plain
# Hello

twat-text markdown "Hello world" --source plain --target html
# <p>Hello world</p>
```

**Options**

| Flag | Default | Choices |
|---|---|---|
| `--source` | `markdown` | `plain`, `markdown`, `html` |
| `--target` | `plain` | `plain`, `html` |

---

### `summarize`

Summarize text via `twat_llm` (requires `twat-text[llm]` installed).

```bash
twat-text summarize "Long article text..."

twat-text summarize "Long article text..." --instruction "Focus on key dates"
```

**Options**

| Flag | Default | Description |
|---|---|---|
| `--instruction` | `None` | Optional prompt directive for the LLM |

---

## Standalone entry-points

Each subcommand is also callable directly:

| Command | Entry-point |
|---|---|
| `twat-text version` | `twat-text-version` |
| `twat-text tokenize` | `twat-text-tokenize` |
| `twat-text clean` | `twat-text-clean` |
| `twat-text markdown` | `twat-text-markdown` |
| `twat-text summarize` | `twat-text-summarize` |
| `twat-text detect-lang` | `twat-text-detect-lang` |
