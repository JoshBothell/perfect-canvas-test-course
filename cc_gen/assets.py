"""Generated course files.

Two layers:
  * a theme-agnostic **toolkit** of deterministic file-format builders (PNG banners, placeholder
    cards, animated GIF, WebP, PDF via fpdf2, hand-zipped DOCX) that themes call from their
    ``assets(course)`` hook with their own text and colors;
  * ``generate(course)``: catalog images (fetched or placeholder), masked identify images, the
    theme's own assets, then ``<theme>/files/**`` copied verbatim.

Everything is deterministic so rebuilding produces byte-identical files.
"""

from __future__ import annotations

import io
import random
import zipfile
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFont

from .content import Course
from .images import mask, palette_png

FIXED_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ------------------------------------------------------------------ toolkit
def font(size: int):
    return ImageFont.load_default(size=size)


def hex_rgb(c: str):
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def mix(a, b, t):
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def placeholder_card(entry: dict, size: int = 120, hide_name: bool = False) -> bytes:
    """Default placeholder for themes without a ``placeholder_image`` hook: seeded gradient + label."""
    rng = random.Random(f"placeholder:{entry['id']}")
    c1 = tuple(rng.randint(60, 200) for _ in range(3))
    c2 = mix(c1, (255, 255, 255), .5)
    img = Image.new("RGB", (size, size))
    d = ImageDraw.Draw(img)
    for y in range(size):
        d.line([(0, y), (size, y)], fill=mix(c1, c2, y / size))
    d.rectangle([0, size - 24, size, size], fill=(255, 255, 255))
    label = "#??? ?????" if hide_name else f"#{str(entry['id']).zfill(3)} {entry['name']}"[:18]
    d.text((6, size - 21), label, fill=(20, 20, 20), font=font(12))
    d.rectangle([0, 0, size - 1, size - 1], outline=(30, 30, 30), width=2)
    return png(img)


