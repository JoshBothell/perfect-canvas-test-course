"""Load a theme directory (YAML + HTML templates) into a normalized in-memory course model.

Every object has a ``key`` (author-facing, stable) and an ``id`` (migration identifier derived
from kind + key). Cross-references in YAML and HTML use keys; they are resolved to
``$WIKI_REFERENCE$`` / ``$CANVAS_OBJECT_REFERENCE$`` / ``$IMS-CC-FILEBASE$`` links here, and
every reference is recorded so ``validate`` can prove nothing dangles.
"""

from __future__ import annotations

import hashlib
import random
import secrets
import urllib.parse
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from jinja2 import Environment, StrictUndefined
from markupsafe import Markup, escape

from .dates import DateResolver
from .ids import mid, slugify, use_namespace
from .theme import Theme, load_theme

WIKI_TOKEN = "$WIKI_REFERENCE$"
OBJECT_TOKEN = "$CANVAS_OBJECT_REFERENCE$"
COURSE_TOKEN = "$CANVAS_COURSE_REFERENCE$"
FILE_TOKEN = "$IMS-CC-FILEBASE$"

ITEM_TYPES = {"subheader", "page", "assignment", "quiz", "discussion", "file", "url", "external_tool"}


class ContentError(Exception):
    pass


@dataclass
class Course:
    theme: Theme
    root: Path
    settings: dict
    dates: DateResolver
    seed: int = 0
    modules: list[dict] = field(default_factory=list)
    pages: dict[str, dict] = field(default_factory=dict)
    assignments: dict[str, dict] = field(default_factory=dict)
    quizzes: dict[str, dict] = field(default_factory=dict)
    banks: dict[str, dict] = field(default_factory=dict)
    discussions: dict[str, dict] = field(default_factory=dict)
    announcements: dict[str, dict] = field(default_factory=dict)
    files: dict[str, dict] = field(default_factory=dict)  # path -> file meta (data filled by assets)
    rubrics: dict[str, dict] = field(default_factory=dict)
    outcomes: dict[str, dict] = field(default_factory=dict)
    grading_standards: dict[str, dict] = field(default_factory=dict)
    assignment_groups: dict[str, dict] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    lti: dict | None = None
    catalog: list[dict] = field(default_factory=list)  # theme entries: id, name, group, slug, image + theme fields
    refs: list[tuple[str, str, str]] = field(default_factory=list)  # (kind, key, where)
    masked: dict[str, object] = field(default_factory=dict)  # masked identify image path -> catalog id
    fetched_images: dict[object, bytes] = field(default_factory=dict)  # catalog id -> image from the theme's source
    art_quality: str = "palette"  # "palette" (256-color PNG) or "original" (untouched fetched bytes)

    # ---------------------------------------------------------------- lookups
    def module_item_id(self, module_key: str, item_key: str) -> str:
        return mid("module_item", f"{module_key}/{item_key}")


def load_course(theme: Theme | str | Path | None = None, anchor: date | None = None, lti_path: Path | None = None,
                seed: int | None = None) -> Course:
    """``theme``: a Theme, a theme name under themes/, or a theme directory (None = default theme).
    ``seed`` drives every randomized choice (generated quiz questions). None = fresh random seed."""
    theme = theme if isinstance(theme, Theme) else load_theme(theme)
    with use_namespace(theme.namespace):
        return _load(theme, anchor, lti_path, seed)


def _load(theme: Theme, anchor: date | None, lti_path: Path | None, seed: int | None) -> Course:
    root = theme.root
    settings = _yaml(root / "course.yaml")
    tz = ZoneInfo(settings.get("time_zone", "America/Denver"))
    resolver = DateResolver(anchor=anchor or date.today(), tz=tz)
    course = Course(theme=theme, root=root, settings=settings, dates=resolver)
    course.seed = seed if seed is not None else secrets.randbelow(10**6)
    course.catalog = theme.catalog()

    lti_file = Path(lti_path) if lti_path else root / "lti.yaml"
    if lti_file.exists():
        course.lti = _yaml(lti_file)

    for g in settings.get("assignment_groups", []):
        g["id"] = mid("assignment_group", g["key"])
        course.assignment_groups[g["key"]] = g
    for gs in settings.get("grading_standards", []):
        gs["id"] = mid("grading_standard", gs["key"])
        course.grading_standards[gs["key"]] = gs
    for o in settings.get("outcomes", {}).get("outcomes", []):
        o["id"] = mid("outcome", o["key"])
        course.outcomes[o["key"]] = o
    for r in settings.get("rubrics", []):
        r["id"] = mid("rubric", r["key"])
        course.rubrics[r["key"]] = r
    for i, ev in enumerate(settings.get("events", [])):
        course.events.append(ev)
    for path, meta in (settings.get("files") or {}).items():
        course.files[path] = {"path": path, **(meta or {})}

    for qpath in sorted((root / "quizzes").glob("*.yaml")):
        doc = _yaml(qpath)
        doc["questions"] = _expand_generated(course, doc)
        if doc.get("bank"):
            doc["id"] = mid("bank", doc["key"])
            course.banks[doc["key"]] = doc
        else:
            _add_quiz(course, doc)

    for a in settings.get("announcements", []):
        _add_discussion(course, a, announcement=True)
    for p in settings.get("pages", []):  # pages not placed in any module
        _add_page(course, p)
    for a in settings.get("assignments", []):
        _add_assignment(course, a)
    for d in settings.get("discussions", []):
        _add_discussion(course, d)

    module_files = sorted((root / "modules").glob("*.yaml"))
    for pos, mpath in enumerate(module_files, start=1):
        m = _yaml(mpath)
        if m.get("requires_lti") and not course.lti:
            continue
        _add_module(course, m)
    for i, m in enumerate(course.modules, start=1):
        m["position"] = i
    return course


