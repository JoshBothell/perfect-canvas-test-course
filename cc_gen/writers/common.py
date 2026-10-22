"""Shared XML fragments (the Canvas ``<assignment>`` element is embedded in several files)."""

from __future__ import annotations

from ..content import Course, file_id
from ..ids import mid
from ..xmlutil import node, sub

GRADING_TYPES = {"points", "percent", "letter_grade", "gpa_scale", "pass_fail", "not_graded"}
SUBMISSION_TYPES = {
    "online_text_entry", "online_url", "online_upload", "media_recording", "student_annotation",
    "external_tool", "discussion_topic", "online_quiz", "wiki_page", "on_paper", "none",
}


def html_page(title: str, body: str, meta: dict | None = None) -> str:
    """Byte-compatible with CCHelper::HtmlContentExporter#html_page."""
    from markupsafe import escape

    meta_html = ""
    for k, v in (meta or {}).items():
        if v is None or v == "" or v is False:  # Ruby's `next unless v.present?`
            continue
        val = "true" if v is True else str(v)
        meta_html += f'<meta name="{escape(k)}" content="{escape(val)}"/>\n'
    return (
        '<html>\n<head>\n<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>\n'
        f"<title>{escape(title)}</title>\n{meta_html}</head>\n<body>\n{body}\n</body>\n</html>"
    )


def assignment_fields(el, a: dict, course: Course, *, submission_types: list[str], position: int | None = None) -> None:
    """Populate an ``<assignment>`` element (assignment_settings.xml, or embedded in quiz/topic meta).

    Mirrors AssignmentResources.create_canvas_assignment. ``a`` is the author-facing dict.
    """
    d = course.dates
    grading_type = a.get("grading_type", "points")
    if grading_type not in GRADING_TYPES:
        raise ValueError(f"{a.get('key')}: bad grading_type {grading_type}")
    for st in submission_types:
        if st not in SUBMISSION_TYPES:
            raise ValueError(f"{a.get('key')}: bad submission type {st}")

    sub(el, "title", a["title"])
    sub(el, "due_at", d.utc(a.get("due")), keep_empty=True)
    sub(el, "lock_at", d.utc(a.get("lock")), keep_empty=True)
    sub(el, "unlock_at", d.utc(a.get("unlock")), keep_empty=True)
    if a.get("all_day") and a.get("due"):
        sub(el, "all_day_date", d.day(a["due"]))
    if a.get("peer_reviews_due"):
        sub(el, "peer_reviews_due_at", d.utc(a["peer_reviews_due"]))
    group = a.get("group")
    if group:
        if group not in course.assignment_groups:
            raise ValueError(f"{a.get('key')}: unknown assignment group {group}")
        sub(el, "assignment_group_identifierref", course.assignment_groups[group]["id"])
    gs = a.get("grading_standard")
    if gs:
        sub(el, "grading_standard_identifierref", course.grading_standards[gs]["id"])
    sub(el, "workflow_state", "published" if a.get("published", True) else "unpublished")

    rubric = a.get("rubric")
    if rubric:
        r = course.rubrics[rubric]
        sub(el, "rubric_identifierref", r["id"])
        sub(el, "rubric_use_for_grading", bool(a.get("rubric_use_for_grading", True)))
        sub(el, "rubric_hide_points", bool(a.get("rubric_hide_points", False)))
        sub(el, "rubric_hide_outcome_results", False)
        sub(el, "rubric_hide_score_total", bool(a.get("rubric_hide_score_total", r.get("hide_score_total", False))))

    node(el, "assignment_overrides")  # section/student/group overrides are created by the seeder
    if a.get("quiz_identifierref"):
        sub(el, "quiz_identifierref", a["quiz_identifierref"])
    sub(el, "allowed_extensions", ",".join(a.get("allowed_extensions", [])), keep_empty=True)
    has_group = bool(a.get("group_category"))
    sub(el, "has_group_category", has_group)
    if has_group:
        sub(el, "group_category", a["group_category"])

    points = a.get("points", 0 if grading_type == "not_graded" else None)
    flags = {
        "points_possible": float(points) if points is not None else None,
        "grading_type": grading_type,
        "all_day": bool(a.get("all_day", False)),
        "submission_types": ",".join(submission_types),
        "position": position,
        "turnitin_enabled": False,
        "vericite_enabled": False,
        "peer_review_count": a.get("peer_review_count", 0),
        "peer_reviews": bool(a.get("peer_reviews", False)),
        "automatic_peer_reviews": bool(a.get("automatic_peer_reviews", False)),
        "anonymous_peer_reviews": bool(a.get("anonymous_peer_reviews", False)),
        "grade_group_students_individually": bool(a.get("grade_group_students_individually", False)),
        "freeze_on_copy": False,
        "omit_from_final_grade": bool(a.get("omit_from_final_grade", False)),
        "hide_in_gradebook": bool(a.get("hide_in_gradebook", False)),
        "intra_group_peer_reviews": bool(a.get("intra_group_peer_reviews", False)),
        "only_visible_to_overrides": bool(a.get("only_visible_to_overrides", False)),
        "post_to_sis": bool(a.get("post_to_sis", False)),
        "moderated_grading": bool(a.get("moderated_grading", False)),
        "grader_count": a.get("grader_count", 0),
        "grader_comments_visible_to_graders": bool(a.get("grader_comments_visible_to_graders", True)),
        "anonymous_grading": bool(a.get("anonymous_grading", False)),
        "graders_anonymous_to_graders": bool(a.get("graders_anonymous_to_graders", False)),
        "grader_names_visible_to_final_grader": bool(a.get("grader_names_visible_to_final_grader", True)),
        "anonymous_instructor_annotations": bool(a.get("anonymous_instructor_annotations", False)),
        "allowed_attempts": a.get("allowed_attempts"),
    }
    for k, v in flags.items():
        sub(el, k, v)

    if "external_tool" in submission_types:
        tool_key = a.get("external_tool")
        if tool_key:
            sub(el, "external_tool_identifierref", mid("lti_tool", tool_key))
        url = a.get("external_tool_url") or (course.lti or {}).get("tools", {}).get(tool_key, {}).get("launch_url")
        if not url:
            raise ValueError(f"{a.get('key')}: external_tool assignment needs external_tool_url or a known external_tool")
        sub(el, "external_tool_url", url)
        sub(el, "external_tool_new_tab", bool(a.get("external_tool_new_tab", False)))
        if a.get("line_item"):
            li = node(el, "line_items")
            item = node(li, "line_item")
            sub(item, "coupled", bool(a["line_item"].get("coupled", True)))
            sub(item, "tag", a["line_item"].get("tag"))
            sub(item, "resource_id", a["line_item"].get("resource_id"))

    pol = node(el, "post_policy")
    sub(pol, "post_manually", bool(a.get("post_manually", False)))

    if a.get("annotatable_file"):
        course.files.setdefault(a["annotatable_file"], {"path": a["annotatable_file"]})
        sub(el, "annotatable_attachment_migration_id", file_id(a["annotatable_file"]))
