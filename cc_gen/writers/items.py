"""Pages, assignments, discussions/announcements, web links, LTI tool links."""

from __future__ import annotations

from ..content import Course, Renderer
from ..ids import mid, slugify
from ..package import BASIC_LTI, DISCUSSION_TOPIC, LOR, WEB_LINK, WEBCONTENT, Package
from ..xmlutil import node, root, sub, tostring
from .common import assignment_fields, html_page

IMSDT_NS = "http://www.imsglobal.org/xsd/imsccv1p1/imsdt_v1p1"
IMSWL_NS = "http://www.imsglobal.org/xsd/imsccv1p1/imswl_v1p1"


def write_pages(pkg: Package, course: Course, r: Renderer) -> None:
    d = course.dates
    used = set()
    for p in course.pages.values():
        if p["url"] in used:
            raise ValueError(f"page url collision: {p['url']}")
        used.add(p["url"])
        body = r.html(p, "body", f"page:{p['key']}")
        meta = {
            "identifier": p["id"],
            "editing_roles": p.get("editing_roles", "teachers"),
            "notify_of_update": p.get("notify_of_update", False),
            "workflow_state": "active" if p.get("published", True) else "unpublished",
            "front_page": p.get("front_page", False),
            "todo_date": d.utc(p.get("todo_date")),
            "publish_at": d.utc(p.get("publish_at")),
            "unlock_at": d.utc(p.get("unlock_at")),
            "lock_at": d.utc(p.get("lock_at")),
        }
        path = pkg.add(f"wiki_content/{p['url']}.html", html_page(p["title"], body, meta))
        pkg.resource(p["id"], WEBCONTENT, href=path, files=[path])


def write_assignments(pkg: Package, course: Course, r: Renderer) -> None:
    for pos, a in enumerate(course.assignments.values(), start=1):
        types = a.get("submission_types", ["online_text_entry"])
        if "external_tool" in types and not course.lti:
            raise ValueError(f"assignment {a['key']} uses external_tool but no lti.yaml is configured")
        html_path = f"{a['id']}/{slugify(a['title'])}.html"
        pkg.add(html_path, html_page("Assignment: " + a["title"], r.html(a, "description", f"assignment:{a['key']}")))
        el = root("assignment", identifier=a["id"])
        assignment_fields(el, a, course, submission_types=types, position=pos)
        settings_path = pkg.add(f"{a['id']}/assignment_settings.xml", tostring(el))
        pkg.resource(a["id"], LOR, href=html_path, files=[html_path, settings_path])


def write_discussions(pkg: Package, course: Course, r: Renderer) -> None:
    d = course.dates
    topics = list(course.announcements.values()) + list(course.discussions.values())
    for pos, t in enumerate(topics, start=1):
        kind = "announcement" if t["announcement"] else "discussion"
        where = f"{kind}:{t['key']}"
        cc = root("topic", ns=IMSDT_NS, schema="http://www.imsglobal.org/profile/cc/ccv1p1/ccv1p1_imsdt_v1p1.xsd")
        sub(cc, "title", t["title"])
        sub(cc, "text", r.html(t, "message", where), texttype="text/html")
        topic_file = pkg.add(f"{t['id']}.xml", tostring(cc))

        meta = root("topicMeta", identifier=t["meta_id"])
        sub(meta, "topic_id", t["id"])
        sub(meta, "title", t["title"])
        if t.get("delayed_post_at"):
            sub(meta, "delayed_post_at", d.utc(t["delayed_post_at"]))
        if t.get("lock_at"):
            sub(meta, "lock_at", d.utc(t["lock_at"]))
        sub(meta, "position", pos)
        sub(meta, "type", "announcement" if t["announcement"] else "topic")
        sub(meta, "discussion_type", t.get("discussion_type", "threaded"))
        if t.get("pinned"):
            sub(meta, "pinned", True)
        if t.get("require_initial_post"):
            sub(meta, "require_initial_post", True)
        sub(meta, "has_group_category", bool(t.get("group_category")))
        if t.get("group_category"):
            sub(meta, "group_category", t["group_category"])
        sub(meta, "workflow_state", "active" if t.get("published", True) else "unpublished")
        sub(meta, "allow_rating", bool(t.get("allow_rating", False)))
        sub(meta, "only_graders_can_rate", bool(t.get("only_graders_can_rate", False)))
        sub(meta, "sort_by_rating", bool(t.get("sort_by_rating", False)))
        if t.get("todo_date"):
            sub(meta, "todo_date", d.utc(t["todo_date"]))
        if t.get("locked"):
            sub(meta, "locked", True)
        g = t.get("graded")
        if g:
            ga = {"title": t["title"], **g}
            ae = node(meta, "assignment", identifier=g["id"])
            assignment_fields(ae, ga, course, submission_types=["discussion_topic"])
        if t.get("anonymous_state"):
            sub(meta, "anonymous_state", t["anonymous_state"])
        meta_file = pkg.add(f"{t['meta_id']}.xml", tostring(meta))
        pkg.resource(t["id"], DISCUSSION_TOPIC, files=[topic_file], dependencies=[t["meta_id"]])
        pkg.resource(t["meta_id"], LOR, href=meta_file, files=[meta_file])


