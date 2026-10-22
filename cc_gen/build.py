"""Orchestrates theme -> Course -> Package -> .imscc."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from . import assets, images
from .content import Course, Renderer, load_course
from .ids import use_namespace
from .package import Package
from .theme import Theme, load_theme
from .writers.items import write_assignments, write_discussions, write_lti_tools, write_pages, write_web_links
from .writers.manifest import write_files, write_manifest
from .writers.qti import write_banks, write_quizzes
from .writers.settings import write_course_settings


class BuildError(Exception):
    pass


def build(theme: Theme | str | Path | None = None, anchor: date | None = None, lti_path: Path | None = None,
          images_mode: str = "auto", refresh_images: bool = False, image_cache: Path | None = None,
          log=print, seed: int | None = None, art_quality: str = "palette") -> tuple[Package, Course]:
    """``theme``: Theme, name under themes/, or a theme directory (None = default theme)."""
    theme = theme if isinstance(theme, Theme) else load_theme(theme)
    with use_namespace(theme.namespace):
        return _build(theme, anchor, lti_path, images_mode, refresh_images, image_cache, log, seed, art_quality)


def _build(theme, anchor, lti_path, images_mode, refresh_images, image_cache, log, seed, art_quality):
    course = load_course(theme, anchor=anchor, lti_path=lti_path, seed=seed)
    log(f"theme: {theme.name}; seed: {course.seed} (rebuild this exact course with --theme {theme.name} --seed {course.seed})")
    # fetch images before rendering: alt text says whether an image was fetched or is a placeholder
    art = images.fetch_all(course.catalog, theme.image_source, mode=images_mode, refresh=refresh_images, cache_dir=image_cache)
    log(art.summary(len(course.catalog)))
    if art.mode == "required" and art.failed:
        raise BuildError(f"--images required: {len(art.failed)} catalog images could not be fetched "
                         f"(e.g. {', '.join(f'{d} {r}' for d, r in sorted(art.failed.items())[:5])})")
    course.fetched_images = art.fetched
    course.art_quality = art_quality
    r = Renderer(course)
    pkg = Package()

    # content first: rendering HTML registers referenced files on course.files
    write_pages(pkg, course, r)
    write_assignments(pkg, course, r)
    write_discussions(pkg, course, r)
    write_banks(pkg, course, r)
    write_quizzes(pkg, course, r)
    write_web_links(pkg, course)
    write_lti_tools(pkg, course)
    write_course_settings(pkg, course, r)  # after content: files_meta needs every referenced file

    missing = write_files(pkg, course, assets.generate(course))
    if missing:
        raise BuildError("referenced files that were never generated:\n  " + "\n  ".join(missing))
    _check_refs(course)
    write_manifest(pkg, course)
    return pkg, course


def _check_refs(course: Course) -> None:
    known = {
        "page": course.pages, "assignment": course.assignments, "quiz": course.quizzes,
        "discussion": course.discussions, "file": course.files,
    }
    item_ids = {it["id"] for m in course.modules for it in m["items"]}
    bad = []
    for kind, key, where in course.refs:
        if kind == "module_item":
            mk, ik = key.split("/", 1)
            if course.module_item_id(mk, ik) not in item_ids:
                bad.append(f"{where}: module item {key}")
        elif key not in known[kind]:
            bad.append(f"{where}: {kind} {key!r}")
    if bad:
        raise BuildError("dangling references:\n  " + "\n  ".join(sorted(set(bad))))
