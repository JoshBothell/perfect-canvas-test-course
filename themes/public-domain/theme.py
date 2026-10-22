"""Public Domain Academy theme hooks: catalog enrichment, placeholder labels, term arithmetic and file recipes.

Loaded by ``cc_gen.theme.load_theme`` from this file path. See ``cc_gen.theme.HOOKS`` for the hook
contract. Commercial-safe: everything generated here is original, the statute excerpts are U.S.
government works, and catalog images come from CC0 museum open-access programs (theme.yaml images.source).
"""

from __future__ import annotations

import csv
import io
import json
import random
from functools import partial

from PIL import Image, ImageDraw

from cc_gen.assets import banner, docx, font, hex_rgb, mix, pdf, png, scene_webp, spinner_gif
from cc_gen.images import palette_png

ERA_LABELS = {1: "Before 1800", 2: "1800-1899", 3: "1900-1930"}
ERA_COLORS = {1: "#7a4e2d", 2: "#2f5d62", 3: "#8a3b5c"}
MUSEUMS = {"aic": "Art Institute of Chicago", "met": "The Metropolitan Museum of Art"}
DEATH_YEARS = list(range(1940, 2021, 5))  # 17 rows
TERMS = list(range(50, 131, 5))  # 17 columns: illustrative "life + N" terms
STATUSES = {  # status stamp badges (original text badges, not anyone's logo)
    "public-domain": ("PUBLIC DOMAIN", "#2e7d32"),
    "cc0": ("CC0 WAIVER", "#1565c0"),
    "us-government": ("U.S. GOV WORK", "#37474f"),
    "expired": ("TERM EXPIRED", "#6d4c41"),
    "not-renewed": ("NOT RENEWED", "#8e24aa"),
    "in-copyright": ("IN COPYRIGHT", "#c62828"),
}
_pdf = partial(pdf, author="Public Domain Academy (test fixture)")


def pd_year(death: int, term: int) -> int:
    """Copyright runs to the end of the calendar year, so the work is free on January 1 of the next one."""
    return death + term + 1


# -------------------------------------------------------------------- hooks
def enrich_entry(e: dict) -> None:
    e["era_label"] = ERA_LABELS[e["era"]]
    e["museum"] = MUSEUMS[e["source"]]
    e["credit"] = f"{e['museum']}, {e['license']}"


def placeholder_image(e: dict, hide_name: bool = False, size: int = 120) -> bytes:
    """Original 'museum label' card: era-colored mat, an abstract seeded composition, and a caption strip."""
    mat = hex_rgb(ERA_COLORS[e["era"]])
    img = Image.new("RGB", (size, size), mat)
    d = ImageDraw.Draw(img)
    rng = random.Random(f"pda:{e['id']}")
    canvas = [10, 8, size - 10, size - 30]
    d.rectangle(canvas, fill=mix((250, 245, 230), mat, .1), outline=(40, 30, 20), width=2)
    for _ in range(5):  # deterministic abstract composition unique per catalog id
        x0, y0 = rng.randint(canvas[0], canvas[2] - 20), rng.randint(canvas[1], canvas[3] - 16)
        x1, y1 = min(x0 + rng.randint(12, 50), canvas[2]), min(y0 + rng.randint(10, 40), canvas[3])
        fill = mix(mat, (255, 255, 255), rng.random() * .6)
        if rng.random() < .5:
            d.ellipse([x0, y0, x1, y1], fill=fill)
        else:
            d.rectangle([x0, y0, x1, y1], fill=fill)
    d.rectangle([0, size - 24, size, size], fill=(255, 255, 255))
    label = "#??? ?????" if hide_name else f"#{e['id']:03d} {e['name']}"[:18]
    d.text((6, size - 21), label, fill=(20, 20, 20), font=font(12))
    d.rectangle([0, 0, size - 1, size - 1], outline=(30, 30, 30), width=2)
    return png(img)


def jinja_globals(course, r) -> dict:
    return {
        "status_badge": lambda key, width=96: r.img(f"images/status/{key}.png", f"{STATUSES[key][0].title()} status stamp", width),
        "STATUSES": STATUSES, "ERA_LABELS": ERA_LABELS, "ERA_COLORS": ERA_COLORS,
        "DEATH_YEARS": DEATH_YEARS, "TERMS": TERMS, "pd_year": pd_year, "MUSEUMS": MUSEUMS,
    }


