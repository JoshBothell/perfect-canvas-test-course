"""Themes: everything that is content (not Canvas mechanics) lives in ``themes/<name>/``.

A theme directory holds:

  theme.yaml        declarative config (namespace, catalog, images, identify questions, seeder keys)
  theme.py          OPTIONAL code hooks (see HOOKS below); loaded by file path, not imported as a package
  course.yaml       course settings, groups, schemes, rubrics, outcomes, events, announcements, files meta
  modules/*.yaml    modules + inline items (filename order = module order)
  pages/*.html      Jinja page bodies
  quizzes/*.yaml    quizzes and banks
  data/  files/     catalog CSV / static files copied verbatim into the course
  lti.example.yaml  LTI tool config used by `cc matrix` and the LTI test build (lti.yaml = real one, gitignored)
  roster.yaml       people / sections / groups / overrides / activity for the seeder

``cc_gen`` never mentions a theme by name; the default theme name only appears in ``DEFAULT_THEME`` below.
A theme can claim CLI shortcut flags with theme.yaml ``cli_flags`` (e.g. ``[-p, -P]`` = ``--theme <name>``).
"""

from __future__ import annotations

import csv
import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import yaml

from .ids import slugify

REPO = Path(__file__).resolve().parent.parent
THEMES_DIR = REPO / "themes"
DEFAULT_THEME = os.environ.get("CC_THEME", "public-domain")

# Optional functions a theme.py may define (all receive plain dicts / the Course):
HOOKS = {
    "load_catalog": "(theme) -> list[dict]: rows, instead of reading catalog.file as CSV",
    "enrich_entry": "(entry) -> None: add derived fields to a catalog entry (runs before the image path is formatted)",
    "placeholder_image": "(entry, hide_name: bool) -> PNG bytes: art used when no fetched image exists",
    "mask_image": "(png: bytes, entry) -> PNG bytes: masked image for identify questions (overrides images.mask)",
    "jinja_globals": "(course, renderer) -> dict: extra template helpers / data",
    "assets": "(course) -> dict[path, bytes]: theme-generated course files (PDFs, CSVs, badges, banners...)",
}


class ThemeError(Exception):
    pass


@dataclass
class ImageSource:
    """Where catalog images are fetched from. ``url`` is a str.format template over entry fields,
    e.g. ``https://host/img/{dex:03d}.png`` or just ``{image_url}`` for a per-row CSV column.
    Any image Pillow decodes is accepted; non-PNG or oversized images are converted to PNG before caching."""

    url: str
    cache: str
    label: str = "remote images"
    user_agent: str = "canvas-test-course-fixture/0.1 (+internal test course build)"
    max_size: int | None = None  # downscale fetched images to this many px on the long side (PNG output)

    def url_for(self, entry: dict) -> str | None:
        try:
            return self.url.format(**entry) or None
        except (KeyError, IndexError, ValueError):
            return None


@dataclass
class Theme:
    name: str
    root: Path
    config: dict
    module: ModuleType | None = None

    # ---------------------------------------------------------------- config
    @property
    def namespace(self) -> str:
        return self.config["namespace"]

    @property
    def output_name(self) -> str:
        return self.config.get("output", f"{self.name}.imscc")

    @property
    def matrix_doc(self) -> Path:
        return REPO / self.config.get("matrix_doc", f"docs/test-matrix/{self.name}.md")

    @property
    def images(self) -> dict:
        return self.config.get("images") or {}

    @property
    def identify(self) -> dict:
        return self.config.get("identify") or {}

    @property
    def seeder(self) -> dict:
        return self.config.get("seeder") or {}

    @property
    def image_source(self) -> ImageSource | None:
        src = self.images.get("source")
        return ImageSource(**src) if src else None

    @property
    def lti_example(self) -> Path:
        return self.root / "lti.example.yaml"

    @property
    def roster_path(self) -> Path:
        return self.root / "roster.yaml"

    def hook(self, name: str):
        return getattr(self.module, name, None) if self.module else None

    def fmt(self, template: str, entry: dict) -> str:
        """Format a theme.yaml string template with a catalog entry's fields."""
        return template.format(**entry)

    # --------------------------------------------------------------- catalog
    def catalog(self) -> list[dict]:
        """Entries with at least: id, name, group (unit 1/2/3), slug, image (course-files path).

        Fresh dicts on every call, so templates/generators can't leak mutations between builds."""
        spec = self.config.get("catalog")
        if not spec:
            return []
        loader = self.hook("load_catalog")
        if loader:
            rows = loader(self)
        else:
            with open(self.root / spec["file"], encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        enrich = self.hook("enrich_entry")
        for r in rows:
            for k in spec.get("int_fields", []):
                r[k] = int(r[k])
            r["id"] = r[spec.get("id", "id")]
            r["name"] = r[spec.get("name", "name")]
            r["group"] = int(r[spec.get("group", "group")])
            r["slug"] = slugify(r["name"])
            if enrich:
                enrich(r)
            r["image"] = spec["image"].format(**r)
        ids = [r["id"] for r in rows]
        if len(ids) != len(set(ids)):
            raise ThemeError(f"theme {self.name}: duplicate catalog ids")
        return rows


def available() -> list[str]:
    return sorted(p.name for p in THEMES_DIR.iterdir() if (p / "theme.yaml").exists()) if THEMES_DIR.exists() else []


def theme_flags() -> dict[str, list[str]]:
    """``{theme name: its theme.yaml cli_flags}`` for every theme that declares any (reads YAML only, not theme.py)."""
    out = {}
    for name in available():
        with open(THEMES_DIR / name / "theme.yaml", encoding="utf-8") as f:
            flags = (yaml.safe_load(f) or {}).get("cli_flags") or []
        if flags:
            out[name] = [str(x) for x in flags]
    return out


def load_theme(name_or_path: str | Path | None = None) -> Theme:
    """``name_or_path``: a directory under themes/, or a path to any theme directory."""
    ref = name_or_path or DEFAULT_THEME
    root = Path(ref)
    if not (root / "theme.yaml").exists():
        root = THEMES_DIR / str(ref)
    if not (root / "theme.yaml").exists():
        raise ThemeError(f"unknown theme {ref!r}; available: {', '.join(available()) or '(none)'}")
    root = root.resolve()
    with open(root / "theme.yaml", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    for req in ("name", "namespace"):
        if not config.get(req):
            raise ThemeError(f"{root / 'theme.yaml'}: missing required key {req!r}")
    module = None
    if (root / "theme.py").exists():
        spec = importlib.util.spec_from_file_location(f"cc_theme_{config['name'].replace('-', '_')}", root / "theme.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return Theme(name=config["name"], root=root, config=config, module=module)
