# CLAUDE.md: Canvas test-course generator

Read this before changing anything. It briefs future agents. Its main job: let you build a **new content theme** (e.g. "Public Domain Academy") that produces a course with the **exact same test matrix** as the reference Pokémon theme, without touching generator code.

## 1. What this project is

A theme-agnostic Python generator (`cc build`) that writes a **Canvas Course Export Package** (`.imscc`), plus a REST **seeder** (`cc seed`). Together they produce a feature-dense Canvas course for testing **LTI tools** against native Canvas features. The owner builds LTI tools (Atomic Jolt). The course is a test fixture, not a product.

**Prime directive:** "This is a test fixture course. The theme is filler. Every item exists to exercise a native Canvas feature."
- Every item must earn its place by exercising a Canvas feature, recorded in `tests: [...]`.
- When adding fun content, pair it with an untested Canvas setting. Never add content that tests nothing new.

**Themes** (`themes/<name>/`) hold all content. `cc_gen/` holds all Canvas mechanics and never names a theme. The default theme name appears only as the fallback in `cc_gen/theme.py` (`public-domain`), overridable with `$CC_THEME`. Other themes get CLI shortcut flags from theme.yaml `cli_flags` (Pokémon: `-p`/`-P`/`--pokemon`).

| Theme | Status | Notes |
|---|---|---|
| `public-domain` | **Default.** Commercial use. | "Public Domain Academy", a course about public-domain law. 386 CC0 museum images (AIC + Met), PD media (LoC Edison film, US Marine Band). Provenance in `themes/public-domain/PROVENANCE.md`, `data/collection.csv`, `data/provenance.jsonl`. `namespace: pda`, `commercial: true`. |
| `pokemon` | Reference theme (`REFERENCE_THEME`). Internal use only. | "Pokémon Trainer Academy: Gen I–III". Trademarked names, official artwork fetched from pokemon.com. `namespace: pta`, `commercial: false`. Build with `cc build -p`. |

**Status (2026-10-22):**
- Theme-agnostic refactor done. The Pokémon output is **byte-identical** to the pre-refactor build: checked with `cmp` for the offline, official-art and LTI variants, and guarded by a golden digest test.
- Static validation passes, and 96 pytest tests pass (1 skip: the commercial-only forbid test for Pokémon).
- The `public-domain` theme is the default and has the **identical test matrix** (`cc matrix --theme public-domain --compare pokemon --ignore-tests`). It passes `--forbid-theme pokemon` and the client hygiene check, and builds byte-reproducibly with all 386 CC0 images (7.9 MB at `max_size: 256`).
- `cc_gen/matrix.py` signature: bank lines are sorted (bank order is theme content) and AGS `line_item` `tag`/`resource_id` are reduced to `<ref>`. Build output is unaffected.
- The course has **never been imported into a live Canvas**, and the seeder has **never run against a real API** (no credentials were available). The first live run is the real verification (§10).

## 2. Hard rules

1. **Client data.** `examples/OTC_PROPRIETARY_EXPORT.imscc` is a real client's course.
   - Use it for XML *structure* only. Never copy its titles, text, URLs, ids, LTI keys or questions anywhere.
   - Redact secrets when printing from it.
   - `examples/` is gitignored and must stay that way.
   - After content changes, run `uv run cc validate --theme T --hygiene-against examples/OTC_PROPRIETARY_EXPORT.imscc`. It must pass. The test suite runs it for every theme when the file exists.
2. **Pokémon output must not change by accident.**
   - `tests/test_pokemon.py::test_golden_output` pins the sha256 of `cc build --theme pokemon --images generated --seed 151 --anchor 2026-10-21`.
   - Any refactor of `cc_gen/` must keep it green.
   - If you *intentionally* change Pokémon content or output, update `GOLDEN_SHA256` in the same change and say so.
   - For refactors that touch fetched-art code paths, also `cmp` an `--images auto` build before and after.
3. **Never change a theme's `namespace`.** Every migration id derives from it, so changing it makes re-imports duplicate instead of update. A new theme gets a new, unique namespace.
4. **Tests stay offline and deterministic.** Use `images_mode="generated"` and a fixed `seed=`. Never let pytest hit the network: fake `cc_gen.images._fetch`.
5. **Same theme + anchor + seed ⇒ byte-identical `.imscc`.** Don't introduce wall-clock time, unordered iteration or unseeded randomness. `Package.write_zip` uses fixed zip timestamps, and the PDF and DOCX builders use `FIXED_DATE`.
6. **Every cross-reference is checked.** A dangling page, assignment, quiz, discussion, file or module-item reference fails the build. Keep it that way.
7. **The seeder maps content to Canvas ids by title.** Titles must be unique per type within a theme, and assignments, graded quizzes and graded discussions share one namespace. A test enforces this.
8. **Commercial themes** (`commercial: true`) may contain no material from non-commercial themes. The test suite enforces this with `--forbid-theme` (§8). All images must come from verifiably PD/CC0 sources, with licence evidence recorded (§9).
9. Don't commit or push unless asked. Remote: `git@github.com:JoshBothell/perfect-canvas-test-course.git`.

