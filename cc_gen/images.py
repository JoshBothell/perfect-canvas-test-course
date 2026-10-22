"""Catalog images: fetch from the theme's image source (with cache + per-entry fallback), and the
theme-agnostic image utilities (figure masks, silhouettes, pixelate/blur masks, palette compression).

The source is ``theme.yaml`` ``images.source`` (an ``ImageSource``: a URL template over catalog
entry fields + a cache dir name). Modes:
  auto       try cache, then network; any entry that fails falls back to the generated placeholder
  required   same, but the build fails if any entry can't be obtained ("official" is accepted as an alias)
  generated  never touch the network (offline / deterministic CI builds)

Downloads are cached under build/cache/<source.cache>/<id>.png, so after one successful build the
images are available offline and rebuilds are byte-identical.
"""

from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import requests
from PIL import Image, ImageFilter

from .theme import ImageSource

CACHE_ROOT = Path(__file__).resolve().parent.parent / "build" / "cache"
PNG_SIG = b"\x89PNG\r\n\x1a\n"
MODES = ("auto", "required", "generated")
MODE_ALIASES = {"official": "required"}
ART_QUALITY = ("palette", "original")
MASK_STYLES = ("silhouette", "pixelate", "blur")


@dataclass
class ArtReport:
    mode: str
    label: str = "remote images"
    fetched: dict = field(default_factory=dict)  # entry id -> PNG bytes
    cached: int = 0
    downloaded: int = 0
    failed: dict = field(default_factory=dict)  # entry id -> reason
    offline: bool = False

    def summary(self, total: int) -> str:
        if self.mode == "generated":
            return f"images: {total} generated placeholders (--images generated)"
        s = (f"images: {len(self.fetched)}/{total} from {self.label} "
             f"({self.cached} cached, {self.downloaded} downloaded)")
        if self.failed:
            reason = "network unreachable" if self.offline else ", ".join(
                f"#{_cache_stem(d)} {r}" for d, r in sorted(self.failed.items())[:5]) + (" ..." if len(self.failed) > 5 else "")
            s += f"; {len(self.failed)} fell back to generated placeholders ({reason})"
        return s


def _cache_stem(entry_id) -> str:
    return str(entry_id).zfill(3)


def _valid_png(data: bytes) -> bool:
    if not data.startswith(PNG_SIG):
        return False
    try:
        Image.open(io.BytesIO(data)).verify()
        return True
    except Exception:
        return False


def _valid_image(data: bytes) -> bool:
    try:
        Image.open(io.BytesIO(data)).verify()
        return True
    except Exception:
        return False


def normalize(data: bytes, max_size: int | None = None) -> bytes:
    """Fetched bytes -> PNG. PNGs within ``max_size`` pass through untouched (byte-identical); anything
    else (JPEG/WebP/GIF, or larger than max_size px on its long side) is converted / downscaled."""
    img = Image.open(io.BytesIO(data))
    too_big = max_size and max(img.size) > max_size
    if data.startswith(PNG_SIG) and not too_big:
        return data
    img = img.convert("RGBA")
    if too_big:
        img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _fetch(session: requests.Session, url: str, timeout: float) -> tuple[bytes | None, str]:
    try:
        r = session.get(url, timeout=timeout)
    except requests.exceptions.ConnectionError:
        return None, "connection error"
    except requests.exceptions.Timeout:
        return None, "timeout"
    except requests.RequestException as e:
        return None, type(e).__name__
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    if not _valid_image(r.content):
        return None, "not a decodable image"
    return r.content, "ok"


def fetch_all(entries: list[dict], source: ImageSource | None, mode: str = "auto", cache_dir: Path | None = None,
              refresh: bool = False, timeout: float = 10.0, workers: int = 8) -> ArtReport:
    """Fetch one PNG per catalog entry (keyed by entry ``id``). No source = everything generated."""
    mode = MODE_ALIASES.get(mode, mode)
    rep = ArtReport(mode=mode if source else "generated", label=source.label if source else "")
    if mode == "generated" or source is None:
        return rep
    cache_dir = cache_dir or CACHE_ROOT / source.cache
    cache_dir.mkdir(parents=True, exist_ok=True)

    todo = []
    for e in entries:
        f = cache_dir / f"{_cache_stem(e['id'])}.png"
        if not refresh and f.exists() and _valid_png(data := f.read_bytes()):
            rep.fetched[e["id"]] = data
            rep.cached += 1
        elif source.url_for(e):
            todo.append(e)
        else:
            rep.failed[e["id"]] = "no URL for entry"
    if not todo:
        return rep

    session = requests.Session()
    session.headers["User-Agent"] = source.user_agent

    # Probe once first so an offline build fails fast instead of waiting out hundreds of timeouts.
    probe = todo[0]
    data, why = _fetch(session, source.url_for(probe), timeout)
    if data is None and why in ("connection error", "timeout"):
        rep.offline = True
        rep.failed.update({e["id"]: why for e in todo})
        return rep
    results = {probe["id"]: (data, why)}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for e, res in zip(todo[1:], pool.map(lambda e: _fetch(session, source.url_for(e), timeout), todo[1:])):
            results[e["id"]] = res

    for eid, (data, why) in results.items():
        if data is None:
            rep.failed[eid] = why
            continue
        try:
            data = normalize(data, source.max_size)
        except Exception as e:  # decodable header but broken pixels, etc.
            rep.failed[eid] = f"conversion failed ({type(e).__name__})"
            continue
        (cache_dir / f"{_cache_stem(eid)}.png").write_bytes(data)
        rep.fetched[eid] = data
        rep.downloaded += 1
    return rep


