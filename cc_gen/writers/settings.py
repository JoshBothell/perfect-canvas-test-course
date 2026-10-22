"""``course_settings/*`` writers (mirrors lib/cc/canvas_resource.rb and friends)."""

from __future__ import annotations

import json
import uuid

from ..content import Course, Renderer, file_id
from ..ids import mid
from ..package import LOR, Package
from ..xmlutil import node, root, sub, subs, tostring
from .common import html_page

DIR = "course_settings"
CANVAS_EXPORT_FLAG = (
    "Q: What did the panda say when he was forced out of his natural habitat?\n"
    "A: This is un-BEAR-able\n"
)

# Canvas Course::TAB_* ids
TABS = {
    "home": 0, "syllabus": 1, "pages": 2, "assignments": 3, "quizzes": 4, "grades": 5, "people": 6,
    "groups": 7, "discussions": 8, "modules": 10, "files": 11, "conferences": 12, "settings": 13,
    "announcements": 14, "outcomes": 15, "collaborations": 16, "rubrics": 18,
}

COURSE_BOOLS = [
    "is_public", "is_public_to_auth_users", "public_syllabus", "public_syllabus_to_auth", "indexed",
    "allow_student_wiki_edits", "allow_student_forum_attachments", "allow_student_organized_groups",
    "lock_all_announcements", "hide_final_grade", "hide_distribution_graphs", "allow_student_discussion_topics",
    "allow_student_discussion_editing", "allow_student_discussion_reporting", "allow_student_anonymous_discussion_topics",
    "show_announcements_on_home_page", "usage_rights_required", "restrict_student_future_view",
    "restrict_student_past_view", "restrict_quantitative_data", "show_total_grade_as_points",
    "restrict_enrollments_to_course_dates", "homeroom_course", "allow_final_grade_override", "enable_course_paces",
    "conditional_release", "hide_sections_on_course_users_page", "syllabus_course_summary",
    "filter_speed_grader_by_student_group",
]
COURSE_STRINGS = ["default_wiki_editing_roles", "default_view", "license", "locale", "group_weighting_scheme",
                  "course_color", "time_zone", "default_due_time", "home_page_announcement_limit", "storage_quota"]


