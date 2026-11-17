# this_file: src/twat_text/aps/__init__.py
"""Abstractive Proposition Segmentation (APS) for long Markdown corpora.

Modules:
    books      Durable chunked APS jobs (prepare, status, run, reconcile-poe).
    quality    Contract-evidence quality audit and calibration of APS output.
    semantics  Semantic hierarchy extraction over accepted APS output.
    transport  One raw FreeLLM POST per attempt with a killable deadline.
    unorouter  Direct UnoRouter dispatch for pinned free models.

Run a module as ``python -m twat_text.aps.books --help`` (or the
``twat-aps-books`` / ``twat-aps-quality`` / ``twat-aps-semantics`` scripts).
Work data defaults to ``./_private`` in the current directory; override it with
``TWAT_TEXT_APS_BASE``. Requires the ``aps`` extra: ``pip install "twat-text[aps]"``.
"""