def write_web_links(pkg: Package, course: Course) -> None:
    for m in course.modules:
        for it in m["items"]:
            if it["type"] != "url":
                continue
            el = root("webLink", ns=IMSWL_NS, schema="http://www.imsglobal.org/profile/cc/ccv1p1/ccv1p1_imswl_v1p1.xsd")
            sub(el, "title", it["title"])
            sub(el, "url", None, href=it["url"])
            f = pkg.add(f"{it['ref_id']}.xml", tostring(el))
            pkg.resource(it["ref_id"], WEB_LINK, files=[f])


BLTI_SCHEMA = (
    "http://www.imsglobal.org/xsd/imslticc_v1p0 http://www.imsglobal.org/xsd/lti/ltiv1p0/imslticc_v1p0.xsd "
    "http://www.imsglobal.org/xsd/imsbasiclti_v1p0 http://www.imsglobal.org/xsd/lti/ltiv1p0/imsbasiclti_v1p0p1.xsd "
    "http://www.imsglobal.org/xsd/imslticm_v1p0 http://www.imsglobal.org/xsd/lti/ltiv1p0/imslticm_v1p0.xsd "
    "http://www.imsglobal.org/xsd/imslticp_v1p0 http://www.imsglobal.org/xsd/lti/ltiv1p0/imslticp_v1p0.xsd"
)


def write_lti_tools(pkg: Package, course: Course) -> None:
    """Course-level ContextExternalTools, only when content/lti.yaml exists (no fake tools by default)."""
    if not course.lti:
        return
    from lxml import etree

    ns = {
        None: "http://www.imsglobal.org/xsd/imslticc_v1p0",
        "blti": "http://www.imsglobal.org/xsd/imsbasiclti_v1p0",
        "lticm": "http://www.imsglobal.org/xsd/imslticm_v1p0",
        "lticp": "http://www.imsglobal.org/xsd/imslticp_v1p0",
        "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    }
    B, CM, CP = (f"{{{ns[k]}}}" for k in ("blti", "lticm", "lticp"))

    def prop(parent, name, value):
        e = etree.SubElement(parent, CM + "property", name=name)
        e.text = "true" if value is True else "false" if value is False else str(value)

    for key, tool in course.lti.get("tools", {}).items():
        tid = mid("lti_tool", key)
        el = etree.Element(f"{{{ns[None]}}}cartridge_basiclti_link", nsmap=ns)
        el.set(f"{{{ns['xsi']}}}schemaLocation", BLTI_SCHEMA)
        etree.SubElement(el, B + "title").text = tool["name"]
        etree.SubElement(el, B + "description").text = tool.get("description", "")
        url = tool["launch_url"]
        etree.SubElement(el, B + ("secure_launch_url" if url.startswith("https://") else "launch_url")).text = url
        if tool.get("icon_url"):
            etree.SubElement(el, B + "icon").text = tool["icon_url"]
        vendor = etree.SubElement(el, B + "vendor")
        etree.SubElement(vendor, CP + "code").text = "unknown"
        etree.SubElement(vendor, CP + "name").text = "unknown"
        if tool.get("custom_fields"):
            cust = etree.SubElement(el, B + "custom")
            for k, v in tool["custom_fields"].items():
                prop(cust, k, v)
        ext = etree.SubElement(el, B + "extensions", platform="canvas.instructure.com")
        prop(ext, "privacy_level", tool.get("privacy_level", "public"))
        if tool.get("domain"):
            prop(ext, "domain", tool["domain"])
        version = str(tool.get("lti_version", "1.3"))
        prop(ext, "lti_version", version)
        if version == "1.3":
            prop(ext, "client_id", tool["client_id"])  # developer key must already exist on the instance
        for placement, opts in (tool.get("placements") or {}).items():
            pe = etree.SubElement(ext, CM + "options", name=placement)
            for k, v in (opts or {}).items():
                prop(pe, k, v)
        f = pkg.add(f"{tid}.xml", etree.tostring(el, xml_declaration=True, encoding="UTF-8", pretty_print=True))
        pkg.resource(tid, BASIC_LTI, files=[f])
