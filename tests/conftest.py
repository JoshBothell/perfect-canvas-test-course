"""Shared fixtures. Every contract test runs once per theme in themes/ (offline, fixed seed + anchor)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from cc_gen.build import build
from cc_gen.theme import available, load_theme

ROOT = Path(__file__).resolve().parent.parent
ANCHOR = date(2026, 10, 21)
SEED = 151
REFERENCE_THEME = "pokemon"  # every other theme must have the same test matrix as this one
THEMES = available()


@pytest.fixture(scope="session", params=THEMES)
def theme(request):
    return load_theme(request.param)


@pytest.fixture(scope="session")
def built(theme, tmp_path_factory):
    pkg, course = build(theme, anchor=ANCHOR, images_mode="generated", seed=SEED, log=lambda *_: None)
    out = tmp_path_factory.mktemp(f"dist-{theme.name}") / "course.imscc"
    pkg.write_zip(out)
    return out, pkg, course


@pytest.fixture(scope="session")
def built_lti(theme, tmp_path_factory):
    pkg, course = build(theme, anchor=ANCHOR, lti_path=theme.lti_example, images_mode="generated", seed=SEED, log=lambda *_: None)
    out = tmp_path_factory.mktemp(f"dist-{theme.name}") / "course-lti.imscc"
    pkg.write_zip(out)
    return out, pkg, course
