"""Static validation of a built .imscc (no Canvas needed).

Checks: well-formed XML, Canvas XSD for cccv1p0 documents, manifest integrity (hrefs, ids,
dependencies, organization refs), module_meta refs, and every $..._REFERENCE$ placeholder in HTML.
Optional hygiene check greps the package for distinctive strings pulled from a local reference
export, so client data can never leak into the fixture.
"""

from __future__ import annotations

import re
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

XSD_PATH = Path(__file__).parent / "xsd" / "cccv1p0.xsd"
CANVAS_NS = "http://canvas.instructure.com/xsd/cccv1p0"
CP = "{http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1}"
# Elements Canvas's own exporter emits (verified in lib/cc/*.rb and in real exports) that the
# published cccv1p0.xsd has not caught up with. Reported as notes, not errors.
STALE_XSD_ELEMENTS = {"category", "points_based", "late_policy", "locked"}

REF_RE = re.compile(r"\$(WIKI_REFERENCE|CANVAS_OBJECT_REFERENCE|CANVAS_COURSE_REFERENCE|IMS-CC-FILEBASE)\$/([^\"'<>\s]*)")


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    stale_xsd: set = field(default_factory=set)

    @property
    def ok(self):
        return not self.errors


def validate(path: Path, hygiene_against: Path | None = None, forbid_themes=(), allow_terms=()) -> Report:
    """``forbid_themes``: Theme objects whose brand_terms / catalog names must not appear (commercial themes);
    ``allow_terms``: exceptions (the checked theme's theme.yaml ``allow_terms``)."""
    rep = Report()
    z = zipfile.ZipFile(path)
    names = set(z.namelist())
    if "imsmanifest.xml" not in names:
        rep.errors.append("missing imsmanifest.xml")
        return rep

    schema = etree.XMLSchema(etree.parse(str(XSD_PATH)))
    docs = {}
    for n in sorted(names):
        if n.endswith((".xml", ".qti")):
            try:
                docs[n] = etree.fromstring(z.read(n))
            except etree.XMLSyntaxError as e:
                rep.errors.append(f"{n}: malformed XML: {e}")
                continue
            if etree.QName(docs[n]).namespace == CANVAS_NS and not schema.validate(docs[n]):
                for err in schema.error_log:
                    m = re.search(r"\{[^}]+\}(\w+)'", err.message)
                    if m and m.group(1) in STALE_XSD_ELEMENTS:
                        rep.stale_xsd.add(m.group(1))
                    else:
                        rep.errors.append(f"{n}:{err.line}: XSD: {err.message}")

    man = docs.get("imsmanifest.xml")
    if man is None:
        return rep
    schema_el = man.find(f"{CP}metadata/{CP}schema")
    if schema_el is None or schema_el.text != "IMS Common Cartridge":
        rep.errors.append("manifest <schema> must be 'IMS Common Cartridge'")

    resources = {}
    referenced = set()
    for r in man.iter(f"{CP}resource"):
        rid = r.get("identifier")
        if rid in resources:
            rep.errors.append(f"duplicate resource identifier {rid}")
        resources[rid] = r
        for f in r.findall(f"{CP}file"):
            href = f.get("href")
            referenced.add(href)
            if href not in names:
                rep.errors.append(f"resource {rid}: file {href} not in package")
        if r.get("href") and r.get("href") not in names:
            rep.errors.append(f"resource {rid}: href {r.get('href')} not in package")
    for r in resources.values():
        for d in r.findall(f"{CP}dependency"):
            if d.get("identifierref") not in resources:
                rep.errors.append(f"resource {r.get('identifier')}: dependency {d.get('identifierref')} missing")
    if not any(r.get("href") == "course_settings/canvas_export.txt" for r in resources.values()):
        rep.errors.append("no course_settings/canvas_export.txt resource: Canvas would treat this as plain Common Cartridge")
    for orphan in sorted(names - referenced - {"imsmanifest.xml"}):
        if not orphan.endswith("/"):
            rep.warnings.append(f"file not listed by any resource: {orphan}")

    org_items = {}
    for it in man.iter(f"{CP}item"):
        org_items[it.get("identifier")] = it
        ref = it.get("identifierref")
        if ref and ref not in resources and ref not in org_items:
            # ContextExternalTool items reference their own module item id (Canvas quirk)
            if not any(x.get("identifier", "").startswith(ref) for x in man.iter(f"{CP}item")):
                rep.errors.append(f"organization item {it.get('identifier')} -> missing resource {ref}")

    mm = docs.get("course_settings/module_meta.xml")
    item_ids = set()
    tool_ids = {rid for rid, r in resources.items() if r.get("type") == "imsbasiclti_xmlv1p0"}
    if mm is not None:
        ns = {"c": CANVAS_NS}
        mod_ids = {m.get("identifier") for m in mm.findall("c:module", ns)}
        for item in mm.iterfind(".//c:items/c:item", ns):
            item_ids.add(item.get("identifier"))
            ct = item.findtext("c:content_type", namespaces=ns)
            ref = item.findtext("c:identifierref", namespaces=ns)
            if ct == "ContextModuleSubHeader":
                continue
            if ct == "ContextExternalTool":
                if ref not in tool_ids:
                    rep.warnings.append(f"module item {item.get('identifier')}: tool {ref} not in package (must exist on the account)")
                continue
            if ref not in resources and not _is_embedded_assignment(ref, docs):
                rep.errors.append(f"module item {item.get('identifier')} ({ct}) -> missing {ref}")
        for p in mm.iterfind(".//c:prerequisite/c:identifierref", ns):
            if p.text not in mod_ids:
                rep.errors.append(f"prerequisite -> unknown module {p.text}")
        for cr in mm.iterfind(".//c:completionRequirement/c:identifierref", ns):
            if cr.text not in item_ids:
                rep.errors.append(f"completion requirement -> unknown module item {cr.text}")

    # HTML placeholder links
    page_ids = {rid for rid, r in resources.items() if (r.get("href") or "").startswith("wiki_content/")}
    object_ids = set(resources) | _embedded_ids(docs)
    n_links = 0
    for n in sorted(names):
        if not n.endswith((".html", ".xml", ".qti")):
            continue
        text = z.read(n).decode("utf-8", "replace")
        for kind, rest in REF_RE.findall(text):
            n_links += 1
            rest = rest.replace("&amp;", "&")
            if kind == "WIKI_REFERENCE":
                pid = rest.split("/")[1].split("?")[0] if "/" in rest else rest
                if pid not in page_ids:
                    rep.errors.append(f"{n}: $WIKI_REFERENCE$ -> unknown page {pid}")
            elif kind == "CANVAS_OBJECT_REFERENCE":
                oid = rest.split("/")[1].split("?")[0]
                if oid not in object_ids:
                    rep.errors.append(f"{n}: $CANVAS_OBJECT_REFERENCE$/{rest} -> unknown object")
            elif kind == "IMS-CC-FILEBASE":
                fpath = "web_resources/" + urllib.parse.unquote(rest.split("?")[0])
                if fpath not in names:
                    rep.errors.append(f"{n}: file link -> missing {fpath}")
            elif kind == "CANVAS_COURSE_REFERENCE" and rest.startswith("modules/items/"):
                if rest.split("/")[2].split("?")[0] not in item_ids:
                    rep.errors.append(f"{n}: module item link -> unknown {rest}")

    rep.stats = {
        "files_in_package": len(names),
        "resources": len(resources),
        "modules": len(mm.findall(f"{{{CANVAS_NS}}}module")) if mm is not None else 0,
        "module_items": len(item_ids),
        "placeholder_links": n_links,
        "by_type": _count_types(resources),
    }

    if hygiene_against:
        rep.errors.extend(_hygiene(z, Path(hygiene_against)))
    for t in forbid_themes:
        rep.errors.extend(forbidden_terms(z, t, allow_terms))
    return rep