def banner(title: str, subtitle: str, colors: list[str], w=1200, h=675) -> bytes:
    """Gradient banner with a concentric-ring motif (course card / banner / unit images).
    Deliberately RGB, not palette: smooth gradients band at 256 colors."""
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    cs = [hex_rgb(c) for c in colors]
    for x in range(w):
        t = x / w * (len(cs) - 1)
        i = min(int(t), len(cs) - 2)
        d.line([(x, 0), (x, h)], fill=mix(cs[i], cs[i + 1], t - i))
    for i, r in enumerate(range(260, 40, -44)):
        d.ellipse([w - 330 - r, h // 2 - r, w - 330 + r, h // 2 + r], outline=(255, 255, 255), width=6 if i % 2 else 3)
    d.text((70, h // 2 - 110), title, fill=(255, 255, 255), font=font(72), stroke_width=3, stroke_fill=(0, 0, 0))
    d.text((74, h // 2 + 10), subtitle, fill=(255, 255, 255), font=font(34), stroke_width=2, stroke_fill=(0, 0, 0))
    return png(img)


def spinner_gif(color=(220, 30, 30), frames: int = 12) -> bytes:
    """Animated GIF test asset: a two-tone disc rotating 360°."""
    out = []
    for i in range(frames):
        img = Image.new("RGB", (96, 96), (255, 255, 255))
        d = ImageDraw.Draw(img)
        d.pieslice([8, 8, 88, 88], 180 + i * 30, 360 + i * 30, fill=color, outline=(0, 0, 0), width=3)
        d.pieslice([8, 8, 88, 88], i * 30, 180 + i * 30, fill=(250, 250, 250), outline=(0, 0, 0), width=3)
        d.ellipse([38, 38, 58, 58], fill=(255, 255, 255), outline=(0, 0, 0), width=3)
        out.append(img)
    buf = io.BytesIO()
    out[0].save(buf, "GIF", save_all=True, append_images=out[1:], duration=80, loop=0)
    return buf.getvalue()


def scene_webp(caption: str) -> bytes:
    """WebP test asset: a simple landscape (field + path) with a caption."""
    img = Image.new("RGB", (640, 360), (120, 190, 90))
    d = ImageDraw.Draw(img)
    for i in range(0, 640, 40):
        d.rectangle([i, 300, i + 20, 360], fill=(90, 150, 60))
    d.rectangle([280, 0, 360, 360], fill=(214, 190, 140))
    d.text((20, 20), caption, fill=(0, 0, 0), font=font(28))
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=80)
    return buf.getvalue()


def pdf(title: str, sections: list[tuple[str, list[str]]], table: list[list[str]] | None = None, landscape=False,
        author: str = "Canvas test course (test fixture)", cell_fill: dict[str, tuple] | None = None) -> bytes:
    """Deterministic PDF. ``table`` renders a bordered grid (first row/column bold); ``cell_fill`` maps
    a cell value to an RGB fill. fpdf2's core fonts are Latin-1 only: keep PDF text ASCII-ish."""
    from fpdf import FPDF

    cell_fill = cell_fill or {}
    doc = FPDF(orientation="L" if landscape else "P")
    doc.set_creation_date(FIXED_DATE)
    doc.set_title(title)
    doc.set_author(author)
    doc.add_page()
    doc.set_font("Helvetica", "B", 20)
    doc.cell(0, 12, title, new_x="LMARGIN", new_y="NEXT")
    for heading, paras in sections:
        doc.set_font("Helvetica", "B", 13)
        doc.cell(0, 9, heading, new_x="LMARGIN", new_y="NEXT")
        doc.set_font("Helvetica", "", 11)
        for p in paras:
            doc.multi_cell(0, 6, p, new_x="LMARGIN", new_y="NEXT")
            doc.ln(1)
    if table:
        doc.set_font("Helvetica", "", 7)
        colw = (doc.w - 20) / len(table[0])
        for ri, row in enumerate(table):
            for ci, cell in enumerate(row):
                bold = ri == 0 or ci == 0
                doc.set_font("Helvetica", "B" if bold else "", 7)
                fill = cell in cell_fill
                if fill:
                    doc.set_fill_color(*cell_fill[cell])
                doc.cell(colw, 6, cell, border=1, align="C", fill=fill)
            doc.ln()
    return bytes(doc.output())


def docx(title: str, paragraphs: list[str]) -> bytes:
    """Minimal valid .docx (title + paragraphs), zipped with fixed timestamps."""
    from xml.sax.saxutils import escape as x

    body = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{x(p)}</w:t></w:r></w:p>" for p in paragraphs)
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
           f'<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr><w:r><w:rPr><w:b/><w:sz w:val="40"/></w:rPr><w:t>{x(title)}</w:t></w:r></w:p>'
           f"{body}</w:body></w:document>")
    parts = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/document.xml": doc,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), data)
    return buf.getvalue()


# ----------------------------------------------------------------- generate
def placeholder(course: Course, entry: dict, hide_name: bool = False) -> bytes:
    hook = course.theme.hook("placeholder_image")
    return hook(entry, hide_name) if hook else placeholder_card(entry, hide_name=hide_name)


def masked_image(course: Course, png_bytes: bytes, entry: dict) -> bytes:
    hook = course.theme.hook("mask_image")
    return hook(png_bytes, entry) if hook else mask(png_bytes, course.theme.images.get("mask", "silhouette"))


def generate(course: Course) -> dict[str, bytes]:
    """Return {course-files path: bytes} for every generated asset."""
    out: dict[str, bytes] = {}
    for e in course.catalog:
        # fetched image when the theme's source provided one, otherwise the generated placeholder
        art = course.fetched_images.get(e["id"])
        if art is None:
            out[e["image"]] = palette_png(placeholder(course, e))
        else:
            out[e["image"]] = art if course.art_quality == "original" else palette_png(art)
    by_id = {e["id"]: e for e in course.catalog}
    for path, eid in course.masked.items():
        art = course.fetched_images.get(eid)
        out[path] = masked_image(course, art, by_id[eid]) if art else palette_png(placeholder(course, by_id[eid], hide_name=True))
    hook = course.theme.hook("assets")
    if hook:
        for path, data in hook(course).items():
            if path in out:
                raise ValueError(f"theme asset {path!r} collides with a catalog image")
            out[path] = data

    static = course.root / "files"
    if static.exists():
        for p in sorted(static.rglob("*")):
            if p.is_file() and not p.name.startswith("."):
                out[p.relative_to(static).as_posix()] = p.read_bytes()
    return out
