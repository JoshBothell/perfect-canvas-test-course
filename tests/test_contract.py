"""The test-matrix contract: every theme builds a valid, deterministic cartridge that exercises the
same Canvas features. Items are found by *structure* (never by theme keys), so a new theme gets
these checks for free just by existing in themes/."""

from __future__ import annotations

import re
import urllib.parse
from collections import Counter

import pytest
import yaml
from lxml import etree

from cc_gen.build import build
from cc_gen.content import load_course
from cc_gen.matrix import compare
from cc_gen.theme import load_theme
from cc_gen.validate import validate
from cc_gen.writers.qti import TYPE_MAP

from conftest import ANCHOR, REFERENCE_THEME, ROOT, SEED, THEMES

CANVAS = "{http://canvas.instructure.com/xsd/cccv1p0}"
QTI = "{http://www.imsglobal.org/xsd/ims_qtiasiv1p2}"


def _xml(pkg, path):
    return etree.fromstring(pkg.files[path])


# ---------------------------------------------------------------- discovery
def identify_bank(c):
    """The bank with one generated identify question per catalog entry."""
    return next(b for b in c.banks.values()
                if len(b["questions"]) == len(c.catalog) and all(q.get("generated") == "identify" for q in b["questions"]))


def practice_quiz(c):
    return next(q for q in c.quizzes.values() if q.get("quiz_type") == "practice_quiz"
                and any(x.get("generated") == "identify" for x in q["questions"]))


def bank_users(c, pkg, bank):
    ref = f"<sourcebank_ref>{bank['id']}</sourcebank_ref>".encode()
    return [q["key"] for q in c.quizzes.values() if ref in pkg.files[f"non_cc_assessments/{q['id']}.xml.qti"]]


# ------------------------------------------------------------------- package
def test_validates(built):
    rep = validate(built[0])
    assert rep.ok, rep.errors
    assert not rep.warnings, rep.warnings


def test_lti_variant_validates(built_lti):
    out, pkg, course = built_lti
    rep = validate(out)
    assert rep.ok, rep.errors
    assert any(r.type == "imsbasiclti_xmlv1p0" for r in pkg.resources)
    assert any(m.get("requires_lti") for m in course.modules)
    tabs = _xml(pkg, "course_settings/course_settings.xml").findtext(f"{CANVAS}tab_configuration")
    assert "context_external_tool_" in tabs


def test_no_lti_without_config(built):
    _, pkg, course = built
    assert not any(r.type == "imsbasiclti_xmlv1p0" for r in pkg.resources)
    assert "context_external_tool_" not in pkg.files["course_settings/course_settings.xml"].decode()


def test_deterministic(built, theme, tmp_path):
    pkg2, _ = build(theme, anchor=ANCHOR, images_mode="generated", seed=SEED, log=lambda *_: None)
    out2 = tmp_path / "again.imscc"
    pkg2.write_zip(out2)
    assert built[0].read_bytes() == out2.read_bytes(), "rebuilding with the same anchor + seed must be byte-identical"


def test_canvas_flavor_marker(built):
    _, pkg, _ = built
    assert "course_settings/canvas_export.txt" in pkg.files
    assert _xml(pkg, "imsmanifest.xml").findtext(".//{*}schemaversion") == "1.1.0"


def test_titles_unique_per_type(built):
    """The seeder maps content -> Canvas ids by title, so titles must be unique per object type."""
    _, _, c = built
    for name, reg in [("assignments", c.assignments), ("quizzes", c.quizzes), ("pages", c.pages),
                      ("discussions", c.discussions), ("modules", {m["key"]: m for m in c.modules})]:
        dupes = [t for t, n in Counter(o["title"] for o in reg.values()).items() if n > 1]
        assert not dupes, f"duplicate {name} titles: {dupes}"
    # assignments, graded quizzes and graded discussions share the Assignments index
    all_asg = [a["title"] for a in c.assignments.values()] + [q["title"] for q in c.quizzes.values()] + \
              [d["title"] for d in c.discussions.values() if d.get("graded")]
    assert len(all_asg) == len(set(all_asg))


@pytest.mark.skipif(len(THEMES) < 2, reason="only one theme")
def test_same_matrix_as_reference_theme(theme):
    """THE contract: identical theme-independent signature (tests: wording may differ)."""
    ref = load_theme(REFERENCE_THEME)
    load = lambda t: load_course(t, anchor=ANCHOR, lti_path=t.lti_example, seed=SEED)
    diff = compare(load(ref), load(theme), ignore_tests=True)
    assert not diff, "\n".join(diff)


