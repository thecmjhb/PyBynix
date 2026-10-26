"""Generate the twenty maintained README editions from reviewed local translations."""

import html
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAPER_DOI = "10.5281/zenodo.23611267"
PAPER_URL = f"https://doi.org/{PAPER_DOI}"
PAPER_TITLE = (
    "PyBynix: Capability-Aware Asynchronous Integration and Safe Execution "
    "Architecture for Bynix Broker"
)
LANGUAGES = [
    "en",
    "bn",
    "zh-CN",
    "hi",
    "es",
    "ar",
    "fr",
    "pt",
    "id",
    "ur",
    "ru",
    "ko",
    "ja",
    "de",
    "tr",
    "it",
    "fa",
    "vi",
    "ta",
    "te",
]
DATA = """import asyncio
from pybynix import Bynix

async def main():
    async with Bynix.public() as client:
        candles = await client.get_candles("EUR/USD OTC", interval="1m", count=100)
        print(candles[-1])
        async with await client.subscribe_ticks("EUR/USD OTC") as ticks:
            for _ in range(3):
                tick = await anext(ticks)
                print(tick.timestamp, tick.price)

asyncio.run(main())"""
AUTH = """import asyncio
import os
from pybynix import Bynix, PasswordAuth, SQLiteJournal

async def main():
    async with Bynix(
        provider=PasswordAuth(os.environ["BYNIX_EMAIL"], os.environ["BYNIX_PASSWORD"]),
        journal=SQLiteJournal("orders.sqlite"),
    ) as client:
        print(await client.get_balance())

asyncio.run(main())"""
EXECUTION = """async def execute_demo_order(client):
    order = await client.place_order("EUR/USD OTC", "call", "1.00", 5)
    result = await client.wait_order_result(order["trade_id"], timeout=90)
    return result"""
NETWORK = """client = Bynix.from_token(token, proxy="socks5://user:password@proxy-host:1080")
# HTTP CONNECT: proxy="http://proxy-host:8080"
# Custom DNS: Bynix.from_token(token, dns_servers=["1.1.1.1"])
# Data fallback: Bynix.public(stream_transport="auto")"""
METHODS = [
    "connect · PasswordAuth · BrowserAuth",
    "get_assets · get_asset · get_payouts",
    "get_candles · get_historical_candles · get_candles_batch",
    "subscribe_ticks · stream_candles · get_tick",
    "place_order · wait_order_result · close_trade_early · ExecutionOrchestrator",
    "SQLiteJournal · reconcile_order · unresolved_orders",
    "ClientPool · account_events · get_balance_breakdown",
    "proxy · dns_servers · connect_any",
    "get_realtime_sentiment · stream_sentiment",
    "export_candles · candles_to_dataframe · resample_candles · sma · ema · rsi · macd · bollinger_bands · atr · stochastic",
]
SECTION_EMOJIS = {    "capabilities_title": "🚀",
    "features_title": "🛠️",
    "install": "📦",
    "quickstart": "⚡",
    "auth_title": "🔐",
    "execution_title": "🎯",
    "network_title": "🌐",
    "cli_title": "💻",
    "docs_title": "📚",
    "validation_title": "🧪",
    "paper_title": "🔬",
    "contribute_title": "🤝",
}
CAPABILITY_EMOJIS = [
    "🛡️",
    "⚡",
    "🔑",
    "🤖",
    "📊",
    "🎯",
    "🔍",
]
CAPABILITY_COUNT = 7

# Release note rendered under the capability list. Editions that have not translated it
# fall back to English, so adding a release never blocks on twenty translations.
WHATS_NEW_TITLE = "What changed in this release"
WHATS_NEW = [
    "**Market session.** Market data is no longer anonymous: the client obtains and "
    "refreshes a market session automatically, and falls back to a guest session when "
    "no account is configured.",
    "**Sealed payloads.** Market responses now arrive encrypted with ChaCha20-Poly1305 "
    "and are opened transparently; a pure-Python RFC 8439 path means no compiled "
    "dependency is required.",
    "**Encrypted streams.** Socket subscriptions carry the market session id, and ticks "
    "are opened with a rotating keyring that the client refreshes across each rotation.",
    "**One session per account.** A market session can be shared between a REST client "
    "and its socket shards via `Bynix(market_session=...)`, which the platform requires.",
    "**Platform limits.** `client.market_limits` exposes the allowance the platform "
    "reports: history calls per minute, maximum candles per request and subscriptions.",
    "**Clearer failures.** A Cloudflare challenge on a market call raises "
    "`ChallengeRequired` instead of a bare `403`, so an IP block is distinguishable "
    "from an authentication problem.",
]


