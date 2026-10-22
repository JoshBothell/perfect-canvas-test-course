"""Pokémon theme hooks: catalog enrichment, placeholder cards, type helpers and file recipes.

Loaded by ``cc_gen.theme.load_theme`` from this file path. See ``cc_gen.theme.HOOKS`` for the
hook contract. Original placeholder art only; official artwork comes from theme.yaml images.source.
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

TYPE_COLORS = {
    "Normal": "#A8A77A", "Fire": "#EE8130", "Water": "#6390F0", "Electric": "#F7D02C", "Grass": "#7AC74C",
    "Ice": "#96D9D6", "Fighting": "#C22E28", "Poison": "#A33EA1", "Ground": "#E2BF65", "Flying": "#A98FF3",
    "Psychic": "#F95587", "Bug": "#A6B91A", "Rock": "#B6A136", "Ghost": "#735797", "Dragon": "#6F35FC",
    "Dark": "#705746", "Steel": "#B7B7CE",
}
TYPES = list(TYPE_COLORS)

# Gen II–V type chart (attacker -> {defender: multiplier}); unlisted = 1x
CHART = {
    "Normal": {"Rock": .5, "Steel": .5, "Ghost": 0},
    "Fire": {"Grass": 2, "Ice": 2, "Bug": 2, "Steel": 2, "Fire": .5, "Water": .5, "Rock": .5, "Dragon": .5},
    "Water": {"Fire": 2, "Ground": 2, "Rock": 2, "Water": .5, "Grass": .5, "Dragon": .5},
    "Electric": {"Water": 2, "Flying": 2, "Electric": .5, "Grass": .5, "Dragon": .5, "Ground": 0},
    "Grass": {"Water": 2, "Ground": 2, "Rock": 2, "Fire": .5, "Grass": .5, "Poison": .5, "Flying": .5, "Bug": .5, "Dragon": .5, "Steel": .5},
    "Ice": {"Grass": 2, "Ground": 2, "Flying": 2, "Dragon": 2, "Fire": .5, "Water": .5, "Ice": .5, "Steel": .5},
    "Fighting": {"Normal": 2, "Ice": 2, "Rock": 2, "Dark": 2, "Steel": 2, "Poison": .5, "Flying": .5, "Psychic": .5, "Bug": .5, "Ghost": 0},
    "Poison": {"Grass": 2, "Poison": .5, "Ground": .5, "Rock": .5, "Ghost": .5, "Steel": 0},
    "Ground": {"Fire": 2, "Electric": 2, "Poison": 2, "Rock": 2, "Steel": 2, "Grass": .5, "Bug": .5, "Flying": 0},
    "Flying": {"Grass": 2, "Fighting": 2, "Bug": 2, "Electric": .5, "Rock": .5, "Steel": .5},
    "Psychic": {"Fighting": 2, "Poison": 2, "Psychic": .5, "Steel": .5, "Dark": 0},
    "Bug": {"Grass": 2, "Psychic": 2, "Dark": 2, "Fire": .5, "Fighting": .5, "Poison": .5, "Flying": .5, "Ghost": .5, "Steel": .5},
    "Rock": {"Fire": 2, "Ice": 2, "Flying": 2, "Bug": 2, "Fighting": .5, "Ground": .5, "Steel": .5},
    "Ghost": {"Psychic": 2, "Ghost": 2, "Dark": .5, "Steel": .5, "Normal": 0},
    "Dragon": {"Dragon": 2, "Steel": .5},
    "Dark": {"Psychic": 2, "Ghost": 2, "Fighting": .5, "Dark": .5, "Steel": .5},
    "Steel": {"Ice": 2, "Rock": 2, "Fire": .5, "Water": .5, "Electric": .5, "Steel": .5},
}

STATS = ("hp", "attack", "defense", "sp_attack", "sp_defense", "speed")
_pdf = partial(pdf, author="Pokemon Trainer Academy (test fixture)")


# -------------------------------------------------------------------- hooks
def enrich_entry(e: dict) -> None:
    e["bst"] = sum(e[k] for k in STATS)
    e["types"] = [t for t in (e["type1"], e["type2"]) if t]


def placeholder_image(mon: dict, hide_name: bool = False, size: int = 120) -> bytes:
    """Abstract trading-card placeholder: type-colored gradient, dex number, an original geometric 'glyph'."""
    c1 = hex_rgb(TYPE_COLORS[mon["types"][0]])
    c2 = hex_rgb(TYPE_COLORS[mon["types"][-1]]) if len(mon["types"]) > 1 else mix(c1, (255, 255, 255), .45)
    img = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(img)
    for y in range(size):
        d.line([(0, y), (size, y)], fill=mix(c1, c2, y / size))
    rng = random.Random(mon["dex"])
    cx, cy = size // 2, size // 2 - 6
    for _ in range(3):  # deterministic abstract glyph unique per dex number
        r = rng.randint(size // 8, size // 4)
        ox, oy = rng.randint(-14, 14), rng.randint(-10, 10)
        shape = rng.choice(["ellipse", "rect", "tri"])
        fill = mix((255, 255, 255), c1, rng.random() * .35)
        box = [cx + ox - r, cy + oy - r, cx + ox + r, cy + oy + r]
        if shape == "ellipse":
            d.ellipse(box, fill=fill, outline=(40, 40, 40), width=2)
        elif shape == "rect":
            d.rounded_rectangle(box, radius=6, fill=fill, outline=(40, 40, 40), width=2)
        else:
            d.polygon([(box[0], box[3]), (box[2], box[3]), ((box[0] + box[2]) // 2, box[1])], fill=fill, outline=(40, 40, 40))
    d.rectangle([0, size - 24, size, size], fill=(255, 255, 255))
    label = "#??? ?????" if hide_name else f"#{mon['dex']:03d} {mon['name']}"[:18]
    d.text((6, size - 21), label, fill=(20, 20, 20), font=font(12))
    d.rectangle([0, 0, size - 1, size - 1], outline=(30, 30, 30), width=2)
    return png(img)


def jinja_globals(course, r) -> dict:
    return {
        "type_badge": lambda type_name, width=72: r.img(f"images/types/{type_name.lower()}.png", f"{type_name} type badge", width),
        "TYPES": TYPES, "TYPE_COLORS": TYPE_COLORS, "effectiveness": lambda a, d: CHART[a].get(d, 1),
    }


def assets(course) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for t in TYPES:
        out[f"images/types/{t.lower()}.png"] = palette_png(type_badge(t))
    out["images/course-card.png"] = banner("Pokemon Trainer Academy", "Gen I-III  |  Canvas LTI test course", ["#e3350d", "#ffcb05", "#3b4cca"])
    out["images/course-banner.png"] = banner("Trainer Academy", "Kanto  ·  Johto  ·  Hoenn", ["#3b4cca", "#7ac74c"], 1500, 400)
    out["images/regions/kanto.png"] = banner("Gen I: Kanto", "Red · Blue · Yellow", ["#e3350d", "#3b4cca"])
    out["images/regions/johto.png"] = banner("Gen II: Johto", "Gold · Silver · Crystal", ["#b8860b", "#c0c0c0", "#4fd1c5"])
    out["images/regions/hoenn.png"] = banner("Gen III: Hoenn", "Ruby · Sapphire · Emerald", ["#c0392b", "#2e86c1", "#27ae60"])
    out["images/loading-ball.gif"] = spinner_gif((220, 30, 30))
    out["images/route-1.webp"] = scene_webp("Route 1 (WebP test image)")
    out["images/capture-ball.svg"] = POKEBALL_SVG.encode()
    out["documents/type-chart.pdf"] = type_chart_pdf()
    out["documents/prof-oak-lab-notes.pdf"] = lab_notes_pdf()
    out["documents/Pokédex – Ōkido notes.pdf"] = unicode_name_pdf()
    out["documents/trainer-handbook.docx"] = docx("Trainer Handbook", [
        "Welcome to the Pokemon Trainer Academy. This Word document is a test asset for document previews and conversions.",
        "Rule 1: Always carry Potions. Rule 2: Never leave your Pokemon fainted. Rule 3: Respect the Gym Leaders."])
    out["data/pokedex_1_386.csv"] = (course.root / "data" / "pokedex_1_386.csv").read_bytes()
    out["data/encounter-log-10k.csv"] = encounter_log(course.catalog)
    out["data/gym-leaders.json"] = gym_leaders_json()
    out["data/team_builder.py"] = TEAM_BUILDER_PY.encode()
    out["Professor's Archive/answer-key.pdf"] = answer_key_pdf()
    return out


# ------------------------------------------------------------------ recipes
def type_badge(t: str) -> bytes:
    img = Image.new("RGBA", (144, 48), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, 143, 47], radius=24, fill=hex_rgb(TYPE_COLORS[t]) + (255,))
    f = font(22)
    w = d.textlength(t.upper(), font=f)
    d.text(((144 - w) / 2, 11), t.upper(), fill=(255, 255, 255), font=f, stroke_width=2, stroke_fill=(0, 0, 0))
    return png(img)


POKEBALL_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" role="img" aria-labelledby="t">
  <title id="t">Capture ball icon (SVG test asset)</title>
  <circle cx="50" cy="50" r="46" fill="#fff" stroke="#222" stroke-width="6"/>
  <path d="M4 50a46 46 0 0 1 92 0z" fill="#e3350d" stroke="#222" stroke-width="6"/>
  <line x1="4" y1="50" x2="96" y2="50" stroke="#222" stroke-width="6"/>
  <circle cx="50" cy="50" r="13" fill="#fff" stroke="#222" stroke-width="6"/>
</svg>
"""