# --------------------------------------------------------------------- helpers
def _yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ------------------------------------------------------------ generated questions
IDENTIFY_DEFAULTS = {
    "prompt": "Who is this?",
    "question_name": "Masked #{id}",
    "correct": "It's {name}!",
    "incorrect": "It's {name}.",
    "choices": 4,
    "points": 1,
}


def _identify(course: "Course", entry: dict, rng: random.Random) -> dict:
    """One identification question: the entry's masked image + N name choices (strings from theme.yaml ``identify``)."""
    cfg = {**IDENTIFY_DEFAULTS, **course.theme.identify}
    cat = course.catalog
    wrong = rng.sample([e for e in cat if e["id"] != entry["id"]], cfg["choices"] - 1)
    choices = [{"text": entry["name"], "correct": True}] + [{"text": e["name"]} for e in wrong]
    rng.shuffle(choices)
    fmt = lambda k: course.theme.fmt(cfg[k], entry)
    return {
        "type": "multiple_choice",
        "generated": "identify",
        "name": fmt("question_name"),  # question names are instructor-facing only
        "points": cfg["points"],
        "text": f"<p>{cfg['prompt']}</p><p>{{{{ masked(catalog[{cat.index(entry)}]) }}}}</p>",
        "answers": choices,
        "feedback": {"correct": fmt("correct"), "incorrect": fmt("incorrect")},
    }


GENERATORS = {"identify": _identify}


def _expand_generated(course: "Course", doc: dict) -> list[dict]:
    """Replace ``{type: generated, generator, count | all, catalog_group}`` entries with concrete questions.

    Randomness comes from ``course.seed`` (+ the quiz key), so a build is reproducible with --seed
    and different on every build without it.
    """
    out = []
    for i, q in enumerate(doc.get("questions", [])):
        if q.get("type") != "generated":
            out.append(q)
            continue
        if q["generator"] not in GENERATORS:
            raise ContentError(f"quiz {doc['key']}: unknown generator {q['generator']!r} (have: {', '.join(GENERATORS)})")
        gen = GENERATORS[q["generator"]]
        rng = random.Random(f"{course.seed}:{doc['key']}:{i}")
        pool = [e for e in course.catalog if not q.get("catalog_group") or e["group"] == q["catalog_group"]]
        picked = pool if q.get("all") else rng.sample(pool, q["count"])
        out.extend(gen(course, e, rng) for e in picked)
    return out


def _unique(registry: dict, key: str, kind: str):
    if key in registry:
        raise ContentError(f"duplicate {kind} key: {key}")


def _add_page(course: Course, p: dict) -> dict:
    if p.get("ref"):
        return course.pages[p["key"]]
    _unique(course.pages, p["key"], "page")
    p["id"] = mid("page", p["key"])
    p.setdefault("url", slugify(p.get("url") or p["title"]))
    course.pages[p["key"]] = p
    return p


def _add_assignment(course: Course, a: dict) -> dict:
    if a.get("ref"):
        return course.assignments[a["key"]]
    _unique(course.assignments, a["key"], "assignment")
    a["id"] = mid("assignment", a["key"])
    course.assignments[a["key"]] = a
    return a


def _add_quiz(course: Course, q: dict) -> dict:
    _unique(course.quizzes, q["key"], "quiz")
    q["id"] = mid("quiz", q["key"])
    q["meta_id"] = mid("quiz_meta", q["key"])
    if q.get("quiz_type", "assignment") in ("assignment", "graded_survey"):
        q["assignment_id"] = mid("quiz_assignment", q["key"])
    course.quizzes[q["key"]] = q
    return q


