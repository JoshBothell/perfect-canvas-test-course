"""Pokémon-theme specifics: golden output (refactor guard), paths, names and helpers only this theme has."""

from __future__ import annotations

import hashlib

import pytest

from conftest import ANCHOR, SEED

from cc_gen.build import build
from cc_gen.theme import load_theme

# sha256 of `cc build --theme pokemon --images generated --seed 151 --anchor 2026-10-21` (no LTI).
# This guards refactors of the theme-agnostic code: the Pokémon package must stay byte-identical.
# If you INTENTIONALLY change Pokémon content or output, rebuild and update this digest in the same change.
GOLDEN_SHA256 = "b5de713bfc39ae9b83398fe90cf68c078bd0679ce3d0de4d42be7ae797be7c75"


@pytest.fixture(scope="module")
def poke(tmp_path_factory):
    pkg, course = build("pokemon", anchor=ANCHOR, images_mode="generated", seed=SEED, log=lambda *_: None)
    out = tmp_path_factory.mktemp("poke") / "p.imscc"
    pkg.write_zip(out)
    return out, pkg, course


def test_golden_output(poke):
    digest = hashlib.sha256(poke[0].read_bytes()).hexdigest()
    assert digest == GOLDEN_SHA256, f"Pokémon package changed: {digest}"


def test_theme_config():
    t = load_theme("pokemon")
    assert t.namespace == "pta", "never change: migration ids derive from it"
    assert t.output_name == "pokemon-trainer-academy.imscc"
    assert not t.config.get("commercial")


def test_catalog_and_paths(poke):
    _, pkg, c = poke
    assert len(c.catalog) == 386 and c.catalog[0]["name"] == "Bulbasaur" and c.catalog[0]["bst"] == 318
    assert c.catalog[0]["image"] == "images/sprites/gen1/001-bulbasaur.png"
    assert pkg.files["wiki_content/national-pokedex-001-386.html"].count(b"<tr>") >= 387
    assert "web_resources/images/types/fire.png" in pkg.files
    assert "web_resources/documents/Pokédex – Ōkido notes.pdf" in pkg.files


def test_silhouette_question_names(poke):
    _, _, c = poke
    bank = c.banks["whos-that-pokemon-bank"]
    assert {q["name"] for q in bank["questions"]} == {f"Silhouette #{n:03d}" for n in range(1, 387)}
    assert c.quizzes["wtp-marathon"]["questions"][0]["pick"] == 50
    assert all(p.startswith("images/mystery/") for p in c.masked)
