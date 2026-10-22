"""Classic Quizzes as Canvas QTI 1.2 (mirrors lib/cc/qti/qti_generator.rb + qti_items.rb).

Per quiz:
  <id>/assessment_qti.xml               CC-profile copy (only the 5 CC-supported types)
  non_cc_assessments/<id>.xml.qti       full-fidelity Canvas copy (what the importer prefers)
  <id>/assessment_meta.xml              quiz settings + embedded <assignment>
Question banks: non_cc_assessments/<bank_id>.xml.qti (objectbank).

Authoring format (YAML) per question type is documented in README.md ("Question types").
"""

from __future__ import annotations

import ast
import hashlib
import math
import operator
import random

from lxml import etree

from ..content import Course, Renderer
from ..ids import mid
from ..package import ASSESSMENT, LOR, Package
from ..xmlutil import node, root, sub, tostring
from .common import assignment_fields

QTI_NS = "http://www.imsglobal.org/xsd/ims_qtiasiv1p2"
CC_QTI_XSD = "http://www.imsglobal.org/profile/cc/ccv1p1/ccv1p1_qtiasiv1p2p1_v1p0.xsd"
QTI_XSD = "http://www.imsglobal.org/xsd/ims_qtiasiv1p2p1.xsd"

TYPE_MAP = {
    "multiple_choice": "multiple_choice_question",
    "true_false": "true_false_question",
    "multiple_answers": "multiple_answers_question",
    "short_answer": "short_answer_question",
    "fill_in_multiple_blanks": "fill_in_multiple_blanks_question",
    "multiple_dropdowns": "multiple_dropdowns_question",
    "matching": "matching_question",
    "numerical": "numerical_question",
    "calculated": "calculated_question",
    "essay": "essay_question",
    "file_upload": "file_upload_question",
    "text_only": "text_only_question",
}
CC_PROFILES = {
    "multiple_choice_question": "cc.multiple_choice.v0p1",
    "multiple_answers_question": "cc.multiple_response.v0p1",
    "true_false_question": "cc.true_false.v0p1",
    "short_answer_question": "cc.fib.v0p1",
    "essay_question": "cc.essay.v0p1",
}
MULTI_ANSWER_TYPES = {"matching_question", "multiple_dropdowns_question", "fill_in_multiple_blanks_question"}


def _num_id(*parts) -> int:
    return int(hashlib.md5(":".join(map(str, parts)).encode()).hexdigest()[:8], 16) % 90000 + 10000