def test_commercial_theme_has_no_other_theme_terms(built, theme):
    if not theme.config.get("commercial"):
        pytest.skip("non-commercial theme")
    others = [load_theme(t) for t in THEMES if t != theme.name]
    rep = validate(built[0], forbid_themes=others, allow_terms=theme.config.get("allow_terms") or ())
    assert rep.ok, rep.errors


def test_no_client_strings(built):
    ref = ROOT / "examples" / "OTC_PROPRIETARY_EXPORT.imscc"
    if not ref.exists():
        pytest.skip("client reference export not present")
    rep = validate(built[0], hygiene_against=ref)
    assert rep.ok, [e for e in rep.errors if e.startswith("hygiene")]


# ------------------------------------------------------------------ coverage
def _assignment_docs(pkg):
    docs = [_xml(pkg, p) for p in pkg.files if p.endswith("assignment_settings.xml")]
    for p in pkg.files:
        if p.endswith("assessment_meta.xml") or (p.endswith(".xml") and b"<topicMeta" in pkg.files[p][:400]):
            docs += list(_xml(pkg, p).iter(f"{CANVAS}assignment"))
    return docs


def test_every_submission_type(built_lti):
    _, pkg, _ = built_lti
    seen = set()
    for a in _assignment_docs(pkg):
        seen.update(a.findtext(f"{CANVAS}submission_types").split(","))
    expected = {"online_text_entry", "online_url", "online_upload", "media_recording", "student_annotation",
                "external_tool", "discussion_topic", "online_quiz", "on_paper", "none"}
    assert expected <= seen, expected - seen


def test_every_grading_type(built):
    _, pkg, _ = built
    seen = {a.findtext(f"{CANVAS}grading_type") for a in _assignment_docs(pkg)}
    assert {"points", "percent", "letter_grade", "gpa_scale", "pass_fail", "not_graded"} <= seen


def test_grading_workflows(built):
    _, pkg, _ = built
    flags = Counter()
    for a in _assignment_docs(pkg):
        for f in ("moderated_grading", "anonymous_grading", "peer_reviews", "automatic_peer_reviews", "anonymous_peer_reviews",
                  "omit_from_final_grade", "only_visible_to_overrides", "has_group_category", "grade_group_students_individually"):
            if a.findtext(f"{CANVAS}{f}") == "true":
                flags[f] += 1
        if a.findtext(f"{CANVAS}post_policy/{CANVAS}post_manually") == "true":
            flags["post_manually"] += 1
        if a.findtext(f"{CANVAS}allowed_attempts") not in (None, ""):
            flags["allowed_attempts"] += 1
        if a.findtext(f"{CANVAS}rubric_identifierref"):
            flags["rubric"] += 1
    for f in ("moderated_grading", "anonymous_grading", "peer_reviews", "automatic_peer_reviews", "anonymous_peer_reviews",
              "omit_from_final_grade", "only_visible_to_overrides", "has_group_category", "grade_group_students_individually",
              "post_manually", "allowed_attempts", "rubric"):
        assert flags[f] >= 1, f"no assignment exercises {f}"


def test_every_question_type(built):
    _, pkg, _ = built
    seen = set()
    for p, data in pkg.files.items():
        if p.endswith(".xml.qti"):
            for f in etree.fromstring(data).iter(f"{QTI}qtimetadatafield"):
                if f.findtext(f"{QTI}fieldlabel") == "question_type":
                    seen.add(f.findtext(f"{QTI}fieldentry"))
    assert set(TYPE_MAP.values()) <= seen, set(TYPE_MAP.values()) - seen


def test_quiz_types_and_banks(built):
    _, pkg, c = built
    assert {"assignment", "practice_quiz", "graded_survey", "survey"} <= {q.get("quiz_type", "assignment") for q in c.quizzes.values()}
    assert c.banks, "at least one question bank"
    refs = b"".join(d for p, d in pkg.files.items() if p.endswith(".xml.qti"))
    assert b"<sourcebank_ref>" in refs and b"<selection_number>" in refs


def test_quiz_settings_coverage(built):
    """Every Classic quiz setting worth testing is exercised by at least one quiz."""
    _, pkg, c = built
    metas = [_xml(pkg, p) for p in pkg.files if p.endswith("assessment_meta.xml")]
    val = lambda tag: {m.findtext(f"{CANVAS}{tag}") for m in metas}
    assert {"keep_highest", "keep_latest", "keep_average"} <= val("scoring_policy")
    assert {"always", "until_after_last_attempt"} <= val("hide_results")
    assert "true" in val("one_time_results") and "false" in val("available")  # unpublished quiz
    assert "true" in val("one_question_at_a_time") and "true" in val("cant_go_back")
    assert {"-1", "1", "2", "3"} <= val("allowed_attempts")