def write_course_settings(pkg: Package, course: Course, r: Renderer) -> str:
    s = course.settings
    course_mid = mid("course", s["course_code"])
    written: list[str] = []

    def put(name: str, el) -> None:
        written.append(pkg.add(f"{DIR}/{name}", tostring(el)))

    # course_settings.xml ------------------------------------------------------
    c = root("course", identifier=course_mid)
    sub(c, "title", s["title"])
    sub(c, "course_code", s["course_code"])
    sub(c, "start_at", course.dates.utc(s.get("start")), keep_empty=True)
    sub(c, "conclude_at", course.dates.utc(s.get("end")), keep_empty=True)
    tabs = []
    for t in s.get("tabs", []):
        name, hidden = (t, False) if isinstance(t, str) else (t["tab"], t.get("hidden", False))
        if name.startswith("lti:"):
            if not course.lti:
                continue
            tab = {"id": f"context_external_tool_{mid('lti_tool', name[4:])}"}
        else:
            tab = {"id": TABS[name]}
        if hidden:
            tab["hidden"] = True
        tabs.append(tab)
    if tabs:
        sub(c, "tab_configuration", json.dumps(tabs, separators=(",", ":")))
    if s.get("image"):
        course.files.setdefault(s["image"], {"path": s["image"]})
        sub(c, "image_identifier_ref", file_id(s["image"]))
    if s.get("banner_image"):
        course.files.setdefault(s["banner_image"], {"path": s["banner_image"]})
        sub(c, "banner_image_identifier_ref", file_id(s["banner_image"]))
    flags = s.get("flags", {})
    for k in COURSE_BOOLS:
        if k in flags:
            sub(c, k, bool(flags[k]))
    for k in COURSE_STRINGS:
        v = s.get(k, flags.get(k))
        if v is not None:
            sub(c, k, v)
    if s.get("default_grading_standard"):
        sub(c, "grading_standard_enabled", True)
        sub(c, "grading_standard_identifier_ref", course.grading_standards[s["default_grading_standard"]]["id"])
    pol = node(c, "default_post_policy")
    sub(pol, "post_manually", bool(s.get("post_manually", False)))
    put("course_settings.xml", c)

    # module_meta.xml -------------------------------------------------------------
    if course.modules:
        put("module_meta.xml", _module_meta(course))

    # assignment_groups.xml -----------------------------------------------------------
    groups = root("assignmentGroups")
    for pos, g in enumerate(course.assignment_groups.values(), start=1):
        ge = node(groups, "assignmentGroup", identifier=g["id"])
        sub(ge, "title", g["title"])
        sub(ge, "position", pos)
        sub(ge, "group_weight", float(g.get("weight", 0)))
        rules = g.get("rules") or {}
        if rules:
            rs = node(ge, "rules")
            for drop_type in ("drop_lowest", "drop_highest"):
                if rules.get(drop_type):
                    rule = node(rs, "rule")
                    sub(rule, "drop_type", drop_type)
                    sub(rule, "drop_count", rules[drop_type])
            for akey in rules.get("never_drop", []):
                if akey not in course.assignments:
                    raise ValueError(f"assignment group {g['key']}: never_drop references unknown assignment {akey}")
                rule = node(rs, "rule")
                sub(rule, "drop_type", "never_drop")
                sub(rule, "identifierref", course.assignments[akey]["id"])
    put("assignment_groups.xml", groups)

    # grading_standards.xml -----------------------------------------------------------
    if course.grading_standards:
        gss = root("gradingStandards")
        for gs in course.grading_standards.values():
            ge = node(gss, "gradingStandard", identifier=gs["id"], version="2")
            sub(ge, "title", gs["title"])
            sub(ge, "data", json.dumps([[n, float(v)] for n, v in gs["data"]], ensure_ascii=False))
            sub(ge, "points_based", bool(gs.get("points_based", False)))
            sub(ge, "scaling_factor", float(gs.get("scaling_factor", 1.0)))
        put("grading_standards.xml", gss)

    # rubrics.xml -----------------------------------------------------------------------
    if course.rubrics:
        put("rubrics.xml", _rubrics(course))

    # learning_outcomes.xml ---------------------------------------------------------------
    if course.outcomes:
        put("learning_outcomes.xml", _outcomes(course, r))

    # files_meta.xml (always referenced by Canvas exports) ------------------------------------
    put("files_meta.xml", _files_meta(course))

    # events.xml -------------------------------------------------------------------------------
    if course.events:
        put("events.xml", _events(course, r))

    # late_policy.xml --------------------------------------------------------------------------
    lp = s.get("late_policy")
    if lp:
        el = root("late_policy", identifier=mid("late_policy", s["course_code"]))
        subs(el, {
            "missing_submission_deduction_enabled": bool(lp.get("missing_deduction_enabled", False)),
            "missing_submission_deduction": float(lp.get("missing_deduction", 0)),
            "late_submission_deduction_enabled": bool(lp.get("late_deduction_enabled", False)),
            "late_submission_deduction": float(lp.get("late_deduction", 0)),
            "late_submission_interval": lp.get("late_interval", "day"),
            "late_submission_minimum_percent_enabled": bool(lp.get("minimum_percent_enabled", False)),
            "late_submission_minimum_percent": float(lp.get("minimum_percent", 0)),
        })
        put("late_policy.xml", el)

    # media_tracks.xml: Canvas only links tracks to media *objects* (Kaltura/Notorious), which a
    # cartridge cannot create. Captions ship as .vtt files and are referenced via <track> in pages.

    # syllabus.html ----------------------------------------------------------------------------
    if s.get("syllabus"):
        body = r.html(s, "syllabus", "syllabus")
        syl = pkg.add(f"{DIR}/syllabus.html", html_page("Syllabus", body))
        pkg.resource(course_mid + "_syllabus", LOR, href=syl, files=[syl], intendeduse="syllabus")

    flag = pkg.add(f"{DIR}/canvas_export.txt", CANVAS_EXPORT_FLAG)
    pkg.resource(course_mid, LOR, href=flag, files=[*written, flag])
    return course_mid