def type_chart_pdf() -> bytes:
    header = ["ATK \\ DEF"] + [t[:4] for t in TYPES]
    rows = [header]
    for a in TYPES:
        row = [a]
        for dfn in TYPES:
            m = CHART[a].get(dfn, 1)
            row.append({2: "2", .5: "1/2", 0: "0", 1: ""}[m])
        rows.append(row)
    return _pdf("Type Matchup Chart (Gen II-III)", [("How to read this chart", [
        "Rows are the attacking move's type; columns are the defending Pokemon's type. "
        "2 = super effective, 1/2 = not very effective, 0 = no effect, blank = normal damage.",
        "Test fixture note: a landscape, table-heavy PDF for document viewers, DocViewer and annotation tools."])],
        table=rows, landscape=True, cell_fill={"2": (140, 220, 140), "1/2": (240, 160, 140), "0": (80, 80, 80)})


def lab_notes_pdf() -> bytes:
    paras = [
        "Observation log, Pallet Town lab. Students: annotate this document using the Canvas annotation tools. "
        "Highlight one observation you agree with, and leave a comment on one you would test differently.",
        "1. Starter Pokemon given to new trainers are selected for balanced type coverage: Grass, Fire and Water.",
        "2. Evolution is triggered by level, stones, trade, or friendship depending on the species.",
        "3. Type matchups follow a rock-paper-scissors cycle, but dual types can stack multipliers (x4 or x1/4).",
        "4. Wild encounter rates differ by route, time of day (Gen II onward) and weather (Gen III).",
    ]
    return _pdf("Professor's Lab Notes (annotatable)", [("Field observations", paras), ("Page 2", ["(Intentionally multi-section so annotation tools have something to scroll.)"] * 12)])


