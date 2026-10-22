"""``cc`` command line: build / validate / compare themed cartridges, and seed / verify / teardown a live course.

Every command takes ``--theme NAME|PATH`` (default: $CC_THEME or the default theme), or a theme's shortcut
flag from its theme.yaml ``cli_flags`` (e.g. ``-p``); ``cc themes`` lists them.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .theme import DEFAULT_THEME, REPO, ThemeError, available, load_theme, theme_flags

ROOT = REPO


def default_out(theme_ref) -> Path:
    return ROOT / "dist" / load_theme(theme_ref).output_name


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="cc", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    shortcuts = theme_flags()

    def theme_arg(p, default=DEFAULT_THEME, help=f"theme name under themes/ or a theme directory (default: {DEFAULT_THEME})"):
        g = p.add_mutually_exclusive_group()
        g.add_argument("--theme", default=default, help=help)
        for name, flags in shortcuts.items():
            g.add_argument(*flags, dest="theme", action="store_const", const=name, help=f"shortcut for --theme {name}")

    sp.add_parser("themes", help="list available themes")

    b = sp.add_parser("build", help="generate the .imscc for a theme")
    theme_arg(b)
    b.add_argument("--anchor", type=date.fromisoformat, default=None, help="date all relative dates hang off (default: today)")
    b.add_argument("--lti", type=Path, default=None, help="LTI tool config (default: <theme>/lti.yaml if present)")
    b.add_argument("--out", type=Path, default=None, help="default: dist/<theme output name>")
    b.add_argument("--no-validate", action="store_true")
    b.add_argument("--images", "--sprites", dest="images", choices=["auto", "required", "official", "generated"], default="auto",
                   help="auto: fetch catalog images from the theme's source, fall back to generated placeholders per entry (default); "
                        "required (alias: official): fail if any can't be fetched; generated: offline, never fetch")
    b.add_argument("--seed", type=int, default=None, help="seed for randomized content (default: new random seed per build)")
    b.add_argument("--art-quality", choices=["palette", "original"], default="palette",
                   help="palette: 256-color PNGs, ~79%% smaller, visually identical (default); original: untouched fetched files")
    b.add_argument("--refresh-images", "--refresh-sprites", dest="refresh_images", action="store_true",
                   help="ignore build/cache/<source> and re-download")

    mx = sp.add_parser("matrix", help="regenerate the theme's test-matrix doc, or --compare two themes' matrices")
    theme_arg(mx)
    mx.add_argument("--anchor", type=date.fromisoformat, default=None)
    mx.add_argument("--lti", type=Path, default=None, help="include the LTI module (default: <theme>/lti.example.yaml)")
    mx.add_argument("--out", type=Path, default=None, help="default: theme.yaml matrix_doc")
    mx.add_argument("--compare", metavar="OTHER_THEME", default=None,
                    help="diff the theme-independent signatures of --theme and OTHER_THEME; exit 1 if they differ")
    mx.add_argument("--signature", action="store_true", help="print the theme-independent signature instead of writing the doc")
    mx.add_argument("--ignore-tests", action="store_true", help="leave `tests:` annotations out of the signature")

    v = sp.add_parser("validate", help="static checks on a built .imscc")
    theme_arg(v)
    v.add_argument("path", type=Path, nargs="?", default=None, help="default: dist/<theme output name>")
    v.add_argument("--hygiene-against", type=Path, default=None, help="reference export whose strings must NOT appear")
    v.add_argument("--forbid-theme", action="append", default=[], metavar="THEME",
                   help="fail if THEME's brand_terms or catalog names appear (repeatable; use for commercial themes)")

    for name, helptext in (("seed", "import + seed a live Canvas course via REST"),
                           ("verify", "assert a seeded course matches expectations"),
                           ("teardown", "delete the seeded course and <sis_prefix>-* users")):
        s = sp.add_parser(name, help=helptext)
        theme_arg(s, default=None, help="default: config.toml `theme`, else the default theme")
        s.add_argument("--config", type=Path, default=ROOT / "seeder" / "config.toml")
        if name == "seed":
            s.add_argument("--package", type=Path, default=None, help="default: dist/<theme output name>")
            s.add_argument("--new-quizzes", action="store_true", help="import Classic quizzes as New Quizzes")
            s.add_argument("--skip-import", action="store_true", help="seed people/activity into an already-imported course")
            s.add_argument("--manifest-out", type=Path, default=None, help="default: dist/seed-manifest-<theme>.json")
        if name in ("verify", "teardown"):
            s.add_argument("--manifest", type=Path, default=None, help="default: dist/seed-manifest-<theme>.json")
        if name == "teardown":
            s.add_argument("--yes", action="store_true", help="don't prompt")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except ThemeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


def _run(args) -> int:
    if args.cmd == "themes":
        for name in available():
            t = load_theme(name)
            flags = " ".join(t.config.get("cli_flags") or []) or ("(default)" if name == DEFAULT_THEME else "--theme " + name)
            print(f"{name:20} {flags:16} namespace={t.namespace:6} commercial={str(bool(t.config.get('commercial'))).lower():5}"
                  f"  {t.config.get('title', '')}")
        return 0

    if args.cmd == "build":
        from .build import BuildError, build
        from .content import ContentError
        from .validate import validate

        out = args.out or default_out(args.theme)
        try:
            pkg, course = build(args.theme, anchor=args.anchor, lti_path=args.lti, images_mode=args.images,
                                refresh_images=args.refresh_images, seed=args.seed, art_quality=args.art_quality)
        except (BuildError, ContentError) as e:
            print(f"build failed: {e}", file=sys.stderr)
            return 1
        pkg.write_zip(out)
        size = out.stat().st_size / 1024 / 1024
        print(f"wrote {out} ({size:.1f} MB, {len(pkg.files)} files, anchor {course.dates.anchor}, tz {course.dates.tz.key}"
              f"{', LTI on' if course.lti else ''})")
        if not args.no_validate:
            return _report(validate(out))
        return 0

    if args.cmd == "matrix":
        from .content import load_course
        from .matrix import compare, render, signature

        def course_for(ref):
            t = load_theme(ref)
            return load_course(t, anchor=args.anchor, lti_path=args.lti or t.lti_example, seed=0)

        course = course_for(args.theme)
        if args.compare:
            diff = compare(course, course_for(args.compare), ignore_tests=args.ignore_tests)
            print("\n".join(diff) if diff else f"identical test matrix: {args.theme} == {args.compare}")
            return 1 if diff else 0
        if args.signature:
            print("\n".join(signature(course, ignore_tests=args.ignore_tests)))
            return 0
        out = args.out or course.theme.matrix_doc
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(course), encoding="utf-8")
        print(f"wrote {out}")
        return 0

    if args.cmd == "validate":
        from .validate import validate

        theme = load_theme(args.theme)
        return _report(validate(args.path or default_out(args.theme), args.hygiene_against,
                                forbid_themes=[load_theme(t) for t in args.forbid_theme],
                                allow_terms=theme.config.get("allow_terms") or ()))

    from seeder import seed as seeder

    return seeder.run(args)


def _report(rep) -> int:
    for w in rep.warnings:
        print(f"warning: {w}")
    if rep.stale_xsd:
        print(f"note: ignored XSD complaints about {sorted(rep.stale_xsd)} (Canvas's exporter emits these; its XSD is stale)")
    for e in rep.errors:
        print(f"ERROR: {e}")
    print(json.dumps(rep.stats, indent=2))
    print("validation " + ("passed" if rep.ok else f"FAILED ({len(rep.errors)} errors)"))
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
