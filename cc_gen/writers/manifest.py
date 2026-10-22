"""imsmanifest.xml (CC 1.1, Canvas flavor) + web_resources file resources."""

from __future__ import annotations

from lxml import etree

from ..content import Course, file_id
from ..ids import mid
from ..package import WEBCONTENT, Package

CP = "http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1"
LOM = "http://ltsc.ieee.org/xsd/imsccv1p1/LOM/resource"
LOMIMSCC = "http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
SCHEMA_LOCATION = (
    "http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1 http://www.imsglobal.org/profile/cc/ccv1p1/ccv1p1_imscp_v1p2_v1p0.xsd "
    "http://ltsc.ieee.org/xsd/imsccv1p1/LOM/resource http://www.imsglobal.org/profile/cc/ccv1p1/LOM/ccv1p1_lomresource_v1p0.xsd "
    "http://ltsc.ieee.org/xsd/imsccv1p1/LOM/manifest http://www.imsglobal.org/profile/cc/ccv1p1/LOM/ccv1p1_lommanifest_v1p0.xsd"
)


def write_files(pkg: Package, course: Course, generated: dict[str, bytes]) -> list[str]:
    """Add every referenced/declared course file. Returns missing paths (referenced but not generated)."""
    missing = []
    # every generated file becomes a course file, referenced or not (realistic Files tab)
    for path in generated:
        course.files.setdefault(path, {"path": path})
    for path, meta in sorted(course.files.items()):
        if path not in generated:
            missing.append(path)
            continue
        href = pkg.add(f"web_resources/{path}", generated[path])
        pkg.resource(file_id(path), WEBCONTENT, href=href, files=[href], instructor_only=bool(meta.get("locked")))
    return missing


def write_manifest(pkg: Package, course: Course) -> None:
    E = lambda tag, ns=CP: f"{{{ns}}}{tag}"
    m = etree.Element(E("manifest"), nsmap={None: CP, "lom": LOM, "lomimscc": LOMIMSCC, "xsi": XSI})
    m.set("identifier", mid("manifest", course.settings["course_code"]))
    m.set(f"{{{XSI}}}schemaLocation", SCHEMA_LOCATION)

    md = etree.SubElement(m, E("metadata"))
    etree.SubElement(md, E("schema")).text = "IMS Common Cartridge"
    etree.SubElement(md, E("schemaversion")).text = "1.1.0"
    lom = etree.SubElement(md, E("lom", LOMIMSCC))
    gen = etree.SubElement(lom, E("general", LOMIMSCC))
    t = etree.SubElement(gen, E("title", LOMIMSCC))
    etree.SubElement(t, E("string", LOMIMSCC)).text = course.settings["title"]
    lc = etree.SubElement(lom, E("lifeCycle", LOMIMSCC))
    c = etree.SubElement(lc, E("contribute", LOMIMSCC))
    dt = etree.SubElement(c, E("date", LOMIMSCC))
    etree.SubElement(dt, E("dateTime", LOMIMSCC)).text = course.dates.anchor.isoformat()
    rights = etree.SubElement(lom, E("rights", LOMIMSCC))
    cr = etree.SubElement(rights, E("copyrightAndOtherRestrictions", LOMIMSCC))
    etree.SubElement(cr, E("value", LOMIMSCC)).text = "yes"
    desc = etree.SubElement(rights, E("description", LOMIMSCC))
    etree.SubElement(desc, E("string", LOMIMSCC)).text = "Private (Copyrighted) - http://en.wikipedia.org/wiki/Copyright"

    orgs = etree.SubElement(m, E("organizations"))
    org = etree.SubElement(orgs, E("organization"), identifier="org_1", structure="rooted-hierarchy")
    root_item = etree.SubElement(org, E("item"), identifier="LearningModules")
    for mod in course.modules:
        mi = etree.SubElement(root_item, E("item"), identifier=mod["id"])
        etree.SubElement(mi, E("title")).text = mod["title"]
        for it in mod["items"]:
            attrs = {"identifier": it["id"]}
            if it["type"] == "external_tool":  # Organization#add_module swaps ids for tool items
                attrs = {"identifier": it["id"] + "_module_item", "identifierref": it["id"]}
            elif it["type"] != "subheader":
                attrs["identifierref"] = it["ref_id"]
            ie = etree.SubElement(mi, E("item"), **attrs)
            etree.SubElement(ie, E("title")).text = it["title"]

    res_el = etree.SubElement(m, E("resources"))
    for r in pkg.resources:
        attrs = {"identifier": r.identifier, "type": r.type}
        if r.href:
            attrs["href"] = r.href
        if r.intendeduse:
            attrs["intendeduse"] = r.intendeduse
        re_ = etree.SubElement(res_el, E("resource"), **attrs)
        if r.instructor_only:
            rmd = etree.SubElement(re_, E("metadata"))
            rl = etree.SubElement(rmd, E("lom", LOM))
            edu = etree.SubElement(rl, E("educational", LOM))
            role = etree.SubElement(edu, E("intendedEndUserRole", LOM))
            etree.SubElement(role, E("source", LOM)).text = "IMSGLC_CC_Rolesv1p1"
            etree.SubElement(role, E("value", LOM)).text = "Instructor"
        for f in r.files:
            etree.SubElement(re_, E("file"), href=f)
        for dep in r.dependencies:
            etree.SubElement(re_, E("dependency"), identifierref=dep)
    pkg.add("imsmanifest.xml", etree.tostring(m, xml_declaration=True, encoding="UTF-8", pretty_print=True))
