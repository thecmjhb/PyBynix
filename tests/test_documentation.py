import ast
import json
import re
from pathlib import Path
from xml.etree import ElementTree

import pytest

ROOT = Path(__file__).resolve().parents[1]
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
SECTION_KEYS = (
    "features_title",
    "install",
    "quickstart",
    "auth_title",
    "execution_title",
    "network_title",
    "cli_title",
    "docs_title",
    "validation_title",
    "paper_title",
    "contribute_title",
)


@pytest.mark.parametrize("language", LANGUAGES)
def test_readme_is_complete_has_valid_examples_and_local_links(language):
    path = ROOT / (
        "README.md"
        if language == "en"
        else "README.bn.md"
        if language == "bn"
        else f"docs/i18n/README.{language}.md"
    )
    text = path.read_text(encoding="utf-8")
    translation = json.loads(
        (ROOT / f"docs/i18n/locales/{language}.json").read_text(encoding="utf-8")
    )
    assert translation["tagline"] in text
    assert len(translation["features"]) == 10
    assert all(feature in text for feature in translation["features"])
    headings = [translation[key] for key in SECTION_KEYS]
    assert len(headings) == 11
    assert len(re.findall(r"^## ", text, re.MULTILINE)) == 11
    assert all(f"## {heading}" in text for heading in headings)
    assert "0.1.0a1" not in text
    for code in re.findall(r"```python\n(.*?)```", text, re.DOTALL):
        ast.parse(code)
    links = re.findall(r'(?:href|src)="([^"]+)"', text)
    links += re.findall(r"\]\(([^)]+)\)", text)
    for target in links:
        if target.startswith(("https://", "http://", "#")):
            continue
        assert (path.parent / target.split("#")[0]).resolve().exists(), target
    badge = ROOT / f"assets/languages/{language}.svg"
    assert ElementTree.parse(badge).getroot().tag.endswith("svg")


def test_translation_set_and_readme_set_agree():
    locales = {p.stem for p in (ROOT / "docs/i18n/locales").glob("*.json")}
    assert locales == set(LANGUAGES)
    assert len(list((ROOT / "docs/i18n").glob("README.*.md"))) == 18


def test_examples_are_import_safe_and_syntactically_valid():
    for path in (ROOT / "examples").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert any(isinstance(node, ast.If) for node in tree.body), path.name