def _embedded_ids(docs):
    """Assignments embedded in quiz/topic meta (graded quizzes & discussions) are linkable objects."""
    ids = set()
    for n, d in docs.items():
        if etree.QName(d).localname in ("quiz", "topicMeta"):
            for a in d.iter(f"{{{CANVAS_NS}}}assignment"):
                ids.add(a.get("identifier"))
            if etree.QName(d).localname == "topicMeta":
                ids.add(d.findtext(f"{{{CANVAS_NS}}}topic_id"))
    return ids


def _is_embedded_assignment(ref, docs):
    return ref in _embedded_ids(docs)


def _count_types(resources):
    out = {}
    for r in resources.values():
        t = r.get("type")
        href = r.get("href") or ""
        if t == "webcontent":
            t = "webcontent:page" if href.startswith("wiki_content/") else "webcontent:file"
        out[t] = out.get(t, 0) + 1
    return dict(sorted(out.items()))


def _hygiene(z: zipfile.ZipFile, reference: Path) -> list[str]:
    """Extract distinctive strings from a reference export and make sure none appear in ours."""
    ref = zipfile.ZipFile(reference)
    terms: set[str] = set()
    for n in ref.namelist():
        if n.endswith(".xml") or n.endswith(".html"):
            try:
                doc = etree.fromstring(ref.read(n))
            except etree.XMLSyntaxError:
                continue
            for el in doc.iter():
                tag = etree.QName(el).localname if isinstance(el.tag, str) else ""
                if tag in ("title", "course_name", "course_code", "root_account_name", "canvas_domain", "external_tool_url", "secure_launch_url", "domain", "string") and el.text:
                    t = el.text.strip()
                    if len(t) >= 8:
                        terms.add(t)
                if tag == "property" and el.get("name") in ("domain",) and el.text:
                    terms.add(el.text.strip())
            for v in doc.xpath("//@identifier"):
                if len(v) > 20:
                    terms.add(v)
    for n in ref.namelist():
        stem = Path(n).stem
        if len(stem) >= 12 and "-" in stem:
            terms.add(stem)
    generic = {"Syllabus", "IMS Common Cartridge", "Private (Copyrighted) - http://en.wikipedia.org/wiki/Copyright", "unknown"}
    terms -= generic
    blob = b"\n".join(z.read(n) for n in z.namelist())
    hits = sorted(t for t in terms if t.encode() in blob)
    return [f"hygiene: string from reference export found in package: {t!r}" for t in hits]


def theme_terms(theme) -> set[str]:
    """Strings that identify a theme: its brand_terms plus every catalog entry name, minus its common_words."""
    names = {e["name"] for e in theme.catalog()} - {str(x) for x in theme.config.get("common_words") or []}
    return {str(x) for x in theme.config.get("brand_terms") or []} | names


def forbidden_terms(z: zipfile.ZipFile, theme, allow=()) -> list[str]:
    """Whole-word, case-insensitive search for another theme's terms in every text file and file name.
    ``allow``: terms the checking theme may legitimately use (its theme.yaml ``allow_terms``)."""
    import unicodedata

    terms = theme_terms(theme) - set(allow)
    texts = [n for n in z.namelist()]
    for n in z.namelist():
        if n.endswith((".xml", ".qti", ".html", ".txt", ".csv", ".json", ".py", ".vtt", ".svg")):
            texts.append(z.read(n).decode("utf-8", "replace"))
    blob = unicodedata.normalize("NFC", "\n".join(texts))
    hits = sorted(t for t in terms if re.search(rf"(?<!\w){re.escape(unicodedata.normalize('NFC', t))}(?!\w)", blob, re.IGNORECASE))
    return [f"forbidden: {theme.name} term found in package: {t!r}" for t in hits]
