"""Unit tests for theme-agnostic pieces: dates, formulas, id namespaces, theme loading, matrix signatures."""

from __future__ import annotations

import shutil
from datetime import date
from zoneinfo import ZoneInfo

import pytest

from cc_gen.content import load_course
from cc_gen.dates import DateResolver
from cc_gen.ids import mid, use_namespace
from cc_gen.matrix import compare
from cc_gen.theme import ThemeError, load_theme
from cc_gen.writers.qti import _eval_formula

from conftest import ANCHOR, REFERENCE_THEME, SEED


def test_dst_and_utc():
    d = DateResolver(anchor=ANCHOR, tz=ZoneInfo("America/Denver"))
    assert d.next_dst_transition() == date(2026, 11, 1)
    assert d.utc("+0d@23:59") == "2026-10-22T05:59:00"  # MDT is UTC-6
    assert d.utc("+15d@23:59") == "2026-11-06T06:59:00"  # MST is UTC-7
    assert d.utc(None) is None


def test_formula_eval():
    assert _eval_formula("((2*L/5+2)*P*A/D)/50+2", {"L": 50, "P": 100, "A": 100, "D": 100}) == pytest.approx(46.0)
    with pytest.raises(Exception):
        _eval_formula("__import__('os')", {})


def test_ids_are_namespaced_and_scoped():
    with pytest.raises(RuntimeError):
        mid("page", "x")
    with use_namespace("aaa"):
        a = mid("page", "x")
        with use_namespace("bbb"):
            b = mid("page", "x")
        assert mid("page", "x") == a
    assert a != b and a.startswith("g") and len(a) == 33


def test_unknown_theme():
    with pytest.raises(ThemeError, match="unknown theme"):
        load_theme("no-such-theme")


def _copy_theme(tmp_path, name="clone", namespace="cln"):
    """Copy the reference theme under a new name/namespace (a stand-in for a new theme)."""
    src = load_theme(REFERENCE_THEME).root
    dst = tmp_path / name
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("files", "__pycache__", "lti.yaml"))
    cfg = (dst / "theme.yaml").read_text()
    cfg = cfg.replace(f"name: {REFERENCE_THEME}", f"name: {name}").replace("namespace: pta", f"namespace: {namespace}")
    (dst / "theme.yaml").write_text(cfg)
    return dst


def _load(ref):
    t = load_theme(ref)
    return load_course(t, anchor=ANCHOR, lti_path=t.lti_example, seed=SEED)


def test_matrix_compare_ignores_content_but_catches_settings(tmp_path):
    clone = _copy_theme(tmp_path)
    ref = _load(REFERENCE_THEME)

    # different namespace + retitled/reworded content: still the same test matrix
    mod = clone / "modules" / "01-gen1-kanto.yaml"
    text = mod.read_text()
    mod.write_text(text.replace('title: "Choose Your Starter"', 'title: "Pick a Painting"')
                       .replace("Pick Bulbasaur, Charmander or Squirtle", "Pick any painting"))
    c = _load(clone)
    assert c.assignments["choose-your-starter"]["id"] != ref.assignments["choose-your-starter"]["id"], "namespace changes ids"
    assert compare(ref, c) == []

    # a changed Canvas setting is caught
    mod.write_text(mod.read_text().replace("allowed_extensions: [png, jpg, jpeg, pdf]", "allowed_extensions: [png]"))
    diff = compare(ref, _load(clone))
    assert any("allowed_extensions" in line for line in diff), diff


def test_forbidden_terms_whole_word_with_exceptions(tmp_path):
    import zipfile

    from cc_gen.validate import forbidden_terms

    p = tmp_path / "x.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("wiki_content/a.html", "<p>A Persian rug, a golem legend, Abraham Lincoln, and pikachu.</p>")
        z.writestr("web_resources/Kanto map.png", b"\x89PNG")
    hits = forbidden_terms(zipfile.ZipFile(p), load_theme(REFERENCE_THEME))
    assert sorted(hits) == ["forbidden: pokemon term found in package: 'Kanto'",
                            "forbidden: pokemon term found in package: 'Pikachu'"], hits  # not Persian/Golem/Abra
    assert forbidden_terms(zipfile.ZipFile(p), load_theme(REFERENCE_THEME), allow=["Pikachu", "Kanto"]) == []
