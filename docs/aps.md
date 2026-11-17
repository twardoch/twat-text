# Proposition segmentation (APS)

<!-- this_file: docs/aps.md -->

`twat_text.aps` rewrites long Markdown corpora into *atomic propositions*: short, self-contained
sentences that each carry one fact, with paragraph structure preserved. Headings, code blocks and
tables are protected and copied through unchanged.

It is built for runs that take hours over many files, where a crash, a timeout or a provider that
silently changes behaviour must never corrupt finished work.

```bash
pip install "twat-text[aps]"
```

## Pipeline

| Module | Command | What it does |
| --- | --- | --- |
| `twat_text.aps.books` | `twat-aps-books` | Chunk sources, dispatch APS jobs, publish accepted output |
| `twat_text.aps.quality` | `twat-aps-quality` | Audit accepted output against the source and calibrate reviewers |
| `twat_text.aps.semantics` | `twat-aps-semantics` | Extract a semantic hierarchy from accepted APS output |
| `twat_text.aps.transport` | — | One raw POST per attempt, run in a killable worker process |
| `twat_text.aps.unorouter` | — | Direct dispatch to pinned free UnoRouter models |

Each command is also runnable as `python -m twat_text.aps.<module> --help`.

## Books

```bash
twat-aps-books prepare --root docs/ --work work/ --output out/
twat-aps-books status  --root docs/ --work work/
twat-aps-books run     --root docs/ --work work/ --output out/ \
  --models freellm:openrouter:some-model:free freellm:unorouter:glm-5.3-flash:free
```

- **State** lives in `work/aps.sqlite3` (WAL). Every raw response is committed before it is
  validated, so a crash never loses a paid-for or rate-limited answer.
- **Fingerprints** cover sources, prompt, processor code, chunker, tokenizer and dependency
  versions. If any of them changes, the run stops rather than mixing incompatible output.
  Upgrading `twat-text` therefore starts a new work directory.
- **Model selectors** must be explicit free pins (`freellm:` prefix). Paid, auto and malformed
  selectors are rejected, and no fallback model list is ever sent.
- **Retries**: an explicit rejection is retried on a different model, up to 3 attempts. 5xx
  responses, redirects, timeouts and malformed bodies are recorded as `unknown` and never retried,
  because the request may still have been billed or executed.
- **Context**: each target chunk is sent with up to 6 neighbouring fragments (at most 6000
  characters) as JSON context.
- A file lock prevents two runs from sharing a work directory.

The system prompt ships as `aps-prompt.txt` inside the package.

## Paths and environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `TWAT_TEXT_APS_BASE` | `./_private` | Base for default `--work` and `--output` paths |
| `TWAT_TEXT_FREELLM_PATH` | local checkout | Directory that provides the `freellm` gateway client |

Pass `--work` and `--output` explicitly in scripts; the defaults exist for interactive use.

## Quality and semantics

`twat-aps-quality status|review-live|review-recorded|calibrate-live|calibration-inputs|check-calibration`
reviews accepted chunks against their sources and holds anything whose provenance or billing
evidence is incomplete. A calibration fixture ships with the package.

`twat-aps-semantics verify-input|run|status|reconcile-job --task1-db work/aps.sqlite3 --aps-output out/`
reads only accepted output and keeps its own `semantics.sqlite3`.