def answer_key_pdf() -> bytes:
    return _pdf("Instructor Answer Key (hidden + locked file)", [("Why this file exists", [
        "This file lives in a hidden, locked folder. Students should never be able to list or download it; "
        "LTI tools using the Files API with a student token should get a 401/404 for it."])])


def unicode_name_pdf() -> bytes:
    return _pdf("Pokedex - Okido Notes", [("Filename test", [
        "The display name of this file contains non-ASCII characters (an en dash, e with acute, O with macron) "
        "and spaces. Use it to test URL encoding, Content-Disposition headers and file pickers."])])


def encounter_log(dex: list[dict], rows: int = 10000) -> bytes:
    rng = random.Random(151)
    routes = [f"Route {n}" for n in range(1, 49)] + ["Viridian Forest", "Mt. Moon", "Ilex Forest", "Granite Cave", "Safari Zone"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["encounter_id", "trainer", "location", "dex", "species", "level", "shiny", "caught", "time_of_day"])
    trainers = ["ash", "misty", "brock", "gary", "may", "brendan", "ethan", "lyra"]
    for i in range(1, rows + 1):
        m = rng.choice(dex)
        w.writerow([i, rng.choice(trainers), rng.choice(routes), m["dex"], m["name"], rng.randint(2, 60),
                    int(rng.random() < 1 / 512), int(rng.random() < .4), rng.choice(["morning", "day", "night"])])
    return buf.getvalue().encode()


def gym_leaders_json() -> bytes:
    leaders = {
        "kanto": ["Brock", "Misty", "Lt. Surge", "Erika", "Koga", "Sabrina", "Blaine", "Giovanni"],
        "johto": ["Falkner", "Bugsy", "Whitney", "Morty", "Chuck", "Jasmine", "Pryce", "Clair"],
        "hoenn": ["Roxanne", "Brawly", "Wattson", "Flannery", "Norman", "Winona", "Tate & Liza", "Wallace"],
    }
    types = {
        "kanto": ["Rock", "Water", "Electric", "Grass", "Poison", "Psychic", "Fire", "Ground"],
        "johto": ["Flying", "Bug", "Normal", "Ghost", "Fighting", "Steel", "Ice", "Dragon"],
        "hoenn": ["Rock", "Fighting", "Electric", "Fire", "Normal", "Flying", "Psychic", "Water"],
    }
    data = {region: [{"order": i + 1, "leader": n, "type": types[region][i]} for i, n in enumerate(names)] for region, names in leaders.items()}
    return (json.dumps({"_note": "JSON test asset for file preview / API download tests", "gyms": data}, ensure_ascii=False, indent=2) + "\n").encode()


TEAM_BUILDER_PY = '''"""Sample source file (code preview / syntax highlighting test asset)."""

CHART = {"Fire": {"Grass": 2.0, "Water": 0.5}, "Water": {"Fire": 2.0, "Grass": 0.5}, "Grass": {"Water": 2.0, "Fire": 0.5}}


def effectiveness(attacking: str, *defending: str) -> float:
    result = 1.0
    for t in defending:
        result *= CHART.get(attacking, {}).get(t, 1.0)
    return result


if __name__ == "__main__":
    print(effectiveness("Fire", "Grass"))  # 2.0
'''
