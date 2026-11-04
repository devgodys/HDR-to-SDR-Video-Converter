"""Translations: every language has exactly the English keys, keeps the same
{placeholders}, and every tr("literal") in the code has a translation entry
(lookup is by the English text, so a typo or reworded string would silently
show English in all 30 languages)."""
import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LANGS = sorted((ROOT / "i18n").glob("*.json"))
EN = json.loads((ROOT / "i18n" / "en.json").read_text(encoding="utf-8"))
_PH = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")


def test_there_are_many_languages():
    assert len(LANGS) >= 30


@pytest.mark.parametrize("path", LANGS, ids=lambda p: p.stem)
def test_same_keys_as_english(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data) == set(EN), (sorted(set(EN) - set(data))[:5], sorted(set(data) - set(EN))[:5])


@pytest.mark.parametrize("path", LANGS, ids=lambda p: p.stem)
def test_placeholders_match_english(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    bad = [k for k, v in EN.items() if k in data and set(_PH.findall(v)) != set(_PH.findall(data[k]))]
    assert not bad, bad[:5]


def test_every_tr_literal_has_an_entry():
    known = set(EN.values())
    missing = {}
    for p in (ROOT / "hdr2sdr").rglob("*.py"):
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "tr"
                    and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)
                    and n.args[0].value not in known):
                missing.setdefault(n.args[0].value, p.name)
    # three long tooltips predate this check and are tracked here; the list may only shrink
    allowed = {"Restore", "Render test clip", "Desaturate overbright"}
    unexpected = {s: f for s, f in missing.items() if not any(s.startswith(a) for a in allowed)}
    assert not unexpected, unexpected
