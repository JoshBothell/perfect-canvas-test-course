"""CLI theme selection: the default theme, per-theme shortcut flags (theme.yaml cli_flags) and `cc themes`."""

from __future__ import annotations

import pytest

from cc_gen import theme as theme_mod
from cc_gen.cli import build_parser, main
from cc_gen.theme import DEFAULT_THEME, available, theme_flags


def test_default_theme_is_the_commercial_one():
    src = open(theme_mod.__file__, encoding="utf-8").read()
    assert 'os.environ.get("CC_THEME", "public-domain")' in src
    assert build_parser().parse_args(["build"]).theme == DEFAULT_THEME


@pytest.mark.parametrize("flag", ["-p", "-P", "--pokemon"])
@pytest.mark.parametrize("cmd", ["build", "matrix", "validate", "seed", "verify", "teardown"])
def test_pokemon_shortcut_flags(cmd, flag):
    assert theme_flags()["pokemon"] == ["-p", "-P", "--pokemon"]
    assert build_parser().parse_args([cmd, flag]).theme == "pokemon"


def test_shortcut_and_theme_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["build", "-p", "--theme", "public-domain"])


def test_seeder_commands_default_to_config():
    assert build_parser().parse_args(["seed"]).theme is None


def test_themes_lists_every_theme(capsys):
    assert main(["themes"]) == 0
    out = capsys.readouterr().out
    assert set(available()) >= {"pokemon", "public-domain"}
    assert all(name in out for name in available())
    assert "-p -P --pokemon" in out
