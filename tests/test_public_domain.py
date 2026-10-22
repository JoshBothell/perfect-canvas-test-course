"""Public Domain Academy specifics: commercial-safety evidence, paths, names and edge cases only this theme has."""

from __future__ import annotations

import csv
import json
from collections import Counter

import pytest

from conftest import ANCHOR, SEED

from cc_gen.build import build
from cc_gen.theme import load_theme

CC0 = "https://creativecommons.org/publicdomain/zero/1.0/"


@pytest.fixture(scope="module")
def pda(tmp_path_factory):
    pkg, course = build("public-domain", anchor=ANCHOR, images_mode="generated", seed=SEED, log=lambda *_: None)
    out = tmp_path_factory.mktemp("pda") / "pda.imscc"
    pkg.write_zip(out)
    return out, pkg, course


def test_theme_config():
    t = load_theme("public-domain")
    assert t.namespace == "pda", "never change: migration ids derive from it"
    assert t.output_name == "public-domain-academy.imscc"
    assert t.config["commercial"] is True
    assert t.config["title"] == "Public Domain Academy"


def test_catalog_licence_evidence():
    t = load_theme("public-domain")
    with open(t.root / "data" / "collection.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 386
    assert Counter(r["era"] for r in rows) == {"1": 151, "2": 100, "3": 135}
    for r in rows:
        assert r["license"] == "CC0 1.0" and r["license_url"] == CC0, r["id"]
        assert r["source"] in ("aic", "met") and r["pd_flag_field"] in ("is_public_domain", "isPublicDomain")
        assert r["object_url"].startswith("https://") and r["image_url"].startswith("https://"), r["id"]
        assert r["retrieved"] and int(r["year"]) <= 1930
    assert len({r["title"].casefold() for r in rows}) == 386, "titles are identify-question choices: must be unique"
    assert len({r["image_url"] for r in rows}) == 386, "some museum objects share one image: never pick two of them"

    with open(t.root / "data" / "provenance.jsonl", encoding="utf-8") as f:
        prov = [json.loads(line) for line in f]
    assert [p["id"] for p in prov] == list(range(1, 387))
    for p in prov:
        assert "description" not in p["fields"], "AIC `description` is CC-BY, not CC0: never store it"
        assert p["fields"].get("is_public_domain", p["fields"].get("isPublicDomain")) is True, p["id"]


def test_paths_and_edge_cases(pda):
    _, pkg, c = pda
    assert len(c.catalog) == 386 and c.catalog[0]["image"].startswith("images/collection/era1/001-")
    assert "web_resources/documents/Domaine public – Œuvres libres.pdf" in pkg.files
    assert "web_resources/Instructor's Archive/answer-key.pdf" in pkg.files
    assert pkg.files["wiki_content/the-full-collection-001-386.html"].count(b"<tr>") >= 387
    assert len(c.pages["max-length-title"]["title"]) == 255


def test_identify_question_names(pda):
    _, _, c = pda
    bank = c.banks["name-that-work-bank"]
    assert {q["name"] for q in bank["questions"]} == {f"Mystery work #{n:03d}" for n in range(1, 387)}
    assert c.quizzes["ntw-marathon"]["questions"][0]["pick"] == 50
    assert all(p.startswith("images/mystery/") for p in c.masked)
