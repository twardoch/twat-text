# Changelog

<!-- this_file: CHANGELOG.md -->

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `twat_text.aps`: Abstractive Proposition Segmentation for long Markdown corpora (`books`,
  `quality`, `semantics`, `transport`, `unorouter`), with `twat-aps-books`, `twat-aps-quality` and
  `twat-aps-semantics` scripts and an `aps` extra. Work paths default to `./_private` or
  `TWAT_TEXT_APS_BASE`; workers run as `python -m twat_text.aps.<module>`.
- APS test suite under `tests/aps/`; CI installs the `aps` extra.
- `docs/aps.md`.
- MkDocs Material documentation site (`mkdocs.yml`, `docs/`) with index, CLI reference, and API reference pages.

### Fixed

- APS rollback notes work on Python 3.10 (`__notes__` instead of `add_note`).

### Changed

- `build.sh --docs` mode; `publish.sh` triggers the GitHub release workflow.

## [v0.1.0] - 2026-06-29

Full rewrite of the text-processing core with a complete public API, Fire CLI, and LLM adapter.

### Added

- Deterministic helpers: `normalize_text`, `clean_text`, `chunk_text`, `context_window_chunks`,
  `extract_urls`, `extract_emails`, `extract_numbers`, `markdown_to_plain`, `html_to_plain`,
  `plain_to_html`, `convert_text`, `estimate_tokens`.
- LLM-backed helpers (lazy import of `twat_llm`): `summarize`, `rewrite`, `extract_structured`, `classify`.
- Fire-based CLI with subcommands: `version`, `tokenize`, `clean`, `markdown`, `summarize`, `detect-lang`.
- Standalone entry-points: `twat-text-version`, `twat-text-tokenize`, `twat-text-clean`,
  `twat-text-markdown`, `twat-text-summarize`, `twat-text-detect-lang`.
- `twat.plugins` entry-point so the host `twat` dispatcher exposes it as `twat text ...`.
- Compatibility `Config` dataclass and `process_data` function that clean, chunk, and extract from list data.
- `py.typed` marker for PEP 561 compliance.
- Full test suite (21 tests) covering deterministic helpers, LLM adapter mocking, and CLI invocation.
- GitHub Actions CI: ruff lint + format check, pytest matrix (Python 3.10–3.12), build verification.

## [v0.0.1] - 2025-02-15

Initial release of the twat-text package.

### Added

- Basic project structure with modern Python packaging (hatchling + hatch-vcs, PEP 621).
- Initial `twat_text.py` skeleton with type hints and `process_data` placeholder.
- Development workflow with Hatch, ruff, mypy.
- MIT License.

[Unreleased]: https://github.com/twardoch/twat-text/compare/v0.1.0...HEAD
[v0.1.0]: https://github.com/twardoch/twat-text/compare/v0.0.1...v0.1.0
[v0.0.1]: https://github.com/twardoch/twat-text/releases/tag/v0.0.1
