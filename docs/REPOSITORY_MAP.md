# Repository map

This map keeps the public checkout uploadable and separates product code from evidence and
release tooling.

```text
PyBynix/
├── src/pybynix/          Public SDK package
├── tests/                Offline regression and release-boundary tests
├── examples/             Small runnable usage examples; no order is run implicitly
├── docs/                 API, authentication, recovery, protocol, validation, and matrices
├── assets/               Logo concept 07 and localized language badges
├── benchmarks/           Reproducible benchmark code and sanitized result metadata
├── tools/                README generator, live lab, soak runner, and release builder
├── .github/workflows/    Prepared CI workflow; remote execution is not claimed
├── pyproject.toml        Package metadata, dependencies, CLI entry point, and test config
├── README.md             Generated English product entry point
└── README.bn.md          Generated Bangla product entry point
```

## Where to start

| Need | File |
| --- | --- |
| Install and first public-data stream | `README.md` |
| Complete public API | `docs/API.md` |
| Login/session providers | `docs/AUTHENTICATION.md` |
| Fallback, reconnect, and uncertain orders | `docs/RECOVERY.md` |
| Protocol evidence | `docs/PROTOCOL.md` |
| Feature and fallback coverage | `docs/FEATURE_MATRIX.md` |
| Validation scope and measured limits | `docs/VALIDATION.md` |
| Contribution and safety rules | `CONTRIBUTING.md`, `SECURITY.md`, `NOTICE.md` |

## Publication boundary

The public checkout must not contain `private/`, saved sessions, credentials, browser
profiles, raw captures, order databases, or manuscript working files. The release builder
selects tracked public files and rejects private-like paths before creating source, wheel,
and sdist artifacts. The paper source is maintained outside this public package as
Overleaf-ready LaTeX only; no PDF is part of the release.