def _module_meta(course: Course):
    mods = root("modules")
    for m in course.modules:
        me = node(mods, "module", identifier=m["id"])
        sub(me, "title", m["title"])
        sub(me, "workflow_state", "active" if m.get("published", True) else "unpublished")
        sub(me, "position", m["position"])
        if m.get("unlock_at"):
            sub(me, "unlock_at", course.dates.utc(m["unlock_at"]))
        sub(me, "require_sequential_progress", bool(m.get("require_sequential_progress", False)))
        if m.get("requirement_count"):
            sub(me, "requirement_count", m["requirement_count"])
        sub(me, "locked", False)
        if m.get("prerequisites"):
            pre = node(me, "prerequisites")
            by_key = {x["key"]: x for x in course.modules}
            for pk in m["prerequisites"]:
                p = by_key[pk]
                if p["position"] >= m["position"]:
                    raise ValueError(f"module {m['key']}: prerequisite {pk} must come earlier")
                pe = node(pre, "prerequisite", type="context_module")
                sub(pe, "title", p["title"])
                sub(pe, "identifierref", p["id"])
        items = node(me, "items")
        reqs = []
        for pos, it in enumerate(m["items"], start=1):
            ie = node(items, "item", identifier=it["id"])
            sub(ie, "content_type", it.get("content_type", "ContextModuleSubHeader"))
            sub(ie, "workflow_state", "active" if it["published"] else "unpublished")
            sub(ie, "title", it["title"])
            if it["type"] != "subheader":
                sub(ie, "identifierref", it["ref_id"])
            if it["type"] in ("url", "external_tool"):
                sub(ie, "url", it["url"])
            sub(ie, "position", pos)
            sub(ie, "new_tab", bool(it["new_tab"]))
            sub(ie, "indent", it["indent"])
            sub(ie, "link_settings_json", "null")
            if it.get("requirement"):
                reqs.append(it)
        if reqs:
            crs = node(me, "completionRequirements")
            for it in reqs:
                ce = node(crs, "completionRequirement", type=it["requirement"])
                if it["requirement"] == "min_score":
                    sub(ce, "min_score", float(it["min_score"]))
                elif it["requirement"] == "min_percentage":
                    sub(ce, "min_percentage", int(it["min_percentage"]))
                sub(ce, "identifierref", it["id"])
    return mods


def _rubrics(course: Course):
    rs = root("rubrics")
    for r in course.rubrics.values():
        re_ = node(rs, "rubric", identifier=r["id"])
        sub(re_, "read_only", False)
        sub(re_, "title", r["title"])
        sub(re_, "reusable", False)
        sub(re_, "public", False)
        total = sum(float(c["points"]) for c in r["criteria"] if not c.get("ignore_for_scoring"))
        sub(re_, "points_possible", total)
        sub(re_, "hide_score_total", bool(r.get("hide_score_total", False)))
        sub(re_, "free_form_criterion_comments", bool(r.get("free_form_criterion_comments", False)))
        if r.get("description"):
            sub(re_, "description", r["description"])
        crit = node(re_, "criteria")
        for ci, c in enumerate(r["criteria"], start=1):
            cid = f"_{abs(hash_int(r['key'], ci)) % 9000 + 1000}"
            ce = node(crit, "criterion")
            sub(ce, "criterion_id", cid)
            sub(ce, "points", float(c["points"]))
            if c.get("mastery_points") is not None:
                sub(ce, "mastery_points", float(c["mastery_points"]))
            if c.get("ignore_for_scoring") is not None:
                sub(ce, "ignore_for_scoring", bool(c["ignore_for_scoring"]))
            sub(ce, "description", c["description"])
            if c.get("long_description"):
                sub(ce, "long_description", c["long_description"])
            if c.get("use_range"):
                sub(ce, "criterion_use_range", True)
            if c.get("outcome"):
                sub(ce, "learning_outcome_identifierref", course.outcomes[c["outcome"]]["id"])
            ratings = node(ce, "ratings")
            for ri, rt in enumerate(c["ratings"], start=1):
                rte = node(ratings, "rating")
                sub(rte, "description", rt["description"])
                sub(rte, "points", float(rt["points"]))
                sub(rte, "criterion_id", cid)
                if rt.get("long_description"):
                    sub(rte, "long_description", rt["long_description"])
                sub(rte, "id", f"_{abs(hash_int(r['key'], ci, ri)) % 9000 + 1000}")
    return rs