def assets(course) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for key in STATUSES:
        out[f"images/status/{key}.png"] = palette_png(status_badge(key))
    out["images/course-card.png"] = banner("Public Domain Academy", "PDA-101  |  Canvas LTI test course", ["#7a4e2d", "#c9a227", "#2f5d62"])
    out["images/course-banner.png"] = banner("Public Domain Academy", "Foundations  ·  Expiration  ·  Reuse", ["#2f5d62", "#8a3b5c"], 1500, 400)
    out["images/units/unit1.png"] = banner("Unit 1: Foundations", "Works before 1800", ["#7a4e2d", "#c9a227"])
    out["images/units/unit2.png"] = banner("Unit 2: Entering the PD", "Works 1800-1899", ["#2f5d62", "#7fb7be"])
    out["images/units/unit3.png"] = banner("Unit 3: Using the PD", "Works 1900-1930", ["#8a3b5c", "#e0a458"])
    out["images/loading-book.gif"] = spinner_gif((122, 78, 45))
    out["images/reading-room.webp"] = scene_webp("Reading Room path (WebP test image)")
    out["images/open-book.svg"] = OPEN_BOOK_SVG.encode()
    out["documents/term-chart.pdf"] = term_chart_pdf()
    out["documents/copyright-clause-annotatable.pdf"] = statute_pdf()
    out["documents/Domaine public – Œuvres libres.pdf"] = unicode_name_pdf()
    out["documents/pd-research-handbook.docx"] = docx("Public Domain Research Handbook", [
        "Welcome to Public Domain Academy. This Word document is a test asset for document previews and conversions.",
        "Step 1: Find the work's first publication date and place. Step 2: Check notice and renewal records for "
        "works published 1930-1963. Step 3: Record your source and licence evidence. Step 4: Remember that new "
        "material in an adaptation, and trademarks, can still be protected.",
        "This handbook is educational test-fixture content, not legal advice."])
    out["data/collection.csv"] = (course.root / "data" / "collection.csv").read_bytes()
    out["data/renewal-search-log-10k.csv"] = renewal_log(course.catalog)
    out["data/term-rules.json"] = term_rules_json()
    out["data/pd_calculator.py"] = PD_CALCULATOR_PY.encode()
    out["Instructor's Archive/answer-key.pdf"] = answer_key_pdf()
    return out


# ------------------------------------------------------------------ recipes
def status_badge(key: str) -> bytes:
    text, color = STATUSES[key]
    img = Image.new("RGBA", (192, 48), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, 191, 47], radius=8, fill=(255, 255, 255, 255), outline=hex_rgb(color) + (255,), width=4)
    f = font(18)
    w = d.textlength(text, font=f)
    d.text(((192 - w) / 2, 13), text, fill=hex_rgb(color), font=f)
    return png(img)


OPEN_BOOK_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" role="img" aria-labelledby="t">
  <title id="t">Open book icon (SVG test asset)</title>
  <path d="M50 26C38 18 22 18 8 22v56c14-4 30-4 42 4z" fill="#fdf6e3" stroke="#3b2a1a" stroke-width="4" stroke-linejoin="round"/>
  <path d="M50 26c12-8 28-8 42-4v56c-14-4-30-4-42 4z" fill="#fdf6e3" stroke="#3b2a1a" stroke-width="4" stroke-linejoin="round"/>
  <line x1="50" y1="26" x2="50" y2="82" stroke="#3b2a1a" stroke-width="4"/>
  <path d="M16 36h24M16 46h24M16 56h20M60 36h24M60 46h24M60 56h20" stroke="#7a4e2d" stroke-width="3"/>