# ------------------------------------------------------------- normalization
def normalize_question(q: dict, qkey: str, r: Renderer, where: str) -> dict:
    """Turn author YAML into Canvas's internal question hash shape (question_data)."""
    t = TYPE_MAP[q["type"]]
    fb = q.get("feedback", {})
    render = lambda v: r.render(str(v), where) if v else None
    r.masked_used = []
    text = r.render(q.get("text", ""), where)
    shown = list(r.masked_used)
    out = {
        "key": qkey,
        "question_type": t,
        "name": q.get("name") or q["type"].replace("_", " ").title(),
        "points_possible": float(q.get("points", 0 if t == "text_only_question" else 1)),
        "question_text": text,
        "answers": [],
        "correct_comments": render(fb.get("correct")),
        "incorrect_comments": render(fb.get("incorrect")),
        # identify questions: every question showing a masked image reveals the unmasked image as
        # general feedback (shown after answering, right or wrong), unless one is given explicitly
        "neutral_comments": render(fb.get("neutral")) or (
            "".join(f"<p>{r.entry_image(m, r.course.theme.images.get('feedback_width', 215))}</p>" for m in shown) if shown else None),
    }
    used: set[int] = set()

    def aid(*p):
        n = _num_id(qkey, *p)
        while n in used:
            n += 1
        used.add(n)
        return n

    if t in ("multiple_choice_question", "multiple_answers_question"):
        for i, a in enumerate(q["answers"]):
            a = a if isinstance(a, dict) else {"text": str(a)}
            out["answers"].append({"id": aid(i), "text": a.get("text", ""), "html": r.render(a["html"], where) if a.get("html") else None,
                                   "weight": 100 if a.get("correct") else 0, "comments": render(a.get("feedback"))})
    elif t == "true_false_question":
        correct = bool(q["answer"])
        for i, label in enumerate(["True", "False"]):
            out["answers"].append({"id": aid(i), "text": label, "weight": 100 if (label == "True") == correct else 0})
    elif t == "short_answer_question":
        for i, a in enumerate(q["answers"]):
            out["answers"].append({"id": aid(i), "text": str(a), "weight": 100})
    elif t == "fill_in_multiple_blanks_question":
        for blank, accepted in q["blanks"].items():
            for i, a in enumerate(accepted):
                out["answers"].append({"id": aid(blank, i), "text": str(a), "weight": 100, "blank_id": blank})
    elif t == "multiple_dropdowns_question":
        for blank, spec in q["blanks"].items():
            for i, opt in enumerate(spec["options"]):
                out["answers"].append({"id": aid(blank, i), "text": str(opt), "weight": 100 if opt == spec["correct"] else 0, "blank_id": blank})
    elif t == "matching_question":
        rights = [p["right"] for p in q["pairs"]] + list(q.get("distractors", []))
        match_ids = {txt: _num_id(qkey, "match", txt) for txt in rights}
        out["matches"] = [{"match_id": match_ids[txt], "text": txt} for txt in rights]
        for i, p in enumerate(q["pairs"]):
            out["answers"].append({"id": aid(i), "text": p["left"], "right": p["right"], "match_id": match_ids[p["right"]], "weight": 100})
    elif t == "numerical_question":
        for i, a in enumerate(q["answers"]):
            ans = {"id": aid(i), "weight": 100}
            if "exact" in a:
                ans.update(numerical_answer_type="exact_answer", exact=a["exact"], margin=a.get("margin", 0))
            elif "range" in a:
                ans.update(numerical_answer_type="range_answer", start=a["range"][0], end=a["range"][1])
            elif "approximate" in a:
                ans.update(numerical_answer_type="precision_answer", approximate=a["approximate"], precision=a["precision"])
            out["answers"].append(ans)
    elif t == "calculated_question":
        out.update(formula=q["formula"], variables=q["variables"], tolerance=q.get("tolerance", 0),
                   decimal_places=q.get("decimal_places", 0))
        rng = random.Random(qkey)
        for i in range(q.get("sets", 10)):
            values = {}
            for v in q["variables"]:
                scale = int(v.get("scale", 0))
                values[v["name"]] = round(rng.uniform(v["min"], v["max"]), scale) if scale else rng.randint(int(v["min"]), int(v["max"]))
            answer = round(_eval_formula(q["formula"], values), out["decimal_places"])
            out["answers"].append({"id": aid(i), "weight": 100, "variables": [{"name": k, "value": val} for k, val in values.items()], "answer": answer})
    return out


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"sqrt": math.sqrt, "floor": math.floor, "ceil": math.ceil, "round": round, "abs": abs, "min": min, "max": max}


def _eval_formula(expr: str, values: dict) -> float:
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            return values[n.id]
        if isinstance(n, ast.BinOp):
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp):
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and n.func.id in _FUNCS:
            return _FUNCS[n.func.id](*map(ev, n.args))
        raise ValueError(f"unsupported formula element: {ast.dump(n)}")

    return float(ev(ast.parse(expr, mode="eval")))


# ------------------------------------------------------------------ QTI items
def _q(parent, tag, text=None, **attrs):
    el = etree.SubElement(parent, f"{{{QTI_NS}}}{tag}")
    for k, v in attrs.items():
        el.set(k, str(v))
    if text is not None:
        el.text = str(text)
    return el


def _meta(parent, label, entry):
    f = _q(parent, "qtimetadatafield")
    _q(f, "fieldlabel", label)
    _q(f, "fieldentry", entry)


def _mattext(parent, html=None, text=None):
    mat = _q(parent, "material")
    if html:
        _q(mat, "mattext", html, texttype="text/html")
    else:
        _q(mat, "mattext", text or "", texttype="text/plain")


