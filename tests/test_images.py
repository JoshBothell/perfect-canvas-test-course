"""Image pipeline: masks, compression, and the fetch -> cache -> per-entry fallback chain (offline: _fetch is faked)."""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageChops, ImageFilter, ImageStat

from cc_gen import images
from cc_gen.build import build
from cc_gen.images import figure_mask, palette_png, silhouette
from cc_gen.theme import ImageSource

from conftest import ANCHOR


def _png(color, size=(40, 40), bg=(255, 255, 255, 0)):
    from PIL import ImageDraw

    im = Image.new("RGBA", size, bg)
    d = ImageDraw.Draw(im)
    d.ellipse([8, 8, 32, 32], fill=color)
    d.ellipse([16, 16, 24, 24], fill=(255, 255, 255, 255))  # white "eye" inside the figure
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _ring(bg):
    """A figure with a white eye and an enclosed transparent/white hole (like Mew's tail loop)."""
    from PIL import ImageDraw

    im = Image.new("RGBA", (60, 60), bg)
    d = ImageDraw.Draw(im)
    d.ellipse([4, 4, 56, 56], fill=(200, 30, 30, 255))
    d.ellipse([10, 26, 18, 34], fill=(255, 255, 255, 255))  # opaque white eye on the figure
    d.ellipse([30, 22, 46, 38], fill=bg)                     # enclosed background hole
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_silhouette_alpha_mask_for_transparent_png():
    raw = _ring((255, 255, 255, 0))
    assert figure_mask(Image.open(io.BytesIO(raw)))[1] == "alpha"
    sil = Image.open(io.BytesIO(silhouette(raw))).convert("RGBA")
    assert sil.getpixel((1, 1))[3] == 0, "background clear"
    assert sil.getpixel((14, 30))[3] == 255, "opaque white eye stays solid (Togepi case)"
    assert sil.getpixel((38, 30))[3] == 0, "enclosed transparent hole stays clear (Mew case)"


def test_silhouette_edge_fill_for_opaque_white_png():
    raw = _ring((255, 255, 255, 255))
    assert figure_mask(Image.open(io.BytesIO(raw)))[1] == "edge-fill"
    sil = Image.open(io.BytesIO(silhouette(raw))).convert("RGBA")
    assert sil.getpixel((1, 1))[3] == 0, "border-connected white is background"
    assert sil.getpixel((14, 30))[3] == 255, "enclosed white eye stays solid"
    assert sil.getpixel((38, 30))[3] == 255, "known limitation: enclosed white hole is filled"


@pytest.mark.parametrize("style", images.MASK_STYLES)
def test_mask_styles_hide_detail(style):
    raw = _ring((240, 200, 120, 255))  # opaque background, like a painting or photo
    out = Image.open(io.BytesIO(images.mask(raw, style)))
    assert out.size == (60, 60) and out.mode == "P"
    if style != "silhouette":
        a, b = Image.open(io.BytesIO(raw)).convert("RGB"), out.convert("RGBA").convert("RGB")
        assert ImageStat.Stat(ImageChops.difference(a, b).convert("L")).mean[0] > 3, "masked image differs from the original"
    with pytest.raises(ValueError):
        images.mask(raw, "nope")


def test_image_compression():
    """Silhouettes: 1-color palette PNG with soft edges. Art: 256-color palette, visually ~identical."""
    ring = Image.open(io.BytesIO(_ring((255, 255, 255, 0)))).convert("RGBA")
    ring.putalpha(ring.getchannel("A").filter(ImageFilter.GaussianBlur(1.5)))  # soft edge like real art
    buf = io.BytesIO()
    ring.save(buf, "PNG")
    raw = buf.getvalue()
    sil = Image.open(io.BytesIO(silhouette(raw)))
    assert sil.mode == "P" and "transparency" in sil.info
    assert sum(1 for n in sil.convert("RGBA").getchannel("A").histogram() if n) > 2, "anti-aliased edge levels kept"

    src = Image.open(io.BytesIO(raw)).convert("RGBA")
    pal = Image.open(io.BytesIO(palette_png(raw)))
    assert pal.mode == "P"
    # compare what's visible: both composited over light and dark backgrounds (hidden RGB of
    # fully transparent pixels is allowed to change)
    for bg in ((255, 255, 255, 255), (40, 40, 40, 255)):
        x = Image.new("RGBA", src.size, bg); x.alpha_composite(src)
        y = Image.new("RGBA", src.size, bg); y.alpha_composite(pal.convert("RGBA"))
        err = ImageStat.Stat(ImageChops.difference(x, y).convert("L")).mean[0]
        assert err < 6.0, (bg, err)  # real pokemon.com art: median 3.4, worst 4.7 over the figure