def test_every_completion_requirement(built):
    _, pkg, _ = built
    mm = _xml(pkg, "course_settings/module_meta.xml")
    seen = {cr.get("type") for cr in mm.iter(f"{CANVAS}completionRequirement")}
    assert {"must_view", "must_submit", "must_contribute", "must_mark_done", "min_score", "min_percentage"} <= seen
    assert mm.find(f".//{CANVAS}prerequisite") is not None
    assert any(m.findtext(f"{CANVAS}require_sequential_progress") == "true" for m in mm.iter(f"{CANVAS}module"))
    assert any(m.findtext(f"{CANVAS}unlock_at") for m in mm.iter(f"{CANVAS}module"))
    assert any(m.findtext(f"{CANVAS}workflow_state") == "unpublished" for m in mm.iter(f"{CANVAS}module"))
    assert any(m.find(f"{CANVAS}items") is not None and len(m.find(f"{CANVAS}items")) == 0 for m in mm.iter(f"{CANVAS}module"))
    kinds = {i.findtext(f"{CANVAS}content_type") for i in mm.iter(f"{CANVAS}item")}
    assert {"WikiPage", "Assignment", "Quizzes::Quiz", "DiscussionTopic", "Attachment", "ExternalUrl", "ContextModuleSubHeader"} <= kinds


def test_date_edge_cases(built):
    _, _, c = built
    d = c.dates
    dues = [d.local(a.get("due")) for a in c.assignments.values() if a.get("due")]
    assert any(x < d.local("+0d@12:00") for x in dues), "past-due item"
    assert any(x.date() == ANCHOR for x in dues), "due-today item"
    assert any(x.date() == d.next_dst_transition() for x in dues), "DST-day item"
    assert any(a.get("all_day") for a in c.assignments.values())
    assert any(not a.get("due") for a in c.assignments.values())


def test_content_edge_cases(built):
    _, pkg, c = built
    titles = [p["title"] for p in c.pages.values()]
    assert any(len(t) == 255 for t in titles), "255-char title"
    assert any("<" in t and "&" in t and '"' in t for t in titles), "escaping title"
    assert any(any(ord(ch) > 0x3000 for ch in t) for t in titles), "CJK title"
    assert len(c.catalog) >= 386, "identification bank coverage needs >= 386 catalog entries"
    assert {e["group"] for e in c.catalog} >= {1, 2, 3}, "catalog groups for units 1-3"
    rows = max(data.count(b"<tr>") for p, data in pkg.files.items() if p.startswith("wiki_content/"))
    assert rows >= len(c.catalog) + 1, "one page is a table with a row per catalog entry"


def test_files_meta(built):
    _, pkg, _ = built
    fm = _xml(pkg, "course_settings/files_meta.xml")
    assert fm.find(f".//{CANVAS}folder/{CANVAS}hidden") is not None
    assert any(f.findtext(f"{CANVAS}display_name") for f in fm.iter(f"{CANVAS}file"))
    assert any(re.search(r"[^\x00-\x7f]", p) and " " in p for p in pkg.files if p.startswith("web_resources/")), \
        "unicode filename with spaces"
    for ext in (".pdf", ".docx", ".csv", ".json", ".py", ".png", ".gif", ".webp", ".svg", ".mp4", ".mp3", ".vtt"):
        assert any(p.startswith("web_resources/") and p.endswith(ext) for p in pkg.files), ext


def test_build_uses_palette_pngs(built):
    import io

    from PIL import Image

    _, pkg, c = built
    for path in (c.catalog[0]["image"], next(iter(c.masked))):
        assert Image.open(io.BytesIO(pkg.files["web_resources/" + path])).mode == "P", path


# ------------------------------------------------------- identify questions
def test_practice_quiz_randomized_per_build_reproducible_by_seed(theme):
    picks = lambda seed: [q["feedback"]["correct"] for q in practice_quiz(load_course(theme, anchor=ANCHOR, seed=seed))["questions"][:3]]
    assert picks(1) == picks(1), "same seed, same questions"
    assert len({tuple(picks(s)) for s in range(10)}) > 1, "different seeds pick different questions"
    quiz = practice_quiz(load_course(theme, anchor=ANCHOR, seed=1))
    assert [q["type"] for q in quiz["questions"]] == ["multiple_choice"] * 3 + ["group"]