def add_item(section, q: dict, ident: str, *, for_cc=False, aq_ref: str | None = None):
    t = q["question_type"]
    item = _q(section, "item", ident=ident, title=q["name"])
    md = _q(_q(item, "itemmetadata"), "qtimetadata")
    if for_cc:
        _meta(md, "cc_profile", CC_PROFILES[t])
        if t == "essay_question":
            _meta(md, "qmd_computerscored", "No")
    else:
        _meta(md, "question_type", t)
        _meta(md, "points_possible", q["points_possible"])
        _meta(md, "original_answer_ids", ",".join(str(a["id"]) for a in q["answers"]))
        if t == "text_only_question":
            _meta(md, "passage", "true")
        if aq_ref:
            _meta(md, "assessment_question_identifierref", aq_ref)

    pres = _q(item, "presentation")
    _mattext(pres, html=f"<div>{q['question_text']}</div>")
    _presentation(pres, q)
    if t == "text_only_question":
        return item

    rp = _q(item, "resprocessing")
    _q(_q(rp, "outcomes"), "decvar", maxvalue="100", minvalue="0", varname="SCORE", vartype="Decimal")
    _resprocessing(rp, q)
    if t == "calculated_question":
        _calculated_ext(item, q)
    _feedback(item, "general_fb", q.get("neutral_comments"))
    _feedback(item, "correct_fb", q.get("correct_comments"))
    _feedback(item, "general_incorrect_fb", q.get("incorrect_comments"))
    for a in q["answers"]:
        _feedback(item, f"{a['id']}_fb", a.get("comments"))
    return item


def _presentation(pres, q):
    t = q["question_type"]
    if t in ("multiple_choice_question", "true_false_question", "multiple_answers_question"):
        lid = _q(pres, "response_lid", ident="response1", rcardinality="Multiple" if t == "multiple_answers_question" else "Single")
        rc = _q(lid, "render_choice")
        for a in q["answers"]:
            _mattext(_q(rc, "response_label", ident=a["id"]), html=a.get("html"), text=a["text"])
    elif t in ("short_answer_question", "essay_question"):
        rs = _q(pres, "response_str", ident="response1", rcardinality="Single")
        _q(_q(rs, "render_fib"), "response_label", ident="answer1", rshuffle="No")
    elif t == "matching_question":
        for a in q["answers"]:
            lid = _q(pres, "response_lid", ident=f"response_{a['id']}")
            _mattext(lid, text=a["text"])
            rc = _q(lid, "render_choice")
            for m in q["matches"]:
                rl = _q(rc, "response_label", ident=m["match_id"])
                _q(_q(rl, "material"), "mattext", m["text"])
    elif t in ("multiple_dropdowns_question", "fill_in_multiple_blanks_question"):
        for blank, answers in _by_blank(q).items():
            lid = _q(pres, "response_lid", ident=f"response_{blank}")
            _q(_q(lid, "material"), "mattext", blank)
            rc = _q(lid, "render_choice")
            for a in answers:
                _mattext(_q(rc, "response_label", ident=a["id"]), text=a["text"])
    elif t in ("calculated_question", "numerical_question"):
        rs = _q(pres, "response_str", ident="response1", rcardinality="Single")
        _q(_q(rs, "render_fib", fibtype="Decimal"), "response_label", ident="answer1")
    # file_upload_question / text_only_question: no response element (matches Canvas)


def _by_blank(q):
    groups: dict[str, list] = {}
    for a in q["answers"]:
        groups.setdefault(a["blank_id"], []).append(a)
    return groups


def _setvar(parent, value, action="Set"):
    _q(parent, "setvar", value, action=action, varname="SCORE")


def _other(rp, continue_="No", fb=None):
    rc = _q(rp, "respcondition", **{"continue": continue_})
    _q(_q(rc, "conditionvar"), "other")
    if fb:
        _q(rc, "displayfeedback", feedbacktype="Response", linkrefid=fb)


def _correct_fb(rc, q):
    if q.get("correct_comments"):
        _q(rc, "displayfeedback", feedbacktype="Response", linkrefid="correct_fb")