def test_fetched_images_used_with_per_entry_fallback(theme, monkeypatch, tmp_path):
    """Fetched images replace placeholders; anything that fails falls back per entry; 'required' fails the build."""
    source = theme.image_source
    if source is None:
        pytest.skip("theme has no image source")
    catalog = theme.catalog()
    ok_urls = {source.url_for(e) for e in catalog[::2]}
    art = _png((40, 160, 60, 255))
    monkeypatch.setattr(images, "_fetch", lambda session, url, timeout: (art, "ok") if url in ok_urls else (None, "HTTP 403"))

    pkg, course = build(theme, anchor=ANCHOR, images_mode="auto", image_cache=tmp_path, log=lambda *_: None, art_quality="original")
    first, second = catalog[0], catalog[1]
    assert pkg.files["web_resources/" + first["image"]] == art
    assert pkg.files["web_resources/" + second["image"]] != art  # fell back
    assert len(course.fetched_images) == len(ok_urls)
    assert (tmp_path / f"{str(first['id']).zfill(3)}.png").exists() and not (tmp_path / f"{str(second['id']).zfill(3)}.png").exists()
    alts = theme.images
    pages = b"".join(d for p, d in pkg.files.items() if p.startswith("wiki_content/")).decode()
    assert theme.fmt(alts["alt_fetched"], first) in pages and theme.fmt(alts["alt_placeholder"], second) in pages

    with pytest.raises(Exception, match="could not be fetched"):
        build(theme, anchor=ANCHOR, images_mode="required", image_cache=tmp_path / "empty", log=lambda *_: None)


def test_offline_falls_back_fast(monkeypatch, tmp_path):
    calls = []

    def offline(session, url, timeout):
        calls.append(url)
        return None, "connection error"

    monkeypatch.setattr(images, "_fetch", offline)
    entries = [{"id": i, "name": f"E{i}"} for i in range(1, 387)]
    rep = images.fetch_all(entries, ImageSource(url="https://example.invalid/{id}.png", cache="t"), cache_dir=tmp_path)
    assert len(calls) == 1, "one failed probe, not 386 timeouts"
    assert rep.offline and len(rep.failed) == 386 and not rep.fetched


def test_no_source_means_generated():
    rep = images.fetch_all([{"id": 1, "name": "x"}], None, mode="auto")
    assert rep.mode == "generated" and not rep.fetched


def test_jpeg_sources_are_converted_and_downscaled(monkeypatch, tmp_path):
    big = Image.new("RGB", (1200, 800), (180, 120, 60))
    buf = io.BytesIO()
    big.save(buf, "JPEG")
    monkeypatch.setattr(images, "_fetch", lambda session, url, timeout: (buf.getvalue(), "ok"))
    src = ImageSource(url="https://example.invalid/{id}.jpg", cache="t", max_size=300)
    rep = images.fetch_all([{"id": 7, "name": "x"}], src, cache_dir=tmp_path)
    out = Image.open(io.BytesIO(rep.fetched[7]))
    assert out.format == "PNG" and max(out.size) == 300
    assert (tmp_path / "007.png").read_bytes() == rep.fetched[7], "cache holds the converted PNG"
    png = _png((1, 2, 3, 255))
    assert images.normalize(png) is png, "small PNGs pass through untouched"


def test_cached_images_follow_a_lowered_max_size(tmp_path):
    """Lowering images.source.max_size takes effect without --refresh-images; images within it stay byte-identical."""
    big, small = _png((200, 30, 30, 255), size=(300, 200)), _png((30, 30, 200, 255), size=(40, 40))
    (tmp_path / "001.png").write_bytes(big)
    (tmp_path / "002.png").write_bytes(small)
    src = ImageSource(url="https://example.invalid/{id}.png", cache="t", max_size=100)
    rep = images.fetch_all([{"id": 1}, {"id": 2}], src, "auto", cache_dir=tmp_path)
    assert rep.cached == 2 and Image.open(io.BytesIO(rep.fetched[1])).size == (100, 67)
    assert rep.fetched[2] == small