def _add_discussion(course: Course, d: dict, announcement: bool = False) -> dict:
    reg = course.announcements if announcement else course.discussions
    if d.get("ref"):
        return reg[d["key"]]
    _unique(reg, d["key"], "announcement" if announcement else "discussion")
    d["id"] = mid("announcement" if announcement else "discussion", d["key"])
    d["meta_id"] = mid("topic_meta", d["key"])
    d["announcement"] = announcement
    if d.get("graded"):
        d["graded"]["id"] = mid("discussion_assignment", d["key"])
    reg[d["key"]] = d
    return d


def _add_module(course: Course, m: dict) -> None:
    m["id"] = mid("module", m["key"])
    items = []
    for raw in m.get("items", []):
        t = raw.get("type")
        if t not in ITEM_TYPES:
            raise ContentError(f"module {m['key']}: unknown item type {t!r}")
        item = {
            "type": t,
            "indent": raw.get("indent", 0),
            "new_tab": raw.get("new_tab", False),
            "published": raw.get("item_published", True),
            "requirement": raw.get("requirement"),
            "min_score": raw.get("min_score"),
            "min_percentage": raw.get("min_percentage"),
            "tests": raw.get("tests", []),
        }
        if t == "subheader":
            item.update(key=f"sub-{slugify(raw['title'])}", title=raw["title"], content_type="ContextModuleSubHeader")
        elif t == "page":
            obj = _add_page(course, raw)
            item.update(key=f"page-{obj['key']}", title=raw.get("item_title") or obj["title"], ref_id=obj["id"], content_type="WikiPage")
        elif t == "assignment":
            obj = _add_assignment(course, raw)
            item.update(key=f"assignment-{obj['key']}", title=raw.get("item_title") or obj["title"], ref_id=obj["id"], content_type="Assignment")
        elif t == "quiz":
            obj = course.quizzes.get(raw["key"])
            if obj is None:
                raise ContentError(f"module {m['key']}: quiz {raw['key']!r} has no quizzes/*.yaml")
            item.update(key=f"quiz-{obj['key']}", title=raw.get("item_title") or obj["title"], ref_id=obj["id"], content_type="Quizzes::Quiz")
        elif t == "discussion":
            obj = _add_discussion(course, raw)
            item.update(key=f"discussion-{obj['key']}", title=raw.get("item_title") or obj["title"], ref_id=obj["id"], content_type="DiscussionTopic")
        elif t == "file":
            path = raw["path"]
            course.files.setdefault(path, {"path": path})
            item.update(key=f"file-{slugify(path)}", title=raw.get("title") or Path(path).name, ref_id=file_id(path), content_type="Attachment")
        elif t == "url":
            item.update(key=f"url-{raw['key']}", title=raw["title"], url=raw["url"], content_type="ExternalUrl",
                        ref_id=mid("weblink", f"{m['key']}/{raw['key']}"))
        elif t == "external_tool":
            tool = (course.lti or {}).get("tools", {}).get(raw["tool"])
            if tool is None:
                raise ContentError(f"module {m['key']}: external_tool item references unknown tool {raw.get('tool')!r}")
            item.update(key=f"tool-{raw['key']}", title=raw["title"], url=raw.get("url") or tool["launch_url"],
                        content_type="ContextExternalTool", ref_id=mid("lti_tool", raw["tool"]))
        item["id"] = course.module_item_id(m["key"], item["key"])
        items.append(item)
    m["items"] = items
    course.modules.append(m)


def file_id(path: str) -> str:
    return mid("file", path)