def _resprocessing(rp, q):
    t = q["question_type"]
    if q.get("neutral_comments"):
        _other(rp, "Yes", "general_fb")
    if t not in ("matching_question", "numerical_question"):
        for a in q["answers"]:
            if not a.get("comments"):
                continue
            respident = f"response_{a['blank_id']}" if t in MULTI_ANSWER_TYPES else "response1"
            rc = _q(rp, "respcondition", **{"continue": "Yes"})
            cv = _q(rc, "conditionvar")
            _q(cv, "varequal", a["text"] if t == "short_answer_question" else a["id"], respident=respident)
            _q(rc, "displayfeedback", feedbacktype="Response", linkrefid=f"{a['id']}_fb")

    if t in ("multiple_choice_question", "true_false_question"):
        correct = next((a for a in q["answers"] if a["weight"] > 0), None)
        if correct:  # surveys have no correct answer
            rc = _q(rp, "respcondition", **{"continue": "No"})
            _q(_q(rc, "conditionvar"), "varequal", correct["id"], respident="response1")
            _setvar(rc, "100")
            _correct_fb(rc, q)
    elif t == "multiple_answers_question":
        rc = _q(rp, "respcondition", **{"continue": "No"})
        and_ = _q(_q(rc, "conditionvar"), "and")
        for a in q["answers"]:
            if a["weight"] > 0:
                _q(and_, "varequal", a["id"], respident="response1")
            else:
                _q(_q(and_, "not"), "varequal", a["id"], respident="response1")
        _setvar(rc, "100")
        _correct_fb(rc, q)
    elif t == "short_answer_question":
        rc = _q(rp, "respcondition", **{"continue": "No"})
        cv = _q(rc, "conditionvar")
        for a in q["answers"]:
            _q(cv, "varequal", a["text"], respident="response1")
        _setvar(rc, "100")
        _correct_fb(rc, q)
    elif t == "essay_question":
        _other(rp)
    elif t == "matching_question":
        pts = "%.2f" % (100.0 / len(q["answers"]))
        for a in q["answers"]:
            rc = _q(rp, "respcondition")
            _q(_q(rc, "conditionvar"), "varequal", a["match_id"], respident=f"response_{a['id']}")
            _setvar(rc, pts, "Add")
    elif t in ("multiple_dropdowns_question", "fill_in_multiple_blanks_question"):
        groups = _by_blank(q)
        pts = "%.2f" % (100.0 / len(groups))
        for blank, answers in groups.items():
            # Canvas emits one condition per blank (first correct answer); alternates ride along
            # as response_labels and are restored from original_answer_ids on import.
            a = next((x for x in answers if x["weight"] > 0), None)
            if a:
                rc = _q(rp, "respcondition")
                _q(_q(rc, "conditionvar"), "varequal", a["id"], respident=f"response_{blank}")
                _setvar(rc, pts, "Add")
    elif t == "calculated_question":
        rc = _q(rp, "respcondition", title="correct")
        _q(_q(rc, "conditionvar"), "other")
        _setvar(rc, "100")
        rc = _q(rp, "respcondition", title="incorrect")
        _q(_q(_q(rc, "conditionvar"), "not"), "other")
        _setvar(rc, "0")
    elif t == "numerical_question":
        for a in q["answers"]:
            rc = _q(rp, "respcondition", **{"continue": "No"})
            cv = _q(rc, "conditionvar")
            kind = a["numerical_answer_type"]
            if kind == "exact_answer":
                or_ = _q(cv, "or")
                _q(or_, "varequal", float(a["exact"]), respident="response1")
                if a.get("margin"):
                    and_ = _q(or_, "and")
                    _q(and_, "vargte", float(a["exact"]) - float(a["margin"]), respident="response1")
                    _q(and_, "varlte", float(a["exact"]) + float(a["margin"]), respident="response1")
            elif kind == "precision_answer":
                or_ = _q(cv, "or")
                approx, precision = float(a["approximate"]), int(a["precision"])
                _q(or_, "varequal", approx, respident="response1")
                prefix, exp = (f"%.{precision - 1}E" % approx).split("E")
                rng = float(f"5E-{precision}")
                and_ = _q(or_, "and")
                _q(and_, "vargt", float(f"{float(prefix) - rng}E{exp}"), respident="response1")
                _q(and_, "varlte", float(f"{float(prefix) + rng}E{exp}"), respident="response1")
            else:
                _q(cv, "vargte", float(a["start"]), respident="response1")
                _q(cv, "varlte", float(a["end"]), respident="response1")
            _setvar(rc, "100")
            _correct_fb(rc, q)
    if q.get("incorrect_comments") and t not in MULTI_ANSWER_TYPES:
        _other(rp, "Yes", "general_incorrect_fb")


def _calculated_ext(item, q):
    ext = _q(item, "itemproc_extension")
    calc = _q(ext, "calculated")
    _q(calc, "answer_tolerance", q["tolerance"])
    forms = _q(calc, "formulas", decimal_places=q["decimal_places"])
    _q(forms, "formula", q["formula"])
    vars_ = _q(calc, "vars")
    for v in q["variables"]:
        ve = _q(vars_, "var", name=v["name"], scale=v.get("scale", 0))
        _q(ve, "min", v["min"])
        _q(ve, "max", v["max"])
    sets = _q(calc, "var_sets")
    for a in q["answers"]:
        s = _q(sets, "var_set", ident=a["id"])
        for v in a["variables"]:
            _q(s, "var", v["value"], name=v["name"])
        _q(s, "answer", a["answer"])


