# Launch checklist

This checklist is the publication gate for the public PyBynix presentation. It records
what is ready in the repository and keeps implementation, offline-test, and live-demo
evidence separate. It does not claim one-to-one PyQuotex compatibility or a universal
performance guarantee.

## Presentation

- [x] README leads with capability categories: open/free access, fast stream-first data,
      multi-login and client pooling, caller-owned automation, pure indicators, and
      execution conveniences.
- [x] README links directly to the [feature and fallback matrix](FEATURE_MATRIX.md).
- [x] All twenty maintained README editions are generated from reviewed locale sources.
- [x] The README keeps the public-data quickstart before credentialed execution.
- [x] The README describes indicators as pure measurements and automation as caller-owned;
      it does not advertise a strategy, signals, or Martingale behavior.

## Evidence and scope

- [x] The feature matrix labels implemented, offline-tested, and live-demo-observed scope.
- [x] New indicator and execution-convenience behavior is covered by offline tests.
- [x] The validation record keeps live observations, local measurements, and outstanding
      checks distinct.
- [x] No exact PyQuotex/Bynix parity claim or unobserved endpoint is added.
- [x] No credentials, saved sessions, private captures, or paper PDFs are publication inputs.

## Owner release gate

- [x] Owner approves the final public tree and GitHub publication target.
- [x] GitHub repository is public at `https://github.com/thecmjhb/PyBynix`.
- [ ] Remote CI is run and reviewed; a prepared workflow is not evidence of a passed run.
- [ ] PyPI publication is performed only with explicit owner approval and Trusted Publishing configured.
- [ ] Any later live or funded validation is recorded with its date, account scope, amount,
      and exact observed behavior.

## Local presentation and release audit — 22 October 2026

Executed from the public checkout using the dedicated parent `.venv`:

- `../.venv/Scripts/python.exe tools/readmes.py`: generated 20 README editions and badges.
- `../.venv/Scripts/python.exe -m pytest tests/test_documentation.py -q`: 22 passed.
- `../.venv/Scripts/ruff.exe check .`: all checks passed.
- `../.venv/Scripts/python.exe -m pytest -q`: 100 passed in 14.18 seconds.
- `../private/env311/Scripts/python.exe -m pytest -q`: 100 passed in 7.68 seconds.
- `../.venv/Scripts/python.exe -m compileall -q src tests examples tools`: passed.

These checks validate local documentation and offline behavior. They add no new live-demo
observation and do not constitute remote CI or publication.

The current validation boundary and open checks are maintained in
[docs/VALIDATION.md](VALIDATION.md). The repository is ready for review when the checked
items above remain true; publication itself remains an owner action.