# ------------------------------------------------------------------ rendering
class Renderer:
    """Renders HTML (Jinja) with link helpers that emit Canvas export placeholders."""

    def __init__(self, course: Course):
        self.course = course
        self.env = Environment(autoescape=False, undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
        c = course
        self.env.globals.update(
            page=self.page_url, assignment=self.assignment_url, quiz=self.quiz_url,
            discussion=self.discussion_url, file=self.file_url, course_link=self.course_url,
            module_item=self.module_item_url, a_page=self.a_page, a_assignment=self.a_assignment,
            a_quiz=self.a_quiz, a_discussion=self.a_discussion, a_file=self.a_file, img=self.img,
            entry_image=self.entry_image, masked=self.masked,
            catalog=c.catalog, catalog_group=lambda g: [x for x in c.catalog if x["group"] == g],
            entry=self.entry, fetched_count=lambda: len(c.fetched_images),
            date=lambda spec, f="%A, %B %-d, %Y at %-I:%M %p %Z": c.dates.local(spec).strftime(f),
            settings=c.settings, lti=c.lti, escape=escape, course=c,
        )
        hook = c.theme.hook("jinja_globals")
        if hook:
            self.env.globals.update(hook(c, self))
        self._where = "?"
        self.masked_used: list[dict] = []  # masked images rendered since last reset (quiz feedback hook)

    def render(self, source: str, where: str, **ctx) -> str:
        self._where = where
        try:
            return self.env.from_string(source).render(**ctx)
        except Exception as e:
            raise ContentError(f"{where}: template error: {e}") from e

    def render_file(self, rel: str, where: str, **ctx) -> str:
        return self.render((self.course.root / rel).read_text(encoding="utf-8"), where, **ctx)

    def html(self, obj: dict, field_name: str, where: str) -> str:
        """``obj[field]`` may be inline HTML or ``{file: pages/x.html}``."""
        val = obj.get(field_name)
        if val is None:
            return ""
        if isinstance(val, dict) and "file" in val:
            return self.render_file(val["file"], where)
        return self.render(str(val), where)

    # url helpers -----------------------------------------------------------
    def _ref(self, kind, key):
        self.course.refs.append((kind, key, self._where))

    def page_url(self, key):
        self._ref("page", key)
        return f"{WIKI_TOKEN}/pages/{mid('page', key)}"

    def assignment_url(self, key):
        self._ref("assignment", key)
        return f"{OBJECT_TOKEN}/assignments/{mid('assignment', key)}"

    def quiz_url(self, key):
        self._ref("quiz", key)
        return f"{OBJECT_TOKEN}/quizzes/{mid('quiz', key)}"

    def discussion_url(self, key):
        self._ref("discussion", key)
        return f"{OBJECT_TOKEN}/discussion_topics/{mid('discussion', key)}"

    def file_url(self, path, download=False):
        self._ref("file", path)
        self.course.files.setdefault(path, {"path": path})
        url = f"{FILE_TOKEN}/" + "/".join(urllib.parse.quote(p) for p in path.split("/"))
        return url + ("?canvas_download=1" if download else "")

    def course_url(self, rest=""):
        return f"{COURSE_TOKEN}/{rest}".rstrip("/")

    def module_item_url(self, module_key, item_key):
        self._ref("module_item", f"{module_key}/{item_key}")
        return f"{COURSE_TOKEN}/modules/items/{self.course.module_item_id(module_key, item_key)}"

    # anchor helpers (mirror RCE-inserted links) ----------------------------
    def _a(self, href, text, rtype, cls=""):
        c = f' class="{cls}"' if cls else ""
        return Markup(f'<a{c} title="{escape(text)}" href="{href}" data-api-returntype="{rtype}">{escape(text)}</a>')

    def a_page(self, key, text=None, cls=""):
        return self._a(self.page_url(key), text or self.course.pages.get(key, {}).get("title", key), "Page", cls)

    def a_assignment(self, key, text=None, cls=""):
        return self._a(self.assignment_url(key), text or self.course.assignments.get(key, {}).get("title", key), "Assignment", cls)

    def a_quiz(self, key, text=None, cls=""):
        return self._a(self.quiz_url(key), text or self.course.quizzes.get(key, {}).get("title", key), "Quiz", cls)

    def a_discussion(self, key, text=None, cls=""):
        return self._a(self.discussion_url(key), text or self.course.discussions.get(key, {}).get("title", key), "Discussion", cls)

    def a_file(self, path, text=None, cls=""):
        text = text or path.rsplit("/", 1)[-1]
        classes = f"instructure_file_link {cls}".strip()
        return Markup(f'<a class="{classes}" title="{escape(text)}" href="{self.file_url(path)}" '
                      f'data-api-returntype="File">{escape(text)}</a>')

    def img(self, path, alt, width=None, style=""):
        w = f' width="{width}"' if width else ""
        s = f' style="{style}"' if style else ""
        return Markup(f'<img src="{self.file_url(path)}" alt="{escape(alt)}"{w}{s} data-api-returntype="File" />')

    # catalog helpers -------------------------------------------------------
    def entry(self, name):
        """Catalog entry by name (case-insensitive)."""
        for x in self.course.catalog:
            if x["name"].lower() == name.lower():
                return x
        raise ContentError(f"{self._where}: no catalog entry named {name!r}")

    def entry_image(self, e, width=96):
        imgs = self.course.theme.images
        fetched = e["id"] in self.course.fetched_images
        alt = imgs.get("alt_fetched" if fetched else "alt_placeholder", "{name}")
        return self.img(e["image"], self.course.theme.fmt(alt, e), width)

    def masked(self, e, width=120):
        """The entry's masked image under a non-revealing filename, with name-free alt text (for quiz questions)."""
        imgs = self.course.theme.images
        digest = hashlib.md5(f"mystery:{e['id']}".encode()).hexdigest()[:10]
        path = f"{imgs.get('masked_dir', 'images/masked')}/{digest}.png"
        self.course.masked[path] = e["id"]
        self.masked_used.append(e)
        fetched = e["id"] in self.course.fetched_images
        alt = imgs.get("masked_alt_fetched" if fetched else "masked_alt_placeholder", "Masked image (name hidden)")
        return self.img(path, alt, width)
