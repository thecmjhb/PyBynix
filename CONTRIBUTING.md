# Contributing

Install `.[dev]`, then run `ruff check .`, `pytest` and `python -m build`.
Use synthetic fixtures or fully sanitized observed samples. Ordinary tests must not
log into an account, send an order or require network access. Record live demo checks
separately with date, code version and limitations. Keep strategies out of the SDK.

New endpoints need observed requests/responses, typed failure behavior and meaningful
tests. Never infer platform capability from a method name in a different broker SDK.