def test_practice_quiz_redraws_from_identify_bank(built):
    _, pkg, c = built
    quiz, bank = practice_quiz(c), identify_bank(c)
    group = quiz["questions"][-1]
    assert group["bank"] == bank["key"] and group["pick"] == 3
    assert len(c.masked) >= len(c.catalog), "every catalog entry has a masked image"
    qti = pkg.files[f"non_cc_assessments/{quiz['id']}.xml.qti"].decode()
    assert f"<sourcebank_ref>{bank['id']}</sourcebank_ref>" in qti and "<selection_number>3</selection_number>" in qti


def test_identify_bank_is_everywhere(built):
    _, pkg, c = built
    users = bank_users(c, pkg, identify_bank(c))
    assert len(users) >= 7, users
    pure = [k for k in users if all(x["type"] == "group" for x in c.quizzes[k]["questions"])]
    assert len(pure) >= 3, pure  # whole quizzes built only from bank draws
    assert any(x.get("pick") == 50 for k in pure for x in c.quizzes[k]["questions"]), "50-draw marathon"


def test_every_masked_question_reveals_its_image(built):
    """Each question showing a masked image has HTML general feedback with the SAME entry's unmasked image."""
    _, pkg, c = built
    image_by_id = {e["id"]: e["image"] for e in c.catalog}
    masked_dirs = {p.rsplit("/", 1)[0] for p in c.masked}
    pattern = "|".join(re.escape(d) for d in masked_dirs) + r"/[0-9a-f]+\.png"
    checked = 0
    for path, data in pkg.files.items():
        if not path.endswith(".xml.qti"):
            continue
        for item in etree.fromstring(data).iter(f"{QTI}item"):
            question = item.findtext(f"{QTI}presentation/{QTI}material/{QTI}mattext") or ""
            shown = re.findall(pattern, question)
            if not shown:
                continue
            fb = item.find(f"{QTI}itemfeedback[@ident='general_fb']")
            assert fb is not None, item.get("title")
            mat = fb.find(f".//{QTI}mattext")
            assert mat.get("texttype") == "text/html"
            expected = image_by_id[c.masked[shown[0]]]
            assert "/".join(urllib.parse.quote(p) for p in expected.split("/")) in mat.text, (item.get("title"), expected)
            assert any(rc.find(f".//{QTI}displayfeedback[@linkrefid='general_fb']") is not None
                       for rc in item.iter(f"{QTI}respcondition")), "feedback must be wired to display"
            checked += 1
    assert checked >= len(c.catalog) + 3, checked  # full bank + practice quiz picks


# ------------------------------------------------------------------- roster
def test_roster_references_resolve(built, theme):
    _, _, c = built
    roster = yaml.safe_load(theme.roster_path.read_text())
    users = {u["key"] for u in roster["users"]}
    sections = {s["key"] for s in roster["sections"]}
    groups = {g for gs in roster["group_sets"] for g in (gs.get("groups") or {})}
    for u in roster["users"]:
        assert set(u["sections"]) <= sections
        if u.get("observes"):
            assert u["observes"] in users
    for gs in roster["group_sets"]:
        for members in (gs.get("groups") or {}).values():
            assert set(members) <= users
    imported_sets = {a.get("group_category") for a in c.assignments.values()} | {d.get("group_category") for d in c.discussions.values()}
    filled = {gs["name"] for gs in roster["group_sets"] if gs.get("groups")}
    assert filled and filled <= imported_sets, "roster fills the group set the cartridge creates"
    for o in roster["overrides"]:
        assert o["assignment"] in c.assignments
        assert o.get("section") in (None, *sections)
        assert o.get("group") in (None, *groups)
    for s in roster["submissions"] + roster["grades"]:
        assert s["assignment"] in c.assignments, s
        assert s["user"] in users
    for s in roster["submissions"]:
        assert s["type"] in c.assignments[s["assignment"]]["submission_types"], s
        if s.get("file"):
            assert s["file"] in c.files
    rubric_crit = {cr["description"] for r in c.rubrics.values() for cr in r["criteria"]}
    for g in roster["grades"]:
        for desc in (g.get("rubric") or {}):
            assert desc in rubric_crit
    assert set(roster["progress"]["complete_orientation"]) <= users
    seeder = theme.seeder
    assert seeder.get("orientation_module") in {m["key"] for m in c.modules}
    assert seeder.get("intro_discussion") in c.discussions
