"""Import the cartridge into Canvas and seed everything a cartridge can't carry.

    cc seed     [--theme NAME] [--config seeder/config.toml] [--new-quizzes] [--skip-import]
    cc verify   [--theme NAME] [--manifest dist/seed-manifest-<theme>.json]
    cc teardown [--theme NAME] [--manifest dist/seed-manifest-<theme>.json] [--yes]

The roster (people, sections, groups, overrides, activity) is the theme's ``roster.yaml``; SIS
prefix, email domain and the orientation module / intro discussion keys come from theme.yaml ``seeder``.

Idempotent: re-running finds existing course/sections/users/groups/overrides by SIS id or name
and only creates what's missing. Non-fatal problems (e.g. a permission your token lacks) are
collected and printed at the end instead of aborting the run.
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
from datetime import date
from pathlib import Path

import yaml

from cc_gen.content import load_course
from cc_gen.theme import load_theme

from .canvas_api import Canvas, CanvasError

ROOT = Path(__file__).resolve().parent.parent


def run(args) -> int:
    cfg = _config(args.config)
    theme = load_theme(args.theme or cfg.get("theme"))
    cfg["theme"] = theme
    for k, v in (("sis_prefix", theme.seeder.get("sis_prefix", theme.namespace)),
                 ("email_domain", theme.seeder.get("email_domain", f"{theme.namespace}.example.com"))):
        cfg.setdefault(k, v)
    manifest = ROOT / "dist" / f"seed-manifest-{theme.name}.json"
    for attr in ("manifest_out", "manifest"):
        if hasattr(args, attr) and getattr(args, attr) is None:
            setattr(args, attr, manifest)
    if hasattr(args, "package") and args.package is None:
        args.package = ROOT / "dist" / theme.output_name
    api = Canvas(cfg["base_url"], cfg["token"], verbose=cfg.get("verbose", False))
    if args.cmd == "seed":
        return Seeder(api, cfg, args).run()
    if args.cmd == "verify":
        return verify(api, cfg, args)
    if args.cmd == "teardown":
        return teardown(api, cfg, args)
    return 2


def _config(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"missing {path}; copy seeder/config.example.toml and fill it in")
    cfg = tomllib.loads(path.read_text())
    cfg["token"] = os.environ.get("CANVAS_TOKEN") or cfg.get("token")
    if not cfg.get("token"):
        sys.exit("set CANVAS_TOKEN or token in the config")
    return cfg


class Seeder:
    def __init__(self, api: Canvas, cfg: dict, args):
        self.api, self.cfg, self.args = api, cfg, args
        self.p = cfg["sis_prefix"]
        self.theme = cfg["theme"]
        self.roster = yaml.safe_load(self.theme.roster_path.read_text())
        anchor = date.fromisoformat(cfg["anchor"]) if cfg.get("anchor") else None
        self.course_model = load_course(self.theme, anchor=anchor)
        self.dates = self.course_model.dates
        self.problems: list[str] = []
        self.m: dict = {"theme": self.theme.name, "base_url": api.base, "sis_prefix": self.p, "new_quizzes": bool(getattr(args, "new_quizzes", False)),
                        "anchor": self.dates.anchor.isoformat(), "sections": {}, "users": {}, "groups": {},
                        "group_categories": {}, "assignments": {}, "quizzes": {}, "discussions": {}, "pages": {}, "modules": {}}

    def soft(self, what: str, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except CanvasError as e:
            self.problems.append(f"{what}: {e}")
            print(f"  ! {what}: HTTP {e.status}")
            return None

    # ------------------------------------------------------------------ run
    def run(self) -> int:
        cid = self.course()
        if not self.args.skip_import:
            self.import_package(cid)
        self.index_content(cid)
        self.sections(cid)
        self.users(cid)
        self.groups(cid)
        self.overrides(cid)
        self.activity(cid)
        self.grading(cid)
        self.lti(cid)
        out = self.args.manifest_out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.m, indent=2, ensure_ascii=False))
        print(f"\ncourse: {self.m['course']['url']}\nwrote {out}")
        if self.problems:
            print(f"\n{len(self.problems)} non-fatal problem(s):")
            for p in self.problems:
                print(f"  - {p}")
        return 0

    # --------------------------------------------------------------- course
    def course(self) -> int:
        a = self.api
        sis = f"{self.p}-course"
        if self.cfg.get("course_id"):
            c = a.get(f"courses/{self.cfg['course_id']}")
        else:
            c = a.get_or_none(f"courses/sis_course_id:{sis}")
            if c is None:
                print("creating course")
                c = a.post(f"accounts/{self.cfg['account_id']}/courses", {
                    "course[name]": self.course_model.settings["title"],
                    "course[course_code]": self.course_model.settings["course_code"],
                    "course[sis_course_id]": sis,
                    "course[time_zone]": self.course_model.settings["time_zone"],
                    "enroll_me": "false",
                })
        self.m["course"] = {"id": c["id"], "sis_course_id": c.get("sis_course_id"), "url": f"{a.base}/courses/{c['id']}"}
        print(f"course {c['id']}: {c['name']}")
        return c["id"]

    def import_package(self, cid: int) -> None:
        a, pkg = self.api, Path(self.args.package)
        if not pkg.exists():
            sys.exit(f"{pkg} not found: run `cc build` first")
        print(f"uploading {pkg.name} ({pkg.stat().st_size / 1e6:.1f} MB)")
        data = {
            "migration_type": "canvas_cartridge_importer",
            "pre_attachment[name]": pkg.name,
            "pre_attachment[size]": str(pkg.stat().st_size),
            "settings[import_quizzes_next]": "true" if self.args.new_quizzes else "false",
        }
        mig = a.post(f"courses/{cid}/content_migrations", data)
        a.upload(mig["pre_attachment"], pkg)
        prog = a.wait_progress(mig["progress_url"])
        issues = a.paginate(f"courses/{cid}/content_migrations/{mig['id']}/migration_issues")
        self.m["migration"] = {"id": mig["id"], "state": prog["workflow_state"], "issues": [
            {"type": i.get("issue_type"), "description": i.get("description"), "error": i.get("error_message")} for i in issues]}
        print(f"import {prog['workflow_state']} with {len(issues)} migration issue(s)")
        for i in issues:
            print(f"  [{i.get('issue_type')}] {i.get('description')}")
        if prog["workflow_state"] == "failed":
            sys.exit("content migration failed")

    def index_content(self, cid: int) -> None:
        """Map content keys -> Canvas ids by title (titles are unique within a theme; a test enforces it)."""
        a, cm = self.api, self.course_model
        by_title = {x["name"]: x for x in a.paginate(f"courses/{cid}/assignments")}
        self._assignments = by_title
        for key, obj in cm.assignments.items():
            if obj["title"] in by_title:
                self.m["assignments"][key] = {"id": by_title[obj["title"]]["id"], "title": obj["title"]}
        quizzes = {q["title"]: q for q in self.soft("list quizzes", a.paginate, f"courses/{cid}/quizzes") or []}
        for key, q in cm.quizzes.items():
            if q["title"] in quizzes:
                self.m["quizzes"][key] = {"id": quizzes[q["title"]]["id"], "assignment_id": quizzes[q["title"]].get("assignment_id"), "title": q["title"]}
            elif q["title"] in by_title:  # imported as New Quizzes
                self.m["quizzes"][key] = {"assignment_id": by_title[q["title"]]["id"], "title": q["title"], "new_quiz": True}
        topics = {t["title"]: t for t in a.paginate(f"courses/{cid}/discussion_topics")}
        for key, d in cm.discussions.items():
            if d["title"] in topics:
                self.m["discussions"][key] = {"id": topics[d["title"]]["id"], "title": d["title"]}
        pages = {p["title"]: p for p in a.paginate(f"courses/{cid}/pages")}
        for key, pg in cm.pages.items():
            if pg["title"] in pages:
                self.m["pages"][key] = {"url": pages[pg["title"]]["url"], "page_id": pages[pg["title"]].get("page_id"), "title": pg["title"]}
        mods = {mo["name"]: mo for mo in a.paginate(f"courses/{cid}/modules")}
        for mo in cm.modules:
            if mo["title"] in mods:
                self.m["modules"][mo["key"]] = {"id": mods[mo["title"]]["id"], "title": mo["title"]}
        missing = [k for k in cm.assignments if k not in self.m["assignments"]]
        if missing:
            self.problems.append(f"assignments not found after import: {missing}")

    # ------------------------------------------------------------- sections
    def sections(self, cid: int) -> None:
        a = self.api
        existing = {s.get("sis_section_id"): s for s in a.paginate(f"courses/{cid}/sections")}
        for s in self.roster["sections"]:
            sis = f"{self.p}-sec-{s['key']}"
            sec = existing.get(sis)
            if not sec:
                data = {"course_section[name]": s["name"], "course_section[sis_section_id]": sis}
                if s.get("start"):
                    data["course_section[start_at]"] = self.dates.local(s["start"]).isoformat()
                    data["course_section[end_at]"] = self.dates.local(s["end"]).isoformat()
                    data["course_section[restrict_enrollments_to_section_dates]"] = "true"
                sec = a.post(f"courses/{cid}/sections", data)
                print(f"section {s['name']}")
            self.m["sections"][s["key"]] = {"id": sec["id"], "name": s["name"], "sis_section_id": sis}

    # ---------------------------------------------------------------- users
    def users(self, cid: int) -> None:
        a = self.api
        acct = self.cfg["account_id"]
        enrollments = a.paginate(f"courses/{cid}/enrollments", {"state[]": ["active", "invited", "inactive", "completed", "creation_pending"]})
        have = {(e["user_id"], e["course_section_id"], e["type"]) for e in enrollments}
        for u in self.roster["users"]:
            sis = f"{self.p}-{u['key']}"
            login = f"{u['key']}@{self.cfg['email_domain']}"
            user = a.get_or_none(f"users/sis_user_id:{sis}")
            if user is None:
                data = {
                    "user[name]": u["name"], "user[short_name]": u.get("short", u["name"]),
                    "user[sortable_name]": u.get("sortable", ", ".join(reversed(u["name"].split(" ", 1)))),
                    "user[skip_registration]": "true", "user[terms_of_use]": "true",
                    "pseudonym[unique_id]": login, "pseudonym[sis_user_id]": sis, "pseudonym[send_confirmation]": "false",
                    "communication_channel[type]": "email", "communication_channel[address]": login,
                    "communication_channel[skip_confirmation]": "true", "enable_sis_reactivation": "true",
                }
                if self.cfg.get("user_password"):
                    data["pseudonym[password]"] = self.cfg["user_password"]
                user = a.post(f"accounts/{acct}/users", data)
                print(f"user {u['name']}")
            self.m["users"][u["key"]] = {"id": user["id"], "name": u["name"], "login": login, "sis_user_id": sis,
                                         "role": u["role"], "sections": u["sections"], "note": u.get("note")}
        for u in self.roster["users"]:
            uid = self.m["users"][u["key"]]["id"]
            for sk in u["sections"]:
                sid = self.m["sections"][sk]["id"]
                if (uid, sid, u["role"]) in have:
                    continue
                data = {"enrollment[user_id]": uid, "enrollment[type]": u["role"], "enrollment[enrollment_state]": "active",
                        "enrollment[notify]": "false"}
                if u.get("limit_privileges_to_course_section"):
                    data["enrollment[limit_privileges_to_course_section]"] = "true"
                if u.get("observes"):
                    data["enrollment[associated_user_id]"] = self.m["users"][u["observes"]]["id"]
                e = self.soft(f"enroll {u['key']} in {sk}", a.post, f"sections/{sid}/enrollments", data)
                if e and u.get("state") in ("inactive", "completed"):
                    task = "deactivate" if u["state"] == "inactive" else "conclude"
                    self.soft(f"{task} {u['key']}", a.delete, f"courses/{cid}/enrollments/{e['id']}", params={"task": task})
        self.m["users_by_role"] = {}
        for k, v in self.m["users"].items():
            self.m["users_by_role"].setdefault(v["role"], []).append(k)

    # --------------------------------------------------------------- groups
    def groups(self, cid: int) -> None:
        a = self.api
        cats = {c["name"]: c for c in a.paginate(f"courses/{cid}/group_categories")}
        for gs in self.roster["group_sets"]:
            cat = cats.get(gs["name"])
            if not cat:
                data = {"name": gs["name"]}
                if gs.get("self_signup"):
                    data["self_signup"] = gs["self_signup"]
                if gs.get("create_group_count"):
                    data["create_group_count"] = gs["create_group_count"]
                cat = a.post(f"courses/{cid}/group_categories", data)
                print(f"group set {gs['name']}")
            self.m["group_categories"][gs["name"]] = {"id": cat["id"]}
            existing = {g["name"]: g for g in a.paginate(f"group_categories/{cat['id']}/groups")}
            for gname, members in (gs.get("groups") or {}).items():
                g = existing.get(gname) or a.post(f"group_categories/{cat['id']}/groups", {"name": gname})
                self.m["groups"][gname] = {"id": g["id"], "group_category": gs["name"], "members": members}
                current = {m["user_id"] for m in a.paginate(f"groups/{g['id']}/memberships")}
                for mk in members:
                    uid = self.m["users"][mk]["id"]
                    if uid not in current:
                        self.soft(f"add {mk} to {gname}", a.post, f"groups/{g['id']}/memberships", {"user_id": uid})

    # ------------------------------------------------------------ overrides
    def overrides(self, cid: int) -> None:
        a = self.api
        for o in self.roster["overrides"]:
            asg = self.m["assignments"].get(o["assignment"])
            if not asg:
                self.problems.append(f"override: assignment {o['assignment']} not found")
                continue
            existing = a.paginate(f"courses/{cid}/assignments/{asg['id']}/overrides")
            data = {"assignment_override[due_at]": self.dates.local(o["due"]).isoformat()}
            if o.get("section"):
                sid = self.m["sections"][o["section"]]["id"]
                if any(x.get("course_section_id") == sid for x in existing):
                    continue
                data["assignment_override[course_section_id]"] = sid
            elif o.get("group"):
                gid = self.m["groups"][o["group"]]["id"]
                if any(x.get("group_id") == gid for x in existing):
                    continue
                data["assignment_override[group_id]"] = gid
            else:
                if any(x.get("title") == o.get("title") for x in existing):
                    continue
                for s in o["students"]:
                    data.setdefault("assignment_override[student_ids][]", []).append(self.m["users"][s]["id"])
                data["assignment_override[title]"] = o.get("title", "Extension")
            self.soft(f"override on {o['assignment']}", a.post, f"courses/{cid}/assignments/{asg['id']}/overrides", data)
        print("overrides done")

    # ------------------------------------------------------------- activity
    def activity(self, cid: int) -> None:
        if not self.cfg.get("masquerade", True):
            print("masquerade disabled: skipping student activity")
            return
        a, cm = self.api, self.course_model
        orient_key = self.theme.seeder.get("orientation_module", cm.modules[0]["key"])
        orient = next(mo for mo in cm.modules if mo["key"] == orient_key)
        mod_id = self.m["modules"].get(orient_key, {}).get("id")
        items = {it["title"]: it for it in a.paginate(f"courses/{cid}/modules/{mod_id}/items")} if mod_id else {}
        intro = self.m["discussions"].get(self.theme.seeder.get("intro_discussion", ""))
        for sk in self.roster["progress"]["complete_orientation"]:
            uid = self.m["users"][sk]["id"]
            for it in orient["items"]:
                live = items.get(it["title"])
                if not live or not it.get("requirement"):
                    continue
                if it["requirement"] == "must_view":
                    # viewing through the module item sequence marks must_view requirements
                    self.soft(f"{sk} view {it['title']}", a.post, f"courses/{cid}/modules/{mod_id}/items/{live['id']}/mark_read", as_user=uid)
                elif it["requirement"] == "must_mark_done":
                    self.soft(f"{sk} mark done", a.put, f"courses/{cid}/modules/{mod_id}/items/{live['id']}/done", as_user=uid)
            msg = self.roster["progress"]["intro_posts"].get(sk)
            if intro and msg:
                self.soft(f"{sk} intro post", a.post, f"courses/{cid}/discussion_topics/{intro['id']}/entries", {"message": msg}, as_user=uid)
        print("orientation progress done")

        for s in self.roster["submissions"]:
            asg = self.m["assignments"].get(s["assignment"])
            uid = self.m["users"][s["user"]]["id"]
            if not asg:
                continue
            data = {"submission[submission_type]": s["type"]}
            if s["type"] == "online_text_entry":
                data["submission[body]"] = s["body"]
            elif s["type"] == "online_url":
                data["submission[url]"] = s["url"]
            elif s["type"] == "online_upload":
                local = self._seed_file(s["file"])
                pre = self.soft(f"{s['user']} upload preflight", a.post,
                                f"courses/{cid}/assignments/{asg['id']}/submissions/{uid}/files",
                                {"name": local.name, "size": str(local.stat().st_size)}, as_user=uid)
                if not pre:
                    continue
                f = self.soft(f"{s['user']} upload", a.upload, pre, local)
                if not f:
                    continue
                data["submission[file_ids][]"] = f["id"]
            self.soft(f"{s['user']} submits {s['assignment']}", a.post,
                      f"courses/{cid}/assignments/{asg['id']}/submissions", data, as_user=uid)
        print("submissions done")

    # -------------------------------------------------------------- grading
    def grading(self, cid: int) -> None:
        a = self.api
        rubric_cache = {}
        for g in self.roster["grades"]:
            asg = self.m["assignments"].get(g["assignment"])
            if not asg:
                continue
            uid = self.m["users"][g["user"]]["id"]
            data = {}
            if g.get("excuse"):
                data["submission[excuse]"] = "true"
            if g.get("grade") is not None:
                data["submission[posted_grade]"] = g["grade"]
            if g.get("rubric"):
                if asg["id"] not in rubric_cache:
                    full = a.get(f"courses/{cid}/assignments/{asg['id']}")
                    rubric_cache[asg["id"]] = {c["description"]: c["id"] for c in full.get("rubric") or []}
                for desc, pts in g["rubric"].items():
                    crit = rubric_cache[asg["id"]].get(desc)
                    if crit:
                        data[f"rubric_assessment[{crit}][points]"] = pts
            if g.get("comment"):
                data["comment[text_comment]"] = g["comment"]
            self.soft(f"grade {g['user']} on {g['assignment']}", a.put,
                      f"courses/{cid}/assignments/{asg['id']}/submissions/{uid}", data)
        fg = self.roster.get("final_grader")
        if fg and fg["assignment"] in self.m["assignments"]:
            self.soft("set final grader", a.put, f"courses/{cid}/assignments/{self.m['assignments'][fg['assignment']]['id']}",
                      {"assignment[final_grader_id]": self.m["users"][fg["user"]]["id"]})
        print("grading done")

    def _seed_file(self, course_path: str) -> Path:
        """Extract a course file from the built package to upload as a student submission."""
        import zipfile

        local = ROOT / "build" / "seed-files" / Path(course_path).name
        local.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(self.args.package) as z:
            local.write_bytes(z.read(f"web_resources/{course_path}"))
        return local

    def lti(self, cid: int) -> None:
        client_id = self.cfg.get("lti_client_id")
        if not client_id:
            return
        tools = self.api.paginate(f"courses/{cid}/external_tools")
        if any(str(t.get("developer_key_id")) == str(client_id) or t.get("client_id") == client_id for t in tools):
            return
        t = self.soft("install LTI 1.3 tool", self.api.post, f"courses/{cid}/external_tools", {"client_id": client_id})
        if t:
            self.m["external_tool"] = {"id": t["id"], "name": t.get("name"), "client_id": client_id}
            print(f"installed LTI tool {t.get('name')}")


# ---------------------------------------------------------------- verify
def verify(api: Canvas, cfg: dict, args) -> int:
    m = json.loads(Path(args.manifest).read_text())
    cid = m["course"]["id"]
    theme = cfg["theme"]
    cm = load_course(theme, anchor=date.fromisoformat(m["anchor"]))
    roster = yaml.safe_load(theme.roster_path.read_text())
    checks = []

    def check(name, expected, actual):
        ok = expected == actual
        checks.append(ok)
        print(f"  {'✓' if ok else '✗'} {name}: expected {expected}, got {actual}")

    modules = api.paginate(f"courses/{cid}/modules", {"include[]": "items"})
    check("modules", len(cm.modules), len(modules))
    live_by_name = {mo["name"]: mo for mo in modules}
    for mo in cm.modules:
        live = live_by_name.get(mo["title"])
        check(f"items in '{mo['title'][:40]}'", len(mo["items"]), len(live.get("items") or []) if live else None)
    pages = api.paginate(f"courses/{cid}/pages")
    check("pages", len(cm.pages), len(pages))
    graded_discussions = sum(1 for d in cm.discussions.values() if d.get("graded"))
    graded_quizzes = sum(1 for q in cm.quizzes.values() if q.get("assignment_id"))
    expected_assignments = len(cm.assignments) + graded_discussions + graded_quizzes
    if m.get("new_quizzes"):
        expected_assignments += sum(1 for q in cm.quizzes.values() if not q.get("assignment_id"))  # practice quizzes become assignments
    assignments = api.paginate(f"courses/{cid}/assignments")
    check("assignments (incl. graded quizzes + discussions)", expected_assignments, len(assignments))
    if not m.get("new_quizzes"):
        check("classic quizzes", len(cm.quizzes), len(api.paginate(f"courses/{cid}/quizzes")))
    topics = api.paginate(f"courses/{cid}/discussion_topics")
    check("discussion topics", len(cm.discussions), len(topics))
    anns = api.paginate(f"courses/{cid}/discussion_topics", {"only_announcements": "true"})
    check("announcements", len(cm.announcements), len(anns))
    groups = api.paginate(f"courses/{cid}/assignment_groups")
    check("assignment groups", len(cm.assignment_groups), len(groups))
    files = api.paginate(f"courses/{cid}/files")
    print(f"  · files in course: {len(files)} (informational; Canvas may add/dedupe)")
    rubrics = api.paginate(f"courses/{cid}/rubrics")
    check("rubrics", len(cm.rubrics), len(rubrics))
    sections = api.paginate(f"courses/{cid}/sections")
    check("sections (incl. default)", len(roster["sections"]) + 1, len(sections))
    issues = m.get("migration", {}).get("issues", [])
    errors = [i for i in issues if i.get("type") == "error"]
    check("migration errors", 0, len(errors))
    print(f"verify: {sum(checks)}/{len(checks)} checks passed")
    return 0 if all(checks) else 1


def teardown(api: Canvas, cfg: dict, args) -> int:
    m = json.loads(Path(args.manifest).read_text())
    cid = m["course"]["id"]
    users = m.get("users", {})
    if not args.yes:
        ans = input(f"Delete course {cid} ({m['course']['url']}) and {len(users)} {m['sis_prefix']}-* users? [y/N] ")
        if ans.strip().lower() != "y":
            print("aborted")
            return 1
    api.delete(f"courses/{cid}", params={"event": "delete"})
    print(f"deleted course {cid}")
    for k, u in users.items():
        try:
            api.delete(f"accounts/{cfg['account_id']}/users/{u['id']}")
            print(f"deleted user {k}")
        except CanvasError as e:
            print(f"  ! user {k}: HTTP {e.status}")
    return 0