## 3. Commands

```bash
uv sync --extra dev
uv run cc themes                                   # list themes (name, flags, namespace, commercial, title)
uv run cc build                                    # default theme: dist/public-domain-academy.imscc + validation
uv run cc build -p                                 # Pokémon (-p / -P / --pokemon = --theme pokemon): dist/pokemon-trainer-academy.imscc
uv run cc build [--theme T]                        # dist/<theme output>.imscc + validation; prints seed + image summary
uv run cc build --theme T --seed 123 --anchor 2026-10-21   # exact reproduction
uv run cc build --images generated                 # offline (auto|required|generated); --art-quality palette|original; --refresh-images
uv run cc build --lti themes/T/lti.example.yaml    # include the LTI Sandbox module (or create themes/T/lti.yaml, gitignored)
uv run cc validate [path] [--theme T] [--hygiene-against ref.imscc] [--forbid-theme OTHER ...]
uv run cc matrix [--theme T]                       # write the theme's matrix doc (theme.yaml matrix_doc)
uv run cc matrix --theme NEW --compare pokemon --ignore-tests   # THE same-matrix check (exit 1 + diff if not)
uv run cc matrix --theme T --signature             # print the theme-independent signature
uv run pytest -q                                   # ~13s/theme, offline; contract tests run once per theme
uv run cc seed|verify|teardown [--theme T]         # live Canvas; needs seeder/config.toml (see config.example.toml)
```

`--sprites` and `--refresh-sprites` are kept as aliases of `--images` and `--refresh-images`, and `official` is an alias of `required`.

**Tooling:**
- Python 3.12+ (the dev machine runs 3.14), managed by `uv`.
- Dependencies: jinja2, pyyaml, lxml, requests, pillow (with WebP/AVIF; no libimagequant), fpdf2, pytest.
- ffmpeg was used **once** to make the static clips in `themes/pokemon/files/media/`. It is not a build dependency.

## 4. Architecture

```
themes/<name>/ ──load_theme()──► Theme ──load_course()──► Course ──Renderer──► writers/* ──► Package ──► .imscc ──► validate()
   theme.yaml + theme.py hooks        │                                                 ▲
                         images.fetch_all() (before rendering)          assets.generate() (after rendering)
```

`build()` and `load_course()` run inside `ids.use_namespace(theme.namespace)`.