def destination(code):
    return ROOT / (
        "README.md"
        if code == "en"
        else "README.bn.md"
        if code == "bn"
        else f"docs/i18n/README.{code}.md"
    )


def generate():
    translations = {
        p.stem: json.loads(p.read_text(encoding="utf-8"))
        for p in (ROOT / "docs/i18n/locales").glob("*.json")
    }
    if set(translations) != set(LANGUAGES):
        raise ValueError("Exactly the twenty maintained language editions are required")
    for code in LANGUAGES:
        t = translations[code]
        if len(t["features"]) != len(METHODS):
            raise ValueError(f"Feature translation count mismatch: {code}")
        if len(t["capabilities"]) != CAPABILITY_COUNT:
            raise ValueError(f"Capability translation count mismatch: {code}")
        path = destination(code)
        path.parent.mkdir(parents=True, exist_ok=True)

        def rel(value, base=path.parent):
            return os.path.relpath(ROOT / value, base).replace("\\", "/")

        navigation = "".join(
            f'<a href="{rel(destination(c).relative_to(ROOT))}" '
            f'title="{html.escape(translations[c]["name"])}">'
            f'<img src="{rel(f"assets/languages/{c}.svg")}" width="58" height="28" '
            f'alt="{html.escape(translations[c]["name"])}"></a>'
            for c in LANGUAGES
        )
        table = "\n".join(
            f"| `{method}` | {description} |"
            for method, description in zip(METHODS, t["features"], strict=True)
        )
        capabilities = "\n".join(
            f"- {emoji} **{item['name']}** — {item['text']}"
            for emoji, item in zip(CAPABILITY_EMOJIS, t["capabilities"], strict=True)
        )
        whats_new = "\n".join(f"- {item}" for item in t.get("whats_new", WHATS_NEW))
        body = f'''<!-- Generated by tools/readmes.py; edit docs/i18n/locales/{code}.json. -->
<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="{rel("assets/logo-dark.png")}">
    <source media="(prefers-color-scheme: light)" srcset="{rel("assets/logo.png")}">
    <img src="{rel("assets/logo.png")}" alt="PyBynix" width="390">
  </picture>
  <h3>{t["tagline"]}</h3>
  <p>{t["intro"]}</p>
  <p>
    <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&amp;logoColor=white" alt="Python 3.11+">
    <img src="https://img.shields.io/badge/Version-1.0.0-00843D" alt="Version 1.0.0">
    <img src="https://img.shields.io/badge/License-MIT-22c55e" alt="MIT">
    <img src="https://img.shields.io/badge/Documentation-20_languages-C49A00" alt="20 languages">
    <a href="{PAPER_URL}"><img src="https://zenodo.org/badge/DOI/{PAPER_DOI}.svg" alt="DOI {PAPER_DOI}"></a>
  </p>
</div>

<p align="center" dir="ltr">{navigation}</p>

## {SECTION_EMOJIS["capabilities_title"]} {t["capabilities_title"]}

{capabilities}

{t["matrix_link_text"]} [{t["matrix_link_label"]}]({rel("docs/FEATURE_MATRIX.md")}).

### {t.get("whats_new_title", WHATS_NEW_TITLE)}

{whats_new}

## {SECTION_EMOJIS["features_title"]} {t["features_title"]}

| API | {t["features_title"]} |
| --- | --- |
{table}

## {SECTION_EMOJIS["install"]} {t["install"]}

{t["install_text"]}

```console
python -m pip install pybynix
```

```console
python -m pip install "pybynix[browser,network,dataframe]"
python -m playwright install chromium
```

## {SECTION_EMOJIS["quickstart"]} {t["quickstart"]}

{t["quickstart_text"]}

```python
{DATA}
```

## {SECTION_EMOJIS["auth_title"]} {t["auth_title"]}

{t["auth_text"]}

```console
pybynix login
pybynix doctor
pybynix balances
```

```python
{AUTH}
```

## {SECTION_EMOJIS["execution_title"]} {t["execution_title"]}

{t["execution_text"]}

```python
{EXECUTION}
```

## {SECTION_EMOJIS["network_title"]} {t["network_title"]}

{t["network_text"]}

```python
{NETWORK}
```

## {SECTION_EMOJIS["cli_title"]} {t["cli_title"]}

{t["cli_text"]}

```console
pybynix stream --symbol "EUR/USD OTC" --interval 15s --count 5
pybynix candles --symbol "EUR/USD OTC" --count 100 --output candles.csv
pybynix sentiment --symbol "EUR/USD OTC"
pybynix domains
```

## {SECTION_EMOJIS["docs_title"]} {t["docs_title"]}

{t["docs_text"]}

- [API reference]({rel("docs/API.md")})
- [Architecture]({rel("docs/ARCHITECTURE.md")})
- [Protocol]({rel("docs/PROTOCOL.md")})
- [Authentication]({rel("docs/AUTHENTICATION.md")})
- [Examples]({rel("examples")})
- [Recovery and networking]({rel("docs/RECOVERY.md")})
- [Automation boundary]({rel("docs/AUTOMATION.md")})
- [Feature and fallback matrix]({rel("docs/FEATURE_MATRIX.md")})
- [Launch checklist]({rel("docs/LAUNCH_CHECKLIST.md")})
- [Repository map]({rel("docs/REPOSITORY_MAP.md")})
- [Troubleshooting]({rel("docs/TROUBLESHOOTING.md")})

## {SECTION_EMOJIS["validation_title"]} {t["validation_title"]}

{t["validation_text"]} [Validation]({rel("docs/VALIDATION.md")}) · [Benchmarks]({rel("benchmarks")})

## {SECTION_EMOJIS["paper_title"]} {t["paper_title"]}

{t["paper_text"]}

> 📄 **[{PAPER_TITLE}]({PAPER_URL})**
>
> C. M. Jubayer Hossain Bappy · Preprint · CC BY 4.0 · [DOI {PAPER_DOI}]({PAPER_URL})

<details>
<summary><b>BibTeX</b></summary>

```bibtex
@misc{{bappy2026pybynix,
  author    = {{{{C. M.}}, Jubayer Hossain Bappy}},
  title     = {{PyBynix: Capability-Aware Asynchronous Integration and Safe
               Execution Architecture for Bynix Broker}},
  year      = {{2026}},
  publisher = {{Zenodo}},
  doi       = {{{PAPER_DOI}}},
  url       = {{{PAPER_URL}}},
  note      = {{Preprint}}
}}
```

</details>

## {SECTION_EMOJIS["contribute_title"]} {t["contribute_title"]}

{t["contribute_text"]} [Contributing]({rel("CONTRIBUTING.md")}) · [Security]({rel("SECURITY.md")}) · [MIT]({rel("LICENSE")})

<p align="center"><strong>C. M. Jubayer Hossain Bappy</strong> · PyBynix</p>
'''
        path.write_text(body, encoding="utf-8")
        badge = ROOT / f"assets/languages/{code}.svg"
        badge.parent.mkdir(parents=True, exist_ok=True)
        badge.write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="58" height="28" '
            f'viewBox="0 0 58 28" role="img"><title>{html.escape(t["name"])}</title>'
            '<rect width="58" height="28" rx="5" fill="#064b31"/>'
            '<rect x="1" y="1" width="56" height="26" rx="4" fill="none" stroke="#cda719"/>'
            f'<text x="29" y="18.5" text-anchor="middle" font-family="Arial,sans-serif" '
            f'font-size="11" font-weight="bold" fill="white">{code.upper()}</text></svg>',
            encoding="utf-8",
        )
    print(f"Generated {len(LANGUAGES)} README editions and language badges")


if __name__ == "__main__":
    generate()