def _feedback(item, ident, text):
    if not text:
        return
    fb = _q(item, "itemfeedback", ident=ident)
    is_html = "<" in text and ">" in text  # Canvas: *_comments_html -> text/html, else text/plain
    _mattext(_q(fb, "flow_mat"), html=text if is_html else None, text=None if is_html else text)


# --------------------------------------------------------------- documents
def _questestinterop(schema):
    el = etree.Element(f"{{{QTI_NS}}}questestinterop", nsmap={None: QTI_NS, "xsi": "http://www.w3.org/2001/XMLSchema-instance"})
    el.set("{http://www.w3.org/2001/XMLSchema-instance}schemaLocation", f"{QTI_NS} {schema}")
    return el


def _xml(el):
    return etree.tostring(el, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def _bank_questions(course: Course, r: Renderer, bank_key: str) -> list[dict]:
    bank = course.banks[bank_key]
    return [normalize_question(q, f"bank:{bank_key}:{i}", r, f"bank:{bank_key}") for i, q in enumerate(bank["questions"])]


def quiz_points(course: Course, quiz: dict) -> float:
    total = 0.0
    for q in quiz["questions"]:
        if q["type"] == "group":
            total += float(q["points_per_item"]) * int(q["pick"])
        elif q["type"] != "text_only":
            total += float(q.get("points", 1))
    return total


def write_banks(pkg: Package, course: Course, r: Renderer) -> None:
    for key, bank in course.banks.items():
        doc = _questestinterop(QTI_XSD)
        ob = _q(doc, "objectbank", ident=bank["id"])
        md = _q(ob, "qtimetadata")
        _meta(md, "bank_title", bank["title"])
        for i, q in enumerate(_bank_questions(course, r, key)):
            add_item(ob, q, mid("aq", q["key"]))
        path = pkg.add(f"non_cc_assessments/{bank['id']}.xml.qti", _xml(doc))
        pkg.resource(bank["id"], LOR, href=path, files=[path])


def write_quizzes(pkg: Package, course: Course, r: Renderer) -> None:
    d = course.dates
    for qz in course.quizzes.values():
        where = f"quiz:{qz['key']}"
        questions = []  # (kind, payload)
        for i, raw in enumerate(qz["questions"]):
            if raw["type"] == "group":
                questions.append(("group", raw, i))
            else:
                questions.append(("q", normalize_question(raw, f"quiz:{qz['key']}:{i}", r, where), i))

        attempts = qz.get("allowed_attempts", 1)
        for for_cc in (True, False):
            doc = _questestinterop(CC_QTI_XSD if for_cc else QTI_XSD)
            asmt = _q(doc, "assessment", ident=qz["id"], title=qz["title"])
            md = _q(asmt, "qtimetadata")
            if for_cc:
                _meta(md, "cc_profile", "cc.exam.v0p1")
                _meta(md, "qmd_assessmenttype", "Examination")
                _meta(md, "qmd_scoretype", "Percentage")
            if qz.get("time_limit"):
                _meta(md, "qmd_timelimit", qz["time_limit"])
            _meta(md, "cc_maxattempts", "unlimited" if attempts == -1 else attempts)
            sec = _q(asmt, "section", ident="root_section")
            for kind, payload, i in questions:
                if kind == "q":
                    if for_cc:
                        if payload["question_type"] in CC_PROFILES:
                            add_item(sec, payload, mid("qq", payload["key"]), for_cc=True)
                    else:
                        add_item(sec, payload, mid("qq", payload["key"]))
                else:
                    _group(sec, payload, qz, i, course, r, for_cc)
            if for_cc:
                cc_path = pkg.add(f"{qz['id']}/assessment_qti.xml", _xml(doc))
            else:
                full_path = pkg.add(f"non_cc_assessments/{qz['id']}.xml.qti", _xml(doc))

        meta_path = pkg.add(f"{qz['id']}/assessment_meta.xml", tostring(_assessment_meta(qz, course, r)))
        pkg.resource(qz["id"], ASSESSMENT, files=[cc_path], dependencies=[qz["meta_id"]])
        pkg.resource(qz["meta_id"], LOR, href=meta_path, files=[meta_path, full_path])


def _group(sec, g, qz, i, course, r, for_cc):
    if for_cc:  # CC copy inlines up to pick_count supported questions
        source = _bank_questions(course, r, g["bank"]) if g.get("bank") else [
            normalize_question(q, f"quiz:{qz['key']}:{i}:{j}", r, f"quiz:{qz['key']}") for j, q in enumerate(g["questions"])]
        chosen = [q for q in source if q["question_type"] in CC_PROFILES][: int(g["pick"])]
        for q in chosen:
            add_item(sec, q, mid("qq", f"cc:{q['key']}"), for_cc=True)
        return
    gs = _q(sec, "section", ident=mid("quiz_group", f"{qz['key']}:{i}"), title=g["name"])
    sel = _q(_q(gs, "selection_ordering"), "selection")
    if g.get("bank"):
        _q(sel, "sourcebank_ref", course.banks[g["bank"]]["id"])
    _q(sel, "selection_number", g["pick"])
    _q(_q(sel, "selection_extension"), "points_per_item", float(g["points_per_item"]))
    if not g.get("bank"):
        for j, raw in enumerate(g["questions"]):
            q = normalize_question(raw, f"quiz:{qz['key']}:{i}:{j}", r, f"quiz:{qz['key']}")
            add_item(gs, q, mid("qq", q["key"]))


def _assessment_meta(qz: dict, course: Course, r: Renderer):
    d = course.dates
    quiz_type = qz.get("quiz_type", "assignment")
    points = quiz_points(course, qz)
    el = root("quiz", identifier=qz["id"])
    sub(el, "title", qz["title"])
    sub(el, "description", r.html(qz, "description", f"quiz:{qz['key']}"), keep_empty=True)
    if qz.get("lock"):
        sub(el, "lock_at", d.utc(qz["lock"]))
    if qz.get("unlock"):
        sub(el, "unlock_at", d.utc(qz["unlock"]))
    if qz.get("due"):
        sub(el, "due_at", d.utc(qz["due"]))
    sub(el, "shuffle_answers", bool(qz.get("shuffle_answers", False)))
    sub(el, "scoring_policy", qz.get("scoring_policy", "keep_highest"))
    if qz.get("hide_results"):
        sub(el, "hide_results", qz["hide_results"])
    else:
        sub(el, "hide_results", None, keep_empty=True)
    sub(el, "quiz_type", quiz_type)
    sub(el, "points_possible", points)
    sub(el, "require_lockdown_browser", False)
    sub(el, "require_lockdown_browser_for_results", False)
    sub(el, "require_lockdown_browser_monitor", False)
    sub(el, "lockdown_browser_monitor_data", None, keep_empty=True)
    if qz.get("access_code"):
        sub(el, "access_code", qz["access_code"])
    if qz.get("ip_filter"):
        sub(el, "ip_filter", qz["ip_filter"])
    sub(el, "show_correct_answers", bool(qz.get("show_correct_answers", True)))
    if qz.get("show_correct_answers_at"):
        sub(el, "show_correct_answers_at", d.utc(qz["show_correct_answers_at"]))
    if qz.get("hide_correct_answers_at"):
        sub(el, "hide_correct_answers_at", d.utc(qz["hide_correct_answers_at"]))
    sub(el, "anonymous_submissions", bool(qz.get("anonymous_submissions", False)))
    sub(el, "could_be_locked", bool(qz.get("lock") or qz.get("unlock")))
    if qz.get("time_limit"):
        sub(el, "time_limit", qz["time_limit"])
    sub(el, "disable_timer_autosubmission", False)
    sub(el, "allowed_attempts", qz.get("allowed_attempts", 1))
    sub(el, "one_question_at_a_time", bool(qz.get("one_question_at_a_time", False)))
    sub(el, "cant_go_back", bool(qz.get("cant_go_back", False)))
    sub(el, "available", bool(qz.get("published", True)))
    sub(el, "one_time_results", bool(qz.get("one_time_results", False)))
    sub(el, "show_correct_answers_last_attempt", bool(qz.get("show_correct_answers_last_attempt", False)))
    sub(el, "only_visible_to_overrides", bool(qz.get("only_visible_to_overrides", False)))
    sub(el, "module_locked", False)
    if qz.get("assignment_id"):
        a = {k: v for k, v in qz.items() if k not in ("questions", "description")}
        a.update(points=points, quiz_identifierref=qz["id"])
        ae = node(el, "assignment", identifier=qz["assignment_id"])
        assignment_fields(ae, a, course, submission_types=["online_quiz"])
    if qz.get("group"):
        sub(el, "assignment_group_identifierref", course.assignment_groups[qz["group"]]["id"])
    node(el, "assignment_overrides")
    return el