| Path | Role |
|---|---|
| `cc_gen/theme.py` | **`Theme`**: loads `theme.yaml` + optional `theme.py` (by file path), generic catalog loader, `ImageSource`, `HOOKS` contract, `available()`. |
| `cc_gen/content.py` | Loads a theme's YAML into `Course` registries (pages, assignments, quizzes, banks, discussions, announcements, files, rubrics, outcomes, grading_standards, assignment_groups, events, modules). Holds the **`identify`** question generator (`GENERATORS`, `_expand_generated`). Holds the **`Renderer`**: Jinja env, link helpers that emit Canvas placeholders and record every ref in `course.refs`, and catalog helpers. |
| `cc_gen/ids.py` | `mid(kind, key) = "g" + md5(f"{namespace}:{kind}:{key}")`, using a ContextVar namespace. It raises outside `use_namespace`. `slugify` ≈ Canvas `to_url`. |
| `cc_gen/dates.py` | `DateResolver`: specs `"+7d@23:59"`, `"-2d"`, `"+2w"`, `"dst_next@01:30"`, ISO. Resolved in the course TZ against `--anchor` (default today). Output is UTC with **no suffix** (Canvas `IMS_DATETIME`). |
| `cc_gen/writers/common.py` | `assignment_fields()` (the `<assignment>` element, shared by assignment_settings, quiz meta and topicMeta), and `html_page()` (byte-compatible with Canvas). |
| `cc_gen/writers/items.py` | Pages, assignments, discussions + announcements, web links, LTI tool XML. |
| `cc_gen/writers/qti.py` | Classic Quizzes and banks: `normalize_question`, QTI for all 12 types, groups, `assessment_meta.xml`. Also the **masked-image feedback hook**: any question that renders `masked(...)` gets the unmasked `entry_image` as HTML general feedback. |
| `cc_gen/writers/settings.py` | `course_settings/*`. **Written after content**, because files_meta needs every referenced file. |
| `cc_gen/writers/manifest.py` | imsmanifest.xml (CC 1.1) + `web_resources/` file resources. Resource order follows the generated-asset dict order, so **asset insertion order affects bytes**. |
| `cc_gen/assets.py` | **Toolkit**: `banner`, `placeholder_card`, `spinner_gif`, `scene_webp`, `pdf` (fpdf2; `cell_fill`, `author`), `docx`, `png`, `font`, `hex_rgb`, `mix`, `FIXED_DATE`. **`generate()`** runs in a fixed order: catalog images (fetched or placeholder) → masked images → `theme.assets()` → `<theme>/files/**` verbatim. |
| `cc_gen/images.py` | `fetch_all(entries, ImageSource, mode)`: cache → probe (fail fast offline) → threaded fetch → `normalize` (any image → PNG, optional `max_size`) → cache. Also mask utilities: `figure_mask` (alpha if the border is transparent, else white-border flood fill), `silhouette`, `pixelate`, `blur`, `mask(style)`, and `palette_png` (256-color FASTOCTREE + Floyd–Steinberg). |
| `cc_gen/validate.py` | Static validation (§7), the client hygiene check, and `forbidden_terms`. |
| `cc_gen/matrix.py` | `render()` (Markdown matrix doc), `signature()` (theme-independent), `compare()`. |
| `cc_gen/build.py` | Orchestration + reference checking. |
| `cc_gen/cli.py` | `cc` entry point. |
| `seeder/` | `seed.py` (reads the theme's `roster.yaml` and `theme.yaml` `seeder` keys), `canvas_api.py`, `config.example.toml`. |
| `tests/conftest.py` | Fixtures parametrized over **every** theme in `themes/`. Defines `REFERENCE_THEME = "pokemon"`. |
| `tests/test_contract.py` | The contract. Finds items **by structure** (identify bank, practice quiz, marathon, …), never by theme keys. Every new theme gets these checks automatically. |
| `tests/test_images.py`, `tests/test_units.py` | Image pipeline (fetch is faked), dates, formulas, namespaces, signature/compare, forbidden terms. |
| `tests/test_pokemon.py` | Pokémon-only checks + **golden digest**. |

## 5. Canvas format knowledge (hard-won; verified against canvas-lms source)

Source of truth: `instructure/canvas-lms`:
- `lib/cc/*.rb` (exporter)
- `lib/cc/importer/canvas/*.rb`
- `gems/plugins/qti_exporter/lib/qti/*.rb`
- `app/models/importers/*`

When unsure, read the exporter and emit exactly what it emits.

- **Canvas-flavor detection.** The manifest must contain a resource with `href="course_settings/canvas_export.txt"`. Without it Canvas treats the package as plain CC and drops most settings.
- **Resource shapes:**
  - **Pages:** `webcontent` at `wiki_content/<slug>.html`. The filename becomes the page URL. `<meta>` tags carry identifier, workflow_state, front_page, editing_roles, todo_date, publish_at, unlock_at and lock_at; Canvas skips meta whose value is false or empty.
  - **Assignments:** LOR resource, `<mid>/<slug>.html` + `<mid>/assignment_settings.xml`.
  - **Discussions:** `imsdt_xmlv1p1` topic + LOR `topicMeta`. Graded discussions embed `<assignment>` in topicMeta (`submission_types=discussion_topic`). Announcements are topicMeta `type=announcement`.
  - **Quizzes:** three files:
    - `<mid>/assessment_qti.xml`: CC copy, **5 CC types only** (MC, MA, TF, short answer, essay).
    - `non_cc_assessments/<mid>.xml.qti`: full fidelity, used by the importer.
    - `<mid>/assessment_meta.xml`: includes `<assignment>` for graded quizzes and graded surveys.
  - **Banks:** `non_cc_assessments/<bank>.xml.qti` `<objectbank>`. Groups reference them via `<sourcebank_ref>`.
- **Link placeholders:**
  - `$WIKI_REFERENCE$/pages/<page mid>`
  - `$CANVAS_OBJECT_REFERENCE$/{assignments,quizzes,discussion_topics}/<mid>`
  - `$CANVAS_COURSE_REFERENCE$/modules/items/<item mid>`, `$CANVAS_COURSE_REFERENCE$/<anything>`, `.../external_tools/retrieve?url=`
  - `$IMS-CC-FILEBASE$/<url-quoted path>`
  - All of them work inside QTI question text and feedback too.
- **Module items:**
  - `ContextExternalTool` items swap ids. In the organization, the item identifier is `<id>_module_item` with identifierref = item id. In module_meta, identifierref = the tool's mid.
  - Sub-headers have no identifierref.
  - External URLs get an `imswl_xmlv1p1` resource.
- **Completion requirements:** must_view, must_submit, must_contribute, must_mark_done, min_score (float), min_percentage (**integer**: the XSD rejects `70.0`).
- **QTI quirks:**
  - Answer ids must be numeric.
  - Fill-in-multiple-blanks: every `response_label` under a blank is imported as correct, with one respcondition per blank.
  - Multiple dropdowns take their weight from the respcondition.
  - Surveys have no correct answer, so the MC respcondition is skipped.
  - Numerical answers come in exact+margin / range / precision forms (exporter math).
  - Calculated questions use `itemproc_extension` (vars, formulas, var_sets); answers come from a safe AST evaluator.
  - `assessment_question_identifierref` is **omitted** on inline questions.
  - General feedback = `<other/>` respcondition `continue="Yes"` → `displayfeedback general_fb`.
  - HTML feedback = `mattext texttype="text/html"`.
- **files_meta:** folder `path` is relative to `web_resources/`. Locked files also get LOM `intendedEndUserRole=Instructor` in the manifest.
- **Events:** recurring = N events sharing `series_uuid` + `rrule`, with `series_head` on the first (as Canvas exports them).
- **Course tabs** (`tab_configuration` ids): home 0, syllabus 1, pages 2, assignments 3, quizzes 4, grades 5, people 6, groups 7, discussions 8, modules 10, files 11, conferences 12, settings 13, announcements 14, outcomes 15, collaborations 16, rubrics 18. LTI tabs are `context_external_tool_<tool mid>`.
- **Groups:** `has_group_category` + `group_category` (a *name*) makes the import create an **empty** group set. The seeder fills it.
- **Not representable in a cartridge** (seeder or manual): sections, users, enrollments, group membership, overrides, submissions, grades, peer-review pairings, moderation graders, Canvas media objects / media_tracks (captions ship as plain .vtt + `<track>`), developer keys.
- **LTI 1.3:** tool XML carries `client_id`. The developer key must exist and be enabled on the instance, or that tool errors on import. LTI 1.1 secrets are not exported.
- **Stale XSD:** Canvas's `cccv1p0.xsd` rejects `category`, `points_based`, `late_policy` and topic `locked`, which Canvas's own exporter writes. Real exports fail it too. `validate.STALE_XSD_ELEMENTS` downgrades these to a note; the rest of the XSD is enforced.
- **Deliberately not generated:** Mastery Paths, discussion checkpoints, course pacing. Their formats are undocumented or feature-flagged. To add one: build it by hand in Canvas, export, copy the shape.

## 6. THE TEST MATRIX CONTRACT (what every theme must reproduce)

A theme is "identical" when its **signature** equals the reference theme's:

```bash
uv run cc matrix --theme NEW --compare pokemon --ignore-tests
```

`tests/test_contract.py::test_same_matrix_as_reference_theme` runs this automatically for every theme.

**What the signature contains** (`cc_gen/matrix.py`):
- Per module: position, published, `unlock_at` spec, prerequisites (as positions), sequential progress, `requires_lti`.
- Per item: content type, indent, `new_tab`, published, requirement + threshold.
- Every Canvas setting on the item's object, with date **specs** compared verbatim. Keys into other registries (group, rubric, grading_standard, group_category, bank, file paths) are reduced to `<ref>`.
- Quiz question shapes: type, points, answer count, feedback kinds, group pick/bank, and `generated: identify`.
- Course-level: settings, flags, tabs, assignment groups and rules, grading schemes (size, points-based), rubrics (criteria points/ratings/flags), outcomes, events, announcements, banks, files meta and folders, pages outside modules.
- Catalog: size (≥386 counts as equal) and groups.

**What it drops:** all titles, bodies, descriptions, messages, question/answer text, keys, URLs and file names. With `--ignore-tests` it also drops the `tests:` annotations, so a theme may reword them. Without the flag they must match word for word. Some Pokémon `tests:` strings mention "Gen I" and "386"; that's fine with `--ignore-tests`.

**Consequence:** copy the Pokémon theme's YAML **structure** and keep every setting key and value. Change only text, titles, keys, data and images. Date specs must be identical. Module file order must be identical.

`docs/TEST_MATRIX.md` is the human-readable per-item matrix for Pokémon. A new theme's doc goes to `docs/test-matrix/<name>.md` (theme.yaml `matrix_doc`).

### Module skeleton (positions matter: prerequisites must point backwards)

| # | Role | Required settings |
|---|---|---|
| 00 | Orientation | Published. Front page. must_view ×3, must_contribute ×1. Sub-headers, indents. Its key = theme.yaml `seeder.orientation_module`. |
| 01 | Unit 1 | Prerequisite = 00. Every requirement type except min_percentage. One of every basic submission type. |
| 02 | Unit 2 | Prerequisite = 01, `require_sequential_progress`. Every grading type, groups, peer review, banks, surveys, min_percentage. |
| 03 | Unit 3 | Prerequisite = 02, `unlock_at: "+14d@08:00"`. Advanced grading workflows, date and content edge cases. |
| 90 | LTI Sandbox | `requires_lti: true` |
| 91 | Preview | **Unpublished** module with unpublished page, assignment and quiz. |
| 92 | Empty | Published, `items: []`. |

### Required items (by feature)

**Pages**
- **Front page:** rich HTML, links of every helper type, a module-item link, course links.
- **"How this course works":** renders groups and schemes from `course`.
- **Media page:** `<video>` + 2 `<track>` VTT, `<audio>`, a forced-download link.
- **Kitchen sink:**
  - h4–h6, inline formatting, nested lists (`ol type/start`, `dl`).
  - Table with caption, scope, colspan and rowspan.
  - Blockquote, pre/code, `<details>`, Canvas equation image + MathML.
  - SVG, GIF, WebP, an external hotlinked image, and a decorative `alt=""` image.
  - External iframe + same-origin page iframe.
  - ja/ko/zh/ar/he text, emoji, diacritics, a long unbroken string, escaped `<script>`.
- **Large generated pages:** one table with a row (and image) per catalog entry (≥386); a group-1 table (~151 rows); a 17×17 generated grid; a 30-image gallery page.
- **`must_mark_done` page.**
- **`todo_date` page.**
- **Student-editable page** (`editing_roles: "teachers,students"`).
- **Edge-case titles:**
  - unicode/CJK/emoji + `&`
  - escaping torture (`< > & " ' \` ${} {{}}` with an explicit `url:`)
  - **exactly 255 characters**
  - RTL title + RTL body
- **Other page states:**
  - `publish_at` scheduled (unpublished)
  - unpublished page in a published module
  - orphan page (published, in no module)
  - unpublished Developer Notes page that renders the matrix from `course`

**Assignments**
- **Submission types:**
  - `online_text_entry` + rubric used for grading
  - `online_upload` + `allowed_extensions`
  - `online_url` (past due)
  - `media_recording`
  - `student_annotation` + `annotatable_file`
  - four types at once
  - `on_paper`
  - `none` + `not_graded` with no due date
  - `external_tool` (LTI module only)
- **Grading types:**
  - `letter_grade` with a scheme
  - `gpa_scale` with a points-based scheme
  - `percent`
  - `pass_fail`
  - `not_graded` with submissions
  - extra credit (0 points in the 5% group)
- **Groups and peers:**
  - group assignment graded per group
  - group assignment with `grade_group_students_individually`
  - automatic + anonymous peer review: count 2, `peer_reviews_due`, rubric
  - group discussion
- **Workflows:**
  - moderated: `grader_count` 2, graders anonymous to each other, a rubric with `hide_score_total`
  - anonymous grading
  - `post_manually`
  - `allowed_attempts: 3`
  - `omit_from_final_grade`
  - `only_visible_to_overrides`, also on the `never_drop` list, with a seeder section override
  - an unpublished assignment
- **Dates:**
  - past due 7d; past due **and** past lock
  - due today 23:59; due +3d
  - `dst_next@01:30`
  - `all_day`
  - far future (+110d)
  - no due date
  - unlock < due < lock window

**Discussions**
- Threaded, ungraded, pinned, with ratings (orientation; key = theme.yaml `seeder.intro_discussion`).
- Graded with `require_initial_post` + rubric (free-form comments + `ignore_for_scoring`).
- Flat with `lock_at` in the past.
- Side-comment with `only_graders_can_rate` + `todo_date`.

**Announcements:** posted, `locked`, and `delayed_post_at` in the future.

**Quizzes (Classic)**
- **Unit 1 graded quiz:**
  - the 5 CC-portable types, answer + question feedback
  - an image question using `masked(entry(...))`
  - a min_score requirement
  - a bonus bank draw
- **Practice quiz:**
  - unlimited attempts
  - `{generated: identify, count: 3, catalog_group: 1}` + a bank draw of 3
- **Unit 2 quiz:**
  - question types: text-only, fill-in-multiple-blanks, dropdowns, numerical in all 4 answer forms, calculated with 4 vars, essay, file upload
  - groups: a bank group, an inline group, a second bank draw
  - settings: time limit, unlock/lock, `one_question_at_a_time` + `cant_go_back`, `show_correct_answers: false`
- **Surveys:** a graded survey; an anonymous ungraded survey.
- **Final exam:**
  - every question type once
  - `access_code`, `show_correct_answers_at`/`hide_correct_answers_at`
  - 60-min limit
  - 3 bank draws
- **Bank-only quizzes:**
  - **Lightning:** 10 draws, 3-min limit, `keep_average`, `hide_results: until_after_last_attempt`, **min_percentage 70**
  - **Marathon:** 50 draws at 0.1 pts, an extra-credit group, `keep_latest`, `one_time_results`
  - **Unpublished:** `hide_results: always`
- **Coverage across quizzes:** all 3 scoring policies, both `hide_results` values, attempts {-1, 1, 2, 3}.

**Question banks**
- One themed 6-question bank.
- **The identify bank:** `{generated: identify, all: true}`, one masked-image question per catalog entry.
  - used by ≥7 quizzes
  - ≥3 quizzes built only from bank draws

**Course level**
- Weighted groups (`percent`) with drop_lowest, plus `never_drop`.
- 3 grading schemes (letter, A–F, points-based GPA).
- Late policy (10%/day, floor 50%).
- 4 outcomes, one per calculation method (decaying_average, highest, n_mastery, latest).
- 3 rubrics:
  - outcome-aligned + range criterion
  - free-form + `ignore_for_scoring`
  - `hide_score_total`
- Events: 6-week recurring, all-day, multi-day, DST-spanning.
- Reordered tabs, with outcomes/rubrics/collaborations/conferences hidden.
- Course image + banner.
- Syllabus with `syllabus_course_summary`.
- `time_zone: America/Denver`.
- Relative course start and end.

**Files**
- Hidden + locked folder holding a locked + hidden file.
- A folder with `unlock_at`.
- Files with usage rights, with `display_name` ≠ filename, and with `lock_at`.
- **A unicode filename with spaces and an en dash.**
- Formats:
  - PDF (landscape table + multi-page annotatable), DOCX
  - CSV (small + 10k rows), JSON, .py
  - PNG, GIF (animated), WebP, SVG
  - MP4, MP3, VTT ×2
- Hundreds of catalog images in nested folders.

**External URLs:** `new_tab` true and false; one percent-encoded.

**LTI module**
- Inline + new-tab tool items.
- `external_tool` assignment with a line_item.
- A pass/fail new-tab tool assignment.
- An RCE iframe embed + link via `external_tools/retrieve`.
- A course-nav tab.

**Roster (`roster.yaml`; same shape in every theme)**
- **Sections:** 3, one with its own dates.
- **Staff:** teacher, a section-limited TA, a second teacher, a designer, an observer of a student.
- **Students:** 12, including two-section, inactive, concluded, unicode+emoji name and very long name.
- **Groups:**
  - a 3-group set filling the set the cartridge creates (same `group_category` name as the assignments)
  - a self-signup set with 4 empty groups
- **Overrides:** section, section-only visibility, student extension, group.
- **Orientation:** completion for 7 students, plus intro posts.
- **Submissions:** text, URL, file (must reference an existing course file).
- **Grades:**
  - rubric assessments (criteria by `description`)
  - posted grades for every grading type
  - an excused submission, a hidden grade, a final grader

## 7. Validation (`cc validate`, run by `cc build`)

- **Well-formed XML.**
- **cccv1p0 XSD**, minus the stale elements in §5.
- **Manifest:**
  - `schema` is IMS Common Cartridge
  - the canvas_export.txt marker is present
  - hrefs and files exist; ids are unique; dependencies and organization refs resolve
- **module_meta:** identifierrefs, prerequisites and requirement refs resolve.
- **Placeholders:** every placeholder in html/xml/qti resolves.
- **Unreferenced files** are warnings. Tests require zero warnings.
- **`--hygiene-against REF.imscc`:** greps the package for distinctive strings from a reference export (the client data rule).
- **`--forbid-theme NAME`** (repeatable):
  - What it matches: that theme's `brand_terms` + catalog names, minus its `common_words`, as whole words, case-insensitively.
  - Where it looks: text files and file names.
  - Exceptions: the checked theme's own `allow_terms`.
  - **Limitation:** it can't see inside PDF/DOCX/PNG bytes. Review generated PDFs and DOCX by eye.

## 8. Theme system reference

### Layout

```
themes/<name>/
  theme.yaml        required: config (below)
  theme.py          optional: code hooks (below). Loaded by file path; may `from cc_gen.assets import ...`
  course.yaml       settings, tabs, groups, schemes, outcomes, rubrics, events, announcements, files/folders meta, orphan pages
  modules/NN-*.yaml modules with inline items (filename order = module order)
  pages/*.html      Jinja bodies
  quizzes/*.yaml    quizzes and banks (`bank: true`)
  data/             catalog CSV etc.
  files/            copied verbatim into course files (media clips, captions...)
  lti.example.yaml  LTI config for `cc matrix` + the LTI test build (copy to lti.yaml for real use; gitignored)
  roster.yaml       seeder data
```

### theme.yaml keys

| Key | Meaning |
|---|---|
| `name` | Directory name and `--theme` value (required). |
| `title` | Display only. |
| `namespace` | Migration-id hash namespace (required, unique, **never change**). Pokémon = `pta`. |
| `output` | dist filename (default `<name>.imscc`). |
| `matrix_doc` | Where `cc matrix` writes (default `docs/test-matrix/<name>.md`). |
| `commercial` | `true` turns on the forbid-other-themes test. |
| `brand_terms` | Strings that identify **this** theme (checked when another theme forbids it). |
| `common_words` | This theme's catalog names that are ordinary words (excluded from forbid checks). |
| `allow_terms` | Terms **this** theme may use even if another theme's list contains them. |
| `catalog.file` | CSV, relative to the theme (or a `load_catalog` hook). |
| `catalog.id` | Column holding the id. Prefer ints; ids also name cache files (`zfill(3)`) and seed the masked filename hash `md5("mystery:{id}")`. |
| `catalog.name` | Column holding the display name. |
| `catalog.group` | Column holding the unit number (1/2/3), used by `catalog_group(n)` and `catalog_group:` in generated questions. |
| `catalog.int_fields` | Columns converted to int (before `id`/`group` are read). |
| `catalog.image` | `str.format` template for each entry's course-files image path (may use `slug` and hook-added fields). |
| `images.source` | `{url, cache, label, user_agent, max_size}`. `url` is a `str.format` template over entry fields (e.g. `"{image_url}"` for a per-row CSV column). Omit it for placeholder-only. |
| `images.alt_fetched` / `alt_placeholder` | Alt text templates for `entry_image`. |
| `images.mask` | `silhouette` (needs a transparent or plain white background) \| `pixelate` \| `blur` (for paintings/photos). |
| `images.masked_dir` | Folder for masked images (filenames are hashes, so they can't reveal the answer). |
| `images.masked_alt_fetched` / `masked_alt_placeholder` | Name-free alt text. |
| `images.feedback_width` | Width of the unmasked image in the auto feedback. |
| `identify.prompt` | Question prompt (literal HTML-safe text). |
| `identify.question_name` / `correct` / `incorrect` | `str.format` templates over entry fields. |
| `identify.choices` / `points` | Generated question settings (defaults: 4, 1). Keep them at 4 and 1 for the same matrix. |
| `seeder.sis_prefix` / `email_domain` | Seeded SIS ids `<prefix>-*`; must differ from other themes. |
| `seeder.orientation_module` / `intro_discussion` | Content keys the seeder completes and posts to. |
| `cli_flags` | Optional shortcut flags meaning `--theme <name>` on every command (e.g. `["-p", "-P", "--pokemon"]`). Mutually exclusive with `--theme`; must be unique across themes. |

### Catalog entry fields

Each catalog entry is a dict with every CSV column plus:
- `id`, `name`, `group` (int), `slug` (`slugify(name)`)
- `image`
- any fields added by `enrich_entry`

### theme.py hooks (all optional; see `cc_gen.theme.HOOKS`)

| Hook | Signature | Default if absent |
|---|---|---|
| `load_catalog` | `(theme) -> list[dict]` | Read `catalog.file` CSV. |
| `enrich_entry` | `(entry) -> None` | Runs before `catalog.image` is formatted. |
| `placeholder_image` | `(entry, hide_name) -> PNG` | `assets.placeholder_card`. |
| `mask_image` | `(png, entry) -> PNG` | `images.mask(style)`. |
| `jinja_globals` | `(course, renderer) -> dict` | Extra template helpers/data. Use `renderer.img(path, alt, width)` so refs are tracked. |
| `assets` | `(course) -> {path: bytes}` | **Must produce the contract's file formats and edge-case names** (§6 Files). Insertion order is part of the output bytes. |

### Template globals (all themes)

- **Links:** `page`, `assignment`, `quiz`, `discussion`, `file(path, download=False)`, `course_link`, `module_item(module_key, item_key)`.
- **Anchors:** `a_page`, `a_assignment`, `a_quiz`, `a_discussion`, `a_file`.
- **Images:** `img(path, alt, width)`.
- **Catalog:**
  - `catalog`, `catalog_group(n)`, `entry(name)`, `fetched_count()`
  - `entry_image(e, width=96)`
  - `masked(e, width=120)`: the hashed path; also triggers the feedback hook.
- **Other:** `date(spec, fmt)`, `settings`, `lti`, `escape`, `course`.
- **Pokémon only:** `type_badge`, `TYPES`, `TYPE_COLORS`, `effectiveness`.

### Generated questions

```yaml
- {type: generated, generator: identify, count: 3, catalog_group: 1}   # or all: true
```

These expand at load using `random.Random(f"{course.seed}:{quiz_key}:{index}")`. Each picked entry becomes a multiple-choice question: the masked image + `choices` names, tagged `generated: identify`. The tests use that tag to find the identify bank and practice quiz structurally.

### Recipe: a new theme with the same matrix

1. `cp -r themes/pokemon themes/<new>`. Delete `theme.py`'s Pokémon recipes and `data/pokedex_1_386.csv`, but keep `files/media/*` (generic clips) or replace them with equivalents.
2. Edit `theme.yaml`: set a new `name`, `namespace`, `output`, `title`, `commercial`, `brand_terms`, catalog spec, `images` (source + `mask` style), `identify` strings and `seeder` values.
3. Catalog:
   - **≥386 entries** across groups 1/2/3.
   - Group 1 needs ≥3 entries, and group 3 needs ≥30 (gallery page).
   - The marathon needs ≥50 in total.
   - Include licence/attribution columns for PD sources.
4. Rewrite content **in place**:
   - In every YAML file, change only `title`, `body`, `description`, `message`, question/answer text and keys. Never change settings, dates or structure.
   - If you rename keys, update every reference: `a_page('key')`, `prerequisites`, `group:`, `rubric:`, `never_drop`, quiz files, roster.
   - The 255-character title must stay exactly 255. Keep the escaping, CJK and RTL edge cases.
5. Write `theme.py` `assets()` producing the same formats and edge cases:
   - hidden/locked folder file
   - the unicode + en-dash + space filename
   - 10k-row CSV, JSON, .py, landscape table PDF, annotatable multi-page PDF, DOCX, animated GIF, WebP, SVG
   - course card + banner
   Keep `course.yaml` `files:`/`folders:` meta pointing at your paths.
6. Write `roster.yaml` with the same shape (§6). Its submission `file:` must exist, and its group-set name must match the cartridge's `group_category`.
7. Verify:
   - `uv run cc build --theme <new> --images generated`
   - `uv run cc matrix --theme <new> --compare pokemon --ignore-tests` (must print "identical")
   - `uv run cc validate --theme <new> --forbid-theme pokemon --hygiene-against examples/OTC_PROPRIETARY_EXPORT.imscc`
   - `uv run pytest -q` (the contract tests now run for both themes; the golden Pokémon test must stay green)
   - `uv run cc matrix --theme <new>` for its doc.
8. Open the generated PDFs/DOCX and check they contain no old-theme text; the forbid check can't see inside them.

## 9. Commercial ("public domain") theme cautions

- **No Pokémon material at all:** no names, artwork, data CSV, pokemon.com URL, type chart or gym-leader data. Write the theme's own `theme.py`; don't import from `themes/pokemon/theme.py`.
- **Images need verifiable PD/CC0 status per file.** Good sources include museum open-access APIs (e.g. the Met, Art Institute of Chicago, Rijksmuseum, Smithsonian CC0), plus Wikimedia Commons files whose licence is explicitly PD/CC0, checked per file.
  - Record source URL + licence per catalog row, and surface it inside existing items (e.g. columns in the catalog table page and the shipped CSV). Don't add new items; that would change the matrix.
  - Set a descriptive `user_agent` and respect API terms. Use `max_size` (public-domain uses 256: 360 gave 13.6 MB, 256 gives 7.9 MB) to keep the package small. Cached images are re-normalized to the current `max_size` on read, but after lowering it run `--refresh-images` once so the cache matches a fresh download byte-for-byte.
  - The fetcher converts JPEG/WebP to PNG.
- **Masking:** `silhouette` only works on transparent or plain-white backgrounds. Paintings and photos need `mask: pixelate` or `blur`, or a `mask_image` hook.
- **Placeholder art and generated assets** must be original. The toolkit's banners, cards, spinner and scene are generic shapes.
- **Fonts:** fpdf2's core fonts are Latin-1 only, so keep PDF text ASCII-ish (the Pokémon theme writes "Pokemon", "Okido").
- **The public-domain theme's workflow** (the worked example of all of the above):
  - `themes/public-domain/tools/curate_catalog.py` (one-time, networked, not part of the build) queries the AIC and Met APIs for PD-flagged works, filters (unique ≤60-char titles, no generic titles, no demo-unsuitable subjects, nothing matching the pokemon theme's terms) and writes `data/collection.csv` + `data/provenance.jsonl`. Never store AIC's `description` field (CC-BY). The Met's old `/v1/search` is retired; use `/v1.1/search` (paginated) and back off on 403s.
  - Media and other non-generated assets are documented in `themes/public-domain/PROVENANCE.md`. Keep it current when you replace an asset.
  - `tests/test_public_domain.py` asserts the licence evidence (CC0 on every row, PD flags in provenance, no `description` keys).

## 10. Known gaps / verify on first live import

- **Never verified live:** the whole import, the seeder, and every endpoint and param in `seed.py`. Expect small fixes. Endpoints used:
  - content_migrations + `pre_attachment`
  - `accounts/:id/users` + `pseudonym[...]`
  - `sections/:id/enrollments` (+ DELETE `task=deactivate|conclude`)
  - group_categories / groups / memberships, assignment overrides
  - module item `mark_read`/`done`, discussion entries
  - submissions with `as_user_id`, file upload preflight
  - grade PUT with `rubric_assessment[criterion_id]`, `assignment[final_grader_id]`
  - `external_tools` with `client_id`
- **Check migration issues for:**
  - `student_annotation`, recurring events, formula/numerical questions, min_percentage
  - `--new-quizzes` conversion
  - whether Marathon's general feedback shows with `show_correct_answers: false`
  - sanitizer-stripped HTML (`<video>`/`<track>`, MathML, iframes)
- **Seeder scope:** it only submits Unit 1 work, because later units are locked by prerequisites. Taking quizzes via the API, peer-review pairings and provisional grades are not seeded.
- **New Quizzes:** with `--new-quizzes`, `cc verify` adjusts assignment counts, but quiz-specific checks are skipped.
- **Seed manifests** are per theme: `dist/seed-manifest-<theme>.json`.

## 11. Conventions

- **Code style:** match the surrounding code: compact, typed Python 3.12, minimal comments that explain *why*.
- **`cc_gen/` stays theme-agnostic:**
  - Theme strings go in theme.yaml, and theme code goes in theme.py hooks.
  - If a new theme needs a generic capability, add it to `cc_gen` behind a theme.yaml key whose default leaves Pokémon output unchanged (keep the golden test green).
- **Writers** mirror the Ruby exporter's element order and naming. Cite the Ruby file in a comment when copying a quirk.
- **New Canvas features:**
  1. Add the item to **every** theme with a `tests:` tag.
  2. Add a structural assertion in `tests/test_contract.py`.
  3. Regenerate the matrix docs.
  4. Update the Pokémon golden digest (intentional change).
- **Images:** write palette PNGs (`palette_png`) unless the asset is a smooth gradient (banners), which band at 256 colors.
- **Randomness:** always `random.Random(f"{course.seed}:{key}:{i}")`, never the global RNG.
- **Docs:** keep `README.md` user-facing and this file agent-facing.
