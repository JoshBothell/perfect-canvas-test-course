"""One-time curation of the Public Domain Academy catalog (NOT part of `cc build`).

Picks 386 public-domain artworks (151 before 1800, 100 from 1800-1899, 135 from 1900-1930) from two
open-access museum APIs and writes the committed source of truth:

  data/collection.csv     one row per work, with source + licence evidence columns
  data/provenance.jsonl   the CC0 API fields each row was built from (licence evidence)

Sources (both checked to be CC0 for public-domain objects):
  * Art Institute of Chicago API: `is_public_domain` objects; all fields are CC0 EXCEPT `description`
    (CC-BY), which is never requested. Images via IIIF at the size AIC asks clients to use (843px).
  * The Met Collection API: `isPublicDomain` objects (Open Access, CC0).

The build only reads the CSV, so re-running this script is optional and may pick different works
(museum search relevance changes over time). Raw responses are cached in build/cache/pd-curation/.

    uv run python themes/public-domain/tools/curate_catalog.py [--contact-sheet PATH]
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests

THEME = Path(__file__).resolve().parent.parent
REPO = THEME.parent.parent
sys.path.insert(0, str(REPO))

from cc_gen.ids import slugify  # noqa: E402
from cc_gen.theme import load_theme  # noqa: E402
from cc_gen.validate import theme_terms  # noqa: E402

CACHE = REPO / "build" / "cache" / "pd-curation"
UA = "public-domain-academy-canvas-fixture/0.1 (catalog curation; josh.bothell@atomicjolt.com)"
ERAS = {1: (1000, 1799, 151), 2: (1800, 1899, 100), 3: (1900, 1930, 135)}  # era: (from, to, count)
CC0 = "https://creativecommons.org/publicdomain/zero/1.0/"
AIC_TYPES = ["Painting"]
MET_DEPARTMENTS = [11, 1, 6, 21]  # European Paintings, American Wing, Asian Art, Modern and Contemporary Art

GENERIC = re.compile(r"^(untitled|landscape|seascape|portrait|self-portrait|still life|study|sketch|figure|head|bust|interior|"
                     r"composition|fragment|flowers|vase of flowers|madonna and child|virgin and child|"
                     r"portrait of an? (man|woman|lady|gentleman|young man|young woman|boy|girl|child|old man|old woman))"
                     r"( \(.*\))?$", re.I)
UNSUITABLE = re.compile(r"nude|nudity|naked|erotic|bather|venus|leda|dana[eë]|bathsheba|susanna|toilette|martyr|execution|"
                        r"crucifi|massacre|corpse|skull|death|dead|lucretia|judith|salome|slaughter|flagellation|lamentation|"
                        r"entombment|pieta|pietà|battle", re.I)

session = requests.Session()
session.headers.update({"User-Agent": UA, "AIC-User-Agent": UA})


def cached(name: str, fetch):
    path = CACHE / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text())
    data = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def get_json(method: str, url: str, **kw):
    for attempt in range(6):
        r = session.request(method, url, timeout=30, **kw)
        if r.status_code in (403, 429, 502, 503):  # the Met's CDN answers bursts with 403
            time.sleep(5 * 2 ** attempt)
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


# ---------------------------------------------------------------------- AIC
AIC_FIELDS = ["id", "title", "artist_title", "artist_display", "date_display", "date_start", "date_end", "image_id",
              "is_public_domain", "artwork_type_title", "medium_display", "credit_line", "term_titles", "subject_titles",
              "api_link"]


def aic_candidates(era: int, lo: int, hi: int, limit: int = 600) -> list[dict]:
    out, page = [], 1
    while len(out) < limit:
        body = {"query": {"bool": {"filter": [
            {"term": {"is_public_domain": True}}, {"terms": {"artwork_type_title.keyword": AIC_TYPES}},
            {"exists": {"field": "image_id"}}, {"range": {"date_end": {"gte": lo, "lte": hi}}}]}},
            "fields": AIC_FIELDS, "limit": 100, "page": page}
        data = cached(f"aic-era{era}-p{page}", lambda: get_json("POST", "https://api.artic.edu/api/v1/artworks/search", json=body))
        rows = data.get("data") or []
        out += rows
        if page >= data["pagination"]["total_pages"] or not rows:
            break
        page += 1
        time.sleep(1.1)  # AIC asks anonymous clients to stay under 60 requests/minute
    return [{
        "source": "aic", "source_id": r["id"], "title": r.get("title") or "", "artist": r.get("artist_title") or "",
        "date_display": r.get("date_display") or "", "year": r.get("date_end"),
        "object_url": f"https://www.artic.edu/artworks/{r['id']}",
        "image_url": f"https://www.artic.edu/iiif/2/{r['image_id']}/full/843,/0/default.jpg",
        "api_url": r.get("api_link") or f"https://api.artic.edu/api/v1/artworks/{r['id']}",
        "pd_flag_field": "is_public_domain", "pd_flag": r.get("is_public_domain"),
        "terms": " ".join((r.get("term_titles") or []) + (r.get("subject_titles") or [])),
        "raw": {k: r.get(k) for k in AIC_FIELDS},
    } for r in out if r.get("is_public_domain") and r.get("image_id")]


# ---------------------------------------------------------------------- Met
MET = "https://collectionapi.metmuseum.org/public/collection"


def met_object(oid: int) -> dict:
    try:
        return get_json("GET", f"{MET}/v1/objects/{oid}")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:  # search index lists some withdrawn objects
            return {}
        raise


def met_candidates(era: int, lo: int, hi: int, limit: int = 300) -> list[dict]:
    ids: list[int] = []
    for dept in MET_DEPARTMENTS:
        params = {"departmentId": dept, "hasImages": "true", "isPublicDomain": "true", "dateBegin": lo, "dateEnd": hi,
                  "q": "painting", "limit": 100, "offset": 0}
        while True:
            data = cached(f"met-era{era}-d{dept}-o{params['offset']}", lambda: get_json("GET", f"{MET}/v1.1/search", params=params))
            ids += data.get("objectIDs") or []
            params["offset"] += 100
            if params["offset"] >= min(data.get("total") or 0, 400):
                break
    out = []
    for oid in dict.fromkeys(ids):
        if len(out) >= limit:
            break
        o = cached(f"met-obj-{oid}", lambda: (time.sleep(0.5), met_object(oid))[1])
        end = o.get("objectEndDate")
        if not (o.get("isPublicDomain") and o.get("primaryImageSmall") and end is not None and lo <= end <= hi):
            continue
        if "paint" not in (o.get("classification", "") + o.get("objectName", "") + o.get("medium", "")).lower():
            continue
        out.append({
            "source": "met", "source_id": oid, "title": o.get("title") or "", "artist": o.get("artistDisplayName") or "",
            "date_display": o.get("objectDate") or str(end), "year": end, "object_url": o.get("objectURL") or "",
            "image_url": o["primaryImageSmall"], "api_url": f"{MET}/v1/objects/{oid}",
            "pd_flag_field": "isPublicDomain", "pd_flag": o.get("isPublicDomain"),
            "terms": " ".join(t.get("term", "") for t in (o.get("tags") or [])),
            "raw": {k: o.get(k) for k in ("objectID", "title", "artistDisplayName", "objectDate", "objectBeginDate", "objectEndDate",
                                          "isPublicDomain", "primaryImageSmall", "objectURL", "department", "classification",
                                          "medium", "creditLine", "rightsAndReproduction", "tags")},
        })
    return out


# ------------------------------------------------------------------- select
def clean_title(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def suitable(c: dict, forbidden: re.Pattern, seen: set[str]) -> bool:
    t = clean_title(c["title"])
    key = slugify(t)
    if not t or len(t) > 60 or not key or key in seen or c["image_url"] in seen or GENERIC.match(t):  # some objects share one image
        return False
    if UNSUITABLE.search(f"{t} {c['terms']}") or forbidden.search(f"{t} {c['artist']}"):
        return False
    if not t.isprintable():
        return False
    seen.update((key, c["image_url"]))
    return True


def select() -> list[dict]:
    terms = sorted(theme_terms(load_theme("pokemon")), key=len, reverse=True)
    forbidden = re.compile("|".join(rf"(?<!\w){re.escape(t)}(?!\w)" for t in terms), re.I)
    seen: set[str] = set()
    rows: list[dict] = []
    for era, (lo, hi, n) in ERAS.items():
        aic, met = aic_candidates(era, lo, hi), met_candidates(era, lo, hi)
        aic = [c for c in aic if suitable(c, forbidden, seen)]
        met = [c for c in met if suitable(c, forbidden, seen)]
        print(f"era {era} ({lo}-{hi}): {len(aic)} AIC + {len(met)} Met suitable candidates, need {n}", file=sys.stderr)
        picked, i = [], 0
        while len(picked) < n and (i < len(aic) or i < len(met)):  # interleave the two museums
            for pool in (aic, met):
                if i < len(pool) and len(picked) < n:
                    picked.append(pool[i])
            i += 1
        if len(picked) < n:
            sys.exit(f"era {era}: only {len(picked)} suitable works (need {n}); widen the queries")
        picked.sort(key=lambda c: (c["year"], c["source"], c["source_id"]))
        for c in picked:
            c["era"] = era
        rows += picked
    for i, c in enumerate(rows, 1):
        c["id"] = i
        c["title"] = clean_title(c["title"])
        c["artist"] = clean_title(c["artist"]) or "Unknown artist"
    return rows


COLUMNS = ["id", "title", "artist", "date_display", "year", "era", "source", "source_id", "object_url", "image_url",
           "license", "license_url", "pd_flag_field", "retrieved"]


def write(rows: list[dict], contact_sheet: Path | None) -> None:
    today = date.today().isoformat()
    with open(THEME / "data" / "collection.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, COLUMNS, lineterminator="\n")
        w.writeheader()
        for c in rows:
            w.writerow({**{k: c.get(k, "") for k in COLUMNS}, "license": "CC0 1.0", "license_url": CC0, "retrieved": today})
    with open(THEME / "data" / "provenance.jsonl", "w", encoding="utf-8") as f:
        for c in rows:
            f.write(json.dumps({"id": c["id"], "source": c["source"], "api_url": c["api_url"], "retrieved": today,
                                "license": "CC0 1.0", "fields": c["raw"]}, ensure_ascii=False, sort_keys=True) + "\n")
    if contact_sheet:
        cells = "".join(f'<figure><img loading="lazy" src="{html.escape(c["image_url"])}" alt=""><figcaption>#{c["id"]:03d} '
                        f'{html.escape(c["title"])}<br><small>{html.escape(c["artist"])}, {html.escape(str(c["date_display"]))} '
                        f'({c["source"]})</small></figcaption></figure>' for c in rows)
        contact_sheet.write_text("<!doctype html><meta charset=utf-8><title>PDA catalog contact sheet</title><style>"
                                 "body{font:12px sans-serif;display:flex;flex-wrap:wrap;gap:8px}figure{width:180px;margin:0}"
                                 "img{width:180px;height:180px;object-fit:contain;background:#eee}</style>" + cells, encoding="utf-8")
    print(f"wrote {len(rows)} rows to data/collection.csv and data/provenance.jsonl", file=sys.stderr)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--contact-sheet", type=Path, default=None, help="also write an HTML contact sheet for human review")
    args = ap.parse_args()
    write(select(), args.contact_sheet)