def _outcomes(course: Course, r: Renderer):
    spec = course.settings["outcomes"]
    root_el = root("learningOutcomes")
    grp = node(root_el, "learningOutcomeGroup", identifier=mid("outcome_group", spec["group"]["key"]))
    sub(grp, "title", spec["group"]["title"])
    if spec["group"].get("description"):
        sub(grp, "description", spec["group"]["description"])
    los = node(grp, "learningOutcomes")
    for o in course.outcomes.values():
        oe = node(los, "learningOutcome", identifier=o["id"])
        sub(oe, "title", o["title"])
        if o.get("description"):
            sub(oe, "description", r.render(o["description"], f"outcome:{o['key']}"))
        sub(oe, "calculation_method", o["calculation_method"])
        if o.get("calculation_int") is not None:
            sub(oe, "calculation_int", o["calculation_int"])
        if o.get("vendor_guid"):
            sub(oe, "vendor_guid", o["vendor_guid"])
        sub(oe, "points_possible", float(o["points_possible"]))
        sub(oe, "mastery_points", float(o["mastery_points"]))
        ratings = node(oe, "ratings")
        for rt in o["ratings"]:
            rte = node(ratings, "rating")
            sub(rte, "description", rt["description"])
            sub(rte, "points", float(rt["points"]))
    return root_el


def _files_meta(course: Course):
    fm = root("fileMeta")
    folders = course.settings.get("folders") or {}
    if folders:
        fe = node(fm, "folders")
        for path, meta in folders.items():
            f = node(fe, "folder", path=path)
            if meta.get("locked"):
                sub(f, "locked", True)
            if meta.get("hidden"):
                sub(f, "hidden", True)
            if meta.get("lock_at"):
                sub(f, "lock_at", course.dates.utc(meta["lock_at"]))
            if meta.get("unlock_at"):
                sub(f, "unlock_at", course.dates.utc(meta["unlock_at"]))
    special = [f for f in course.files.values() if any(f.get(k) for k in ("locked", "hidden", "lock_at", "unlock_at", "display_name", "usage_rights"))]
    if special:
        files_el = node(fm, "files")
        for f in special:
            e = node(files_el, "file", identifier=file_id(f["path"]))
            if f.get("locked"):
                sub(e, "locked", True)
            if f.get("hidden"):
                sub(e, "hidden", True)
            if f.get("lock_at"):
                sub(e, "lock_at", course.dates.utc(f["lock_at"]))
            if f.get("unlock_at"):
                sub(e, "unlock_at", course.dates.utc(f["unlock_at"]))
            if f.get("display_name"):
                sub(e, "display_name", f["display_name"])
            sub(e, "category", "uncategorized")
            ur = f.get("usage_rights")
            if ur:
                u = node(e, "usage_rights", use_justification=ur["use_justification"])
                sub(u, "legal_copyright", ur.get("legal_copyright"))
                sub(u, "license", ur.get("license"))
    return fm


def _events(course: Course, r: Renderer):
    d = course.dates
    evs = root("events")
    for ev in course.events:
        occurrences = ev.get("repeat_weekly", 1)
        series = str(uuid.UUID(mid("event_series", ev["key"])[1:])) if occurrences > 1 else None
        for n in range(occurrences):
            start = _shift_weeks(ev.get("start"), n)
            end = _shift_weeks(ev.get("end"), n)
            ee = node(evs, "event", identifier=mid("event", f"{ev['key']}/{n}"))
            sub(ee, "title", ev["title"])
            sub(ee, "description", r.render(ev.get("description", ""), f"event:{ev['key']}"), keep_empty=True)
            if ev.get("all_day"):
                sub(ee, "start_at", d.utc(_at(start, "00:00")))
                sub(ee, "end_at", d.utc(_at(start, "00:00")))
                sub(ee, "all_day", True)
                sub(ee, "all_day_date", d.day(start))
            else:
                sub(ee, "start_at", d.utc(start))
                sub(ee, "end_at", d.utc(end))
            if series:
                sub(ee, "rrule", f"FREQ=WEEKLY;INTERVAL=1;COUNT={occurrences}")
                sub(ee, "series_uuid", series)
                if n == 0:
                    sub(ee, "series_head", True)
    return evs


def _shift_weeks(spec: str | None, n: int) -> str | None:
    """'+3d@15:00' shifted by n weeks -> '+{3+7n}d@15:00'."""
    if not spec or n == 0:
        return spec
    import re

    m = re.match(r"^([+-])(\d+)d(@.*)?$", spec)
    if not m:
        raise ValueError(f"repeat_weekly needs a relative day spec, got {spec!r}")
    days = int(m[2]) * (1 if m[1] == "+" else -1) + 7 * n
    return f"{'+' if days >= 0 else '-'}{abs(days)}d{m[3] or ''}"


def _at(spec: str, hhmm: str) -> str:
    return spec.split("@")[0] + "@" + hhmm


def hash_int(*parts) -> int:
    import hashlib

    return int(hashlib.md5(":".join(map(str, parts)).encode()).hexdigest()[:8], 16)
