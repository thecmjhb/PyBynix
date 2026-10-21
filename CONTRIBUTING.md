# Contributing

Install `.[dev,network,dataframe]`, then run `ruff check .`, `pytest` and `python -m build`.
Use synthetic fixtures or fully sanitized observed samples. Ordinary tests must not
log into an account, send an order or require network access. Record live demo checks
separately with date, code version and limitations. Keep strategies out of the SDK.

New endpoints need observed requests/responses, typed failure behavior and meaningful
tests. Never infer platform capability from a method name in a different broker SDK.

The twenty README editions use reviewed UTF-8 sources in docs/i18n/locales/. Change
the appropriate locale and run `python tools/readmes.py`; don't hand-edit generated
README files. Preserve method names and code samples across languages. Ordinary tests
check local links, feature completeness and Python snippet syntax without network access.

Live validation is deliberately separate: tools/live_lab.py only affects its own local
test relays; tools/soak.py performs a finite read-only stream run. Demo order experiments
are explicitly scoped and must keep account/trade identifiers out of public artifacts.

Run release packaging from a reviewed, committed public checkout with
`python tools/package_release.py --out PATH`. Publish only its public artifacts; owner
sessions, browser profiles, order databases and manuscript working files stay separate.