</svg>
"""

# Simplified U.S. rules (after Cornell's "Copyright Term and the Public Domain" chart), as of 2026.
US_RULES = [
    ("Published before 1931", "Any", "Expired", "Yes"),
    ("Published 1931-1963", "No notice", "None", "Yes"),
    ("Published 1931-1963", "Notice, not renewed", "28 years", "Yes"),
    ("Published 1931-1963", "Notice and renewed", "95 years", "No"),
    ("Published 1964-1977", "Without notice", "None", "Yes"),
    ("Published 1964-1977", "With notice", "95 years", "No"),
    ("Created 1978 or later", "Individual author", "Life + 70", "No"),
    ("Created 1978 or later", "Work for hire", "95/120 years", "No"),
    ("Any date", "U.S. federal government work", "None (17 USC 105)", "Yes"),
    ("Any date", "CC0 dedication", "Waived", "Yes"),
]


def term_chart_pdf() -> bytes:
    rows = [["Publication", "Conditions", "Term", "PD in U.S.?"]] + [list(r) for r in US_RULES]
    return _pdf("U.S. Copyright Term Chart (simplified)", [("How to read this chart", [
        "Find the row for the work's publication date and conditions. Terms run to the end of the calendar year, "
        "so a work enters the public domain on January 1. Simplified educational content, not legal advice.",
        "Test fixture note: a landscape, table-heavy PDF for document viewers, DocViewer and annotation tools."])],
        table=rows, landscape=True, cell_fill={"Yes": (140, 220, 140), "No": (240, 160, 140)})


STATUTES = [
    ("U.S. Constitution, Article I, Section 8, Clause 8", [
        "[The Congress shall have Power...] To promote the Progress of Science and useful Arts, by securing for limited "
        "Times to Authors and Inventors the exclusive Right to their respective Writings and Discoveries;"]),
    ("17 U.S.C. 102(a)", [
        "Copyright protection subsists, in accordance with this title, in original works of authorship fixed in any "
        "tangible medium of expression, now known or later developed, from which they can be perceived, reproduced, "
        "or otherwise communicated, either directly or with the aid of a machine or device."]),
    ("17 U.S.C. 102(b)", [
        "In no case does copyright protection for an original work of authorship extend to any idea, procedure, process, "
        "system, method of operation, concept, principle, or discovery, regardless of the form in which it is described, "
        "explained, illustrated, or embodied in such work."]),
    ("17 U.S.C. 105(a)", [
        "Copyright protection under this title is not available for any work of the United States Government, but the "
        "United States Government is not precluded from receiving and holding copyrights transferred to it by assignment, "
        "bequest, or otherwise."]),
    ("17 U.S.C. 302(a)", [
        "Copyright in a work created on or after January 1, 1978, subsists from its creation and, except as provided by "
        "the following subsections, endures for a term consisting of the life of the author and 70 years after the "
        "author's death."]),
]


def statute_pdf() -> bytes:
    intro = ("Annotation exercise. Students: annotate this document using the Canvas annotation tools. Highlight the "
             "phrase that limits copyright in time, and comment on one thing copyright does not protect. These texts are "
             "works of the U.S. government and are in the public domain.")
    return _pdf("The Copyright Clause & Title 17 Excerpts (annotatable)", [("Instructions", [intro]), *STATUTES,
                ("Page 2", ["(Intentionally multi-section so annotation tools have something to scroll.)"] * 12)])


def answer_key_pdf() -> bytes:
    return _pdf("Instructor Answer Key (hidden + locked file)", [("Why this file exists", [
        "This file lives in a hidden, locked folder. Students should never be able to list or download it; "
        "LTI tools using the Files API with a student token should get a 401/404 for it."])])


def unicode_name_pdf() -> bytes:
    return _pdf("Domaine public - Oeuvres libres", [("Filename test", [
        "The display name of this file contains non-ASCII characters (an en dash and the ligature OE) and spaces. "
        "Use it to test URL encoding, Content-Disposition headers and file pickers."])])


def renewal_log(catalog: list[dict], rows: int = 10000) -> bytes:
    rng = random.Random(1790)
    researchers = ["tom", "elizabeth", "huck", "jane", "dorothy", "ichabod", "jo", "hester"]
    sources = ["Catalog of Copyright Entries", "Copyright Office online records", "Stanford renewal database",
               "Museum open-access API", "Library catalog"]
    results = ["no registration found", "registered, not renewed", "registered and renewed", "published before 1931",
               "CC0 dedication", "U.S. government work"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["search_id", "researcher", "catalog_id", "title", "source_searched", "result", "minutes_spent", "verified"])
    for i in range(1, rows + 1):
        e = rng.choice(catalog)
        w.writerow([i, rng.choice(researchers), e["id"], e["name"], rng.choice(sources), rng.choice(results),
                    rng.randint(2, 90), int(rng.random() < .6)])
    return buf.getvalue().encode()


def term_rules_json() -> bytes:
    rules = [{"publication": p, "conditions": c, "term": t, "public_domain_in_us": pd == "Yes"} for p, c, t, pd in US_RULES]
    return (json.dumps({"_note": "JSON test asset for file preview / API download tests. Simplified; not legal advice.",
                        "as_of": "2026-01-01", "us_rules": rules}, ensure_ascii=False, indent=2) + "\n").encode()


PD_CALCULATOR_PY = '''"""Sample source file (code preview / syntax highlighting test asset). Simplified; not legal advice."""


def us_public_domain_year(published: int, renewed: bool = True, notice: bool = True) -> int | None:
    """January 1 of the year a U.S. work published before 1978 enters the public domain (None = already free)."""
    if published < 1931 or not notice:
        return None
    if published < 1964 and not renewed:
        return None
    return published + 95 + 1


if __name__ == "__main__":
    print(us_public_domain_year(1950))  # 2046
'''