def background_mask(img: Image.Image, white: int = 240, alpha: int = 40) -> Image.Image:
    """'L' mask, 255 = background: near-white or transparent pixels connected to the image border.

    Fallback for images WITHOUT a transparent background: the white background is used as a rough
    mask via a flood fill from the edges, so white *inside* the figure (eyes, Togepi's shell)
    stays part of the figure. Enclosed background (e.g. inside Mew's tail loop) gets filled.
    """
    from PIL import ImageDraw

    rgba = img.convert("RGBA")
    r, g, b, a = rgba.split()
    is_white = Image.merge("RGB", (r, g, b)).convert("L").point(lambda v: 255 if v >= white else 0)
    # require all channels high, not just luminance (so pale yellow != background)
    for ch in (r, g, b):
        is_white = Image.composite(is_white, Image.new("L", img.size, 0), ch.point(lambda v: 255 if v >= white else 0))
    is_clear = a.point(lambda v: 255 if v < alpha else 0)
    candidate = Image.composite(Image.new("L", img.size, 255), is_white, is_clear)
    # pad by 1px so a single fill from the corner reaches every border-connected region
    padded = Image.new("L", (img.width + 2, img.height + 2), 255)
    padded.paste(candidate, (1, 1))
    ImageDraw.floodfill(padded, (0, 0), 128)
    return padded.crop((1, 1, img.width + 1, img.height + 1)).point(lambda v: 255 if v == 128 else 0)


def has_transparent_background(img: Image.Image, clear: int = 16, share: float = 0.98) -> bool:
    """True when the PNG carries real transparency: an alpha channel and a (nearly) fully clear border.

    (All 386 pokemon.com images verified on 2026-10-21: RGBA with 100% transparent borders.)
    """
    if "A" not in img.getbands() and not (img.mode == "P" and "transparency" in img.info):
        return False
    a = img.convert("RGBA").getchannel("A")
    w, h = a.size
    border = [a.getpixel((x, y)) for x in range(w) for y in (0, h - 1)] + \
             [a.getpixel((x, y)) for y in range(1, h - 1) for x in (0, w - 1)]
    return sum(v < clear for v in border) / len(border) >= share


def figure_mask(img: Image.Image) -> tuple[Image.Image, str]:
    """('L' mask, method). 255 = figure.

    alpha:     the image's own alpha channel (soft anti-aliased edges; holes such as the inside of
               Mew's tail loop stay clear; opaque white pixels on the figure stay solid)
    edge-fill: fallback for opaque white backgrounds: near-white pixels connected to the border
    """
    if has_transparent_background(img):
        return img.convert("RGBA").getchannel("A"), "alpha"
    return background_mask(img).point(lambda v: 0 if v else 255), "edge-fill"


def silhouette(png: bytes, color=(24, 32, 72), alpha_levels: int = 16) -> bytes:
    """Solid one-color silhouette wherever the figure mask is (needs a transparent or plain white background).

    Written as a palette PNG: one color at ``alpha_levels`` opacities (the levels only matter on the
    anti-aliased edge). ~1.8 KB vs ~6.7 KB for the equivalent RGBA PNG, visually identical.
    """
    figure, _ = figure_mask(Image.open(io.BytesIO(png)))
    step = 255 / (alpha_levels - 1)
    index = figure.point(lambda v: round(v / step))           # palette index = alpha level
    out = index.convert("P")
    out.putpalette(list(color) * alpha_levels)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True, transparency=bytes(round(i * step) for i in range(alpha_levels)))
    return buf.getvalue()


def palette_png(png: bytes, colors: int = 256) -> bytes:
    """Lossy-but-visually-identical 256-color RGBA->palette PNG (~79% smaller on transparent cut-out art)."""
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    q = img.quantize(colors, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.FLOYDSTEINBERG)
    buf = io.BytesIO()
    q.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def pixelate(png: bytes, blocks: int = 10) -> bytes:
    """Mask for images without a clean background (paintings, photos): ``blocks`` px across, scaled back up."""
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    w, h = img.size
    small = img.resize((blocks, max(1, round(blocks * h / w))), Image.Resampling.BOX)
    return palette_png(_to_png(small.resize((w, h), Image.Resampling.NEAREST)), colors=64)


def blur(png: bytes, radius: float | None = None) -> bytes:
    """Mask for images without a clean background: heavy Gaussian blur (default radius = 6% of width)."""
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    return palette_png(_to_png(img.filter(ImageFilter.GaussianBlur(radius or img.width * 0.06))))


def mask(png: bytes, style: str = "silhouette") -> bytes:
    """The masked image an identify question shows. ``style`` comes from theme.yaml ``images.mask``."""
    if style == "silhouette":
        return silhouette(png)
    if style == "pixelate":
        return pixelate(png)
    if style == "blur":
        return blur(png)
    raise ValueError(f"unknown mask style {style!r} (expected one of {MASK_STYLES})")


def _to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
