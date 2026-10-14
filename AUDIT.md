# Audit: known bugs, semantic risks, and data issues

Findings from a full review of the code and the local Parquet datasets.
Recorded 2026-08-04 against ICE Detention Pathway 2.0.0; updated 2026-08-05
against 2.0.0 and the national datasets; updated 2026-09-30 against the
2026-09-30 data release. Items marked FIXED were resolved in 2.0.0; see
README-v2.md. Unmarked items remain open.

Counts are from the **national** datasets now in place (the original NYC-only
arrests copy, 23,909 rows, was replaced): arrests 896,393 rows / 812,823
people · detention stints 3,202,317 rows / 1,174,420 people · detention stays
1,264,756 rows · facilities 877 rows. Data window: book-in 2021-10-01 →
2026-08-06, book-out 2022-10-01 → 2026-08-06. Items marked **(2025-08 era)**
were measured on the previous release: arrests 713,464 rows / 651,237 people ·
detention stints 2,617,844 rows / 1,014,866 people · detention stays 1,087,417
rows · facilities 844 rows, book-in 2004-12-05 → 2026-03-11. Items measured on
the filtered copy are marked **filtered-era**. Every count is reproducible with
`q.py` or `verify_bulk.py`.

Severity: **P1** wrong output a reader would trust · **P2** refuses or degrades
work it should handle · **P3** correctness risk under conditions not yet seen ·
**P4** housekeeping.

---

## A0. The 2026-09-30 release (new findings)

### A0.1 — Hard crash on the new arrests file · P1 · FIXED

The 2026-09-30 release renamed `apprehension_site_landmark` to `event_landmark`.
Every lookup against the new file died before returning a row:

```
Binder Error: Referenced column "apprehension_site_landmark" not found in FROM clause!
```

The same release dropped `arresting_agency`, `operation`, `toa_current_duty_site`,
`apprehension_type`, `book_in_aor`, `detainee_classification`, `race`, and
`birth_country` from the detention tables, and changed `initial_bond_set_amount`
from `double` to `string`.

**Fix:** schema discovery rather than hardcoded column names. Both the core and
the two verifiers now probe the file's columns and read each field through a
declared lineage (`ARREST_CITY_LINEAGE`, `ARREST_LANDMARK_LINEAGE`), so a
release that reshapes its tables no longer requires a code change.

### A0.2 — `unique_identifier` is not stable across releases · P1 · DOCUMENTED

The publisher re-salts identifiers on every rebuild. Between the 2025-08 and
2026-09 releases:

| Measure | Result |
|---|---|
| Old `unique_identifier` values found in the new release | **0 of 1,014,866** |
| Old `stay_ID` values found in the new release | **0 of 1,087,417** |
| Natural-key matches (`birth_year`, `gender`, `book_in_date_time`, `detention_facility_code`) | 3,171,995 |
| …of those, sharing the same `unique_identifier` | **0** |

The same people are present under different identifiers. **Consequence: an
identifier saved from an earlier export will not resolve after a data update,
and two exports cannot be diffed or linked by ID.** Within one release the IDs
are consistent — `detainers-latest.parquet` joins 325,571 of its people to
`detention-stints-latest.parquet` — so a single lookup is internally sound.

This contradicts any earlier reading of these IDs as durable person keys. There
is no publisher-provided crosswalk; a natural key is the only bridge, and it is
ambiguous where two people share a birth year, gender, and book-in minute.

### A0.3 — Placeholder landmarks reported as arrest locations · P1 · FIXED

`event_landmark` is a coverage statement, not an address. 292,790 rows
(32.66%) are placeholders such as `DALLAS COUNTY GENERAL AREA`,
`MTG GENERAL AREA, NON-SPECIFIC`. The old code took the landmark as the
location whenever it was non-empty, so a person's pathway opened with a
non-place. Landmark coverage also fell from 97.86% to 77.57% in this release.

**Fix:** placeholder patterns are excluded from the location expression, and
the raw value is quoted under its own field name on a `[arrest — ...]` line
instead. The location now prefers the publisher's normalized
`apprehension_city_common_case`, falls back to a real landmark, then state, then
AOR.

### A0.4 — New city field is manually entered and inconsistent · P1 · MITIGATED

`apprehension_city_original` is typed by hand: 18,512 distinct raw values
collapse to 11,262 after the publisher's own `apprehension_city_common_case`
normalization, which rewrites 197,588 of 785,235 populated values (25.16%).
Residual defects survive normalization — `CEDAR RA[IDS` becomes `Cedar Raids`.

**Mitigation:** the common-case field is used and the raw field is only a
fallback. It is not a solution: the normalized value is still sometimes wrong,
and a wrong city is worse than none. Geographic misattribution (county/town/
borough/parish for a city field) is small at 158 heuristic matches.

### A0.5 — `;` in three landmark values collides with the output grammar · P2 · FIXED

Field pairs on the `[arrest — ...]` line are separated by `; `. Three landmark
values contain `;` themselves (5 rows):

- `PRISON CAP PROGRAM; FEDERAL BOP PRISON CASES` (3)
- `PRISON CAP PROGRAM; OKLA DOC PRISON CASES` (1)
- `I-15, I-215 CORRIDOR; F/HWY 76, TO C. KEITH RD` (1)

Unsplit, a parser reads the second half as a new field. **Fix:** a value
containing `;` is emitted as one labelled pair per segment, reusing the
repeated-label convention the grammar already defines. `verify_bulk.py` C10 now
checks the rendered expressions rather than the source columns.

### A0.8 — National bulk verification on the 2026-09-30 release · PASS

`verify_bulk.py` over all 1,174,420 people / 1,264,756 stays / 3,191,399 stints
(790s), and again on the 2025-08 files to confirm no regression:

| | 2026-09-30 | 2025-08 (200k sample) |
|---|---|---|
| Paired stays | 673,477 | 40,214 |
| Unpaired stays | 591,279 | 42,965 |
| Unmatched arrests | 68,652 | 4,201 |
| C8 `n_stints` disagreements | 0 | 0 |
| C10 delimiter contract | pass | pass |
| Absent renderer fields | `book_in_aor`, `detainee_classification` | none |

C8 boundary reconciliation reports 33,038 stays whose published book-in
precedes the first stint row and 29,604 whose published book-out follows the
last. These come from the published table's wider nominal window, not pipeline
failures, and 0 stays disagree in the other direction. Open stays: 65,738,
stable in rate against 2025-08.

### A0.6 — Facility codes missing from the facilities table · P2 · OPEN

60 facility codes used by the new stints (3,825 stints) are absent from the
877-row facilities table, so those stints cannot be given a canonical name. The
2025-08 release had 4 such codes. The path still renders using the facility code
alone.

### A0.7 — `detainers-latest.parquet` can inflate a pathway if joined naively · P1 · NOT WIRED IN

The detainer file joins per-person within a release, and it still carries fields
the stints file just dropped (`case_status`, `conviction`, `detainer_type`,
`birth_country`, `msc_*`). 429,140 detainer rows belong to 325,570 people who
have stints, and **107,962 people have more than one detainer row** (up to
88,836), with 31,679 duplicated `(person, episode)` groups. A row-level join
would multiply stints and corrupt every stay boundary.

Not wired in. It is the most promising route to restoring the dropped case
fields, but only with episode-level deduplication.

### A0.9 — Release-crossing tools (`emit_moves.py`, `link_releases.py`) · 2026-10

Audited against the 2.1.1 code. Two P1 findings were demonstrated on synthetic
releases and fixed in 2.1.2; the rest remain open.

**FIXED in 2.1.2 — `emit_moves` reintroduced A0.3 and A0.4 (P1).** The MOVES
line re-implemented the arrest location with its own column picks, inverting
both lineages: it preferred the hand-typed `apprehension_city_original` /
`apprehension_state_original` over the publisher's normalized
`apprehension_city_common_case` / `apprehension_state_filled_in`, and used a
placeholder landmark as the location whenever no city was recorded. Fixed by
selecting the location through the core's `arrest_location_expr`, so a MOVES
line and a pathway text can no longer disagree. Regression tests:
`test_emit_location_prefers_the_normalized_city_and_state`,
`test_emit_location_excludes_placeholder_landmarks`.

**FIXED in 2.1.2 — crosswalk mislabelled `closed_and_extended` as `exact`
(P1).** `attach_pathways` re-derived the match shape from `stints_appended`,
losing the three-way classification `classify` had already computed. A final
stint that gained its book-out (path changed, no stints appended) was reported
as `exact` — the strongest claim a crosswalk makes. Fixed by emitting the
classifier's status verbatim. Regression test:
`test_closed_and_extended_is_reported_as_such`.

**OPEN — flagged duplicates are unmarked in MOVES segments (P2).** The core
renders `duplicate_drop_row` rows with a `[FLAGGED DUPLICATE ROW]` marker;
`emit_moves` renders the canonical facility name with no marker, so a
possibly-duplicate book-in/out reads as fact in the most reused output.

**OPEN — crosswalk fingerprints include flagged duplicates (P2).** Path
segments are built from every row. A later release that drops a duplicate the
earlier one kept changes the fingerprint shape and can turn a true match into
`not_found`/`ambiguous`. Excluding `duplicate_drop_row` rows consistently on
both sides would make matching robust to publisher-side dedup.

**OPEN — multiple arrests resolve silently to the last row (P2).** The arrest
lookup dict-comprehends an unordered select, so a person with several arrests
gets one arbitrary arrest stamp on their MOVES line. The core tracks every
arrest (`arrests_without_stay`). Needs a deterministic order and an
"additional arrests not shown" marker.

**OPEN — arrest-with-no-detention people are refused (P2).** `emit_moves`
exits `Not present in this release` for an identifier that has an arrest row
but no stints, where the core renders `[ARREST WITH NO RECORDED DETENTION]`
and `Record.moves_line` already has the `(NO DETENTION RECORD IN THIS DATASET)`
form. Only the absent-id guard blocks it; the two cases need separating.

**OPEN — MOVES output names no absent source fields (P3).** The core names
dropped fields via `missing_source_fields`; an empty `district-`/`age-` line
says nothing about whether the publisher stopped publishing the field or left
the person's value empty. The manifest could carry the absent-expected-columns
list.

**OPEN — crosswalk omits candidates for ambiguous entries (P3).** The
crosswalk file records `status: ambiguous` with a null new identifier, but the
candidate ids live only in the JSON bundle. Carrying them in the crosswalk
makes the durable artifact self-contained.

**OPEN — `any_value` absorbs intra-person demographic conflicts (P3).** The
people tables collapse `birth_year`/`gender` with `any_value`; a handful of
identifiers carry internal conflicts, and an arbitrary pick there can flip a
match verdict.

---

## A. Code bugs

### A1 — `verify_pathway.py` hard-failed on multiple arrests · P1 · FIXED in 2.0.0

`verify_pathway.py:200` keeps the `Expected exactly one arrest row` guard that
was removed from the core lookup in 2.0.0.

```
$ python verify_pathway.py 082832ed5c19ba0131e6e5c8824f1e54e3cae494
Verification failed: Expected exactly one arrest row, but found 2.
```

The core tool returns a full pathway for that identifier. The verifier — the
tool whose entire purpose is letting a skeptic confirm the output — refuses.
It fails on precisely the 465 people the fix was written to rescue.

**Fix:** replace its private query with a call into `fetch_pathway`, so one
code path produces both the pathway and the receipt.

### A2 — Two different arrest-pairing algorithms · P1 · FIXED in 2.0.0

`ice_detention_pathway.pair_arrests_with_stays` assigns each stay the **nearest
preceding** arrest. `verify_pathway.match_arrest_episode:288` assigns the
**earliest** arrest whose stay starts after it.

For anyone with more than one arrest these disagree, so the receipt can cite a
different arrest than the pathway it claims to verify. A verifier that can
contradict the thing it verifies is worse than no verifier.

Currently masked by A1: the verifier errors out before the divergence shows.
Fixing A1 alone would expose this.

**Fix:** delete `match_arrest_episode` as part of A1.

### A3 — Naive/aware datetime crash in `verify_pathway.time_window` · P3 · FIXED in 2.0.0

`verify_pathway.py:409` falls back to `datetime.now(tz=None)` — timezone-naive —
while every other datetime in the module is UTC-aware. Comparing them raises
`TypeError`.

Unreachable in this dataset (0 stints have a null `book_in_date_time`), so it
is latent, not live. National data may differ.

**Fix:** `datetime.now(timezone.utc)`.

### A4 — False pairings of an arrest to a stay it did not open · P1 · FIXED in 2.0.0

Two pairing shortcuts let an arrest claim a stay it cannot have opened. Both
are gone.

**The lone-pair shortcut.** `ice_detention_pathway.py` paired a lone arrest
with a lone stay unconditionally, skipping the time check every other pairing
enforces. A single arrest logged years after a single stay's book-in still
paired and printed a `DISCREPANCY` instead of the accurate unpaired stay plus
`[ARREST WITH NO RECORDED DETENTION]`. The shortcut was removed; every pairing
now requires the arrest to precede the stay's first book-in within
`ARREST_TO_BOOK_IN_TOLERANCE`.

**The stale-leftover claim.** `pair_arrests_with_stays` gave each stay the
latest unclaimed arrest before its book-in with no lower bound. Once an
earlier stay had claimed a newer arrest, the older leftover could be dumped on
a later, unrelated stay. The bulk verifier found 350 such regressions across
the national data — e.g. `06e8600b5b4549b98596cc6a5d2683d4df013a52`, where a
Feb 2024 arrest was claimed by a June 2025 stay. Claims must now be strictly
newer than the previous stay's claim, so an arrest that an earlier stay passed
over can never open a later stay.

Net effect on national data: 456 pairings removed (521,861 → 521,405), every
one a false pairing. Removed pairings render as `[ARREST WITH NO RECORDED
DETENTION]` plus an unpaired stay.

### A5 — `[STAY 1 of 1]` appears when an unmatched arrest exists · P4 · FIXED in 2.0.0

`ice_detention_pathway.py:481` uses the compact single-stay rendering only when
there are no unmatched arrests. A person with one stay plus a stray arrest gets
`[STAY 1 of 1]`, which reads oddly. Cosmetic.

### A6 — Stay metadata is taken from mismatched ends · P3 · FIXED in 2.0.0

`group_stays` reads `entry_program`/`entry_aor` from the **first** stint and
`release_reason` from the **last**. A stay transferred between AORs showed only
its opening AOR, and a reader could not tell that later stints disagreed. Now
both ends are visible:

- Every stay names its first stint's program and AOR:
  `[first stint — final_program: …; book_in_aor: …]`.
- When later stints disagree, the differing values are listed:
  `[stint fields — …]`.
- Each stay also renders the named fields of its last stint:
  `[last stint — classification; case_status; threat_level; final_order;
  final_order_date; departed; charge]`.

National counts: 10,869 stays carry program variance, 366,302 AOR variance.

---

## B. Semantic and presentation risks

### B1 — `entered via` overstates what `final_program` records · P1 · FIXED in 2.0.0

Output renders the detention row's `final_program` as
`(entered via Border Patrol, Houston Area of Responsibility)`.

`final_program` is the program of record for the case. It is **not** an
arresting-agency field, and nothing in the data confirms who apprehended the
person. `entered via` is narration added by this project.

Compounding it: the label sits in the arrest slot, so it reads as arrest
metadata, while both values come from `detention-stints-latest.parquet`. And
`final_program` **also exists in the arrests file** with a different vocabulary
(8 distinct values there, 19 in detention), so the name alone is ambiguous.

**Fix:** name the fields instead of narrating them — every stay now renders its
first stint's values as `[first stint — final_program: Border Patrol; book_in_aor: Houston Area of Responsibility]`.

### B2 — Program vocabulary is uncontrolled · P2

`final_program` in the detention file holds 19 distinct values including two
unmerged spellings of the same unit:

- `Homeland Security Investigations` — 48 rows
- `HSI Criminal Arrest Only` — 2 rows

Also present: `287G Program`, `Joint Terrorism Task Force`,
`Law Enforcement Area Response Units`, `Inspections - Land` / `- Air`,
`Mobile Criminal Alien Team`, `Non-User Fee Investigations`.

The tool passes the string through verbatim and merges nothing. That is the
correct default, but it means output cannot be grouped or counted by agency
without a mapping the project does not have. Sub-units and joint operations are
not modelled at all.

**Verified non-CBP passthrough** with: `e7ee5fb10b7ffec18207ce693bc775be9b9d49b4`
(HSI), `b8c73d9c604a1813eeb7d3aba6badb077087ee9c` (HSI second spelling),
`f87fcf51e73d1c6f2bacd867bfa948eee9358870` (Inspections - Land).

### B3 — A `stay_ID` or `stint_ID` input is silently widened · P2 · FIXED in 2.0.0

`normalize_identifier` truncates at the first underscore, so pasting a specific
`stay_ID` returned **every** stay for that person. Documented, but it meant a
user asking about one stay was answered about all of them — the opposite of the
scoping the 2.0.0 fix was about.

A suffixed input now scopes the pathway to the named stay. The suffix is
matched against the complete value (`base_YYYY-MM-DD HH:MM:SS` for a stay,
`base_..._code` for a stint); that stay renders in full and the person's other
stays collapse to one `[CONTEXT — another stay for this person: …]` line. The
CLI prints `Scoped to stay:` when a suffix matched.

---

## C. Data-quality issues in the source datasets

### C1 — Sub-day arrest/book-in inversions flood the DISCREPANCY label · P1 · FIXED in 2.0.0

Stays whose arrest is logged **after** their own first book-in, by under 12h:

| Window | Stays |
|---|---|
| under 12 hours | 7,821 |
| under 2 hours | 5,022 |
| under 15 minutes | 997 |

Roughly a third of all stays. Every one prints
`DISCREPANCY: arrest date is after first detention book-in`, with identical
wording to a genuine year-scale impossibility.

A 15-minute inversion is paperwork order, not impossible chronology. At this
volume the label stops carrying information.

**Options:** leave as-is (retain-and-label, but readers learn to ignore it);
suppress under a threshold (hides real cases near the line); or grade it —
`MINOR DISCREPANCY` under a threshold, `DISCREPANCY` above. Grading is
recommended: nothing is hidden and severity stays legible.

Live examples: `e7ee5fb10b7ffec18207ce693bc775be9b9d49b4` (92 min),
`f87fcf51e73d1c6f2bacd867bfa948eee9358870` (15 min).

### C2 — 204 overlapping stints inside a single stay · P2

204 stints begin before the previous stint in the same stay booked out — the
person appears held in two facilities simultaneously. Correctly flagged today as
`detention begins before previous book-out`. Recorded so the count is known.

### C3 — Facility codes missing from the facilities table · P2 · WORSENED

**2025-08 era:** 4 codes (`NYMARCC`, `NYMDSTC`, `NYGROVC`, `NYFISHC`) appeared
in stints but not in `facilities-latest.parquet` (844 rows).

**2026-09:** 60 codes used by the new stints are absent from the 877-row
facilities table, affecting 3,825 stints. The join falls back to
`detention_facility`, so output degrades to the raw uppercase name rather than
the canonical one. No failure, no warning. The gap grew roughly 15× in stints
affected, which is worth raising with the publisher.

### C4 — Rows flagged `duplicate_drop_row` are labelled, not excluded · P2 · FIXED in 2.0.0

DDP flags 45,869 detention rows (2025-08 era; 46,430 in 2026-09, national) as
duplicates and excludes them from
its own `n_stints`. This project reads every row and appends
`[FLAGGED DUPLICATE ROW]` to the facility name, so stint counts here can be
reconciled against DDP's published figures instead of silently diverging. The
bulk verifier confirms every stay's non-duplicate stint count equals DDP's
`n_stints` (C8).

### C5 — 82,863 stints have no `final_program` (national) · P2

`final_program` is null in 82,863 stints, so the stay's `[first stint — …]`
line omits the program. Output renders the timeline normally and omits the
field. When such a stay also has no arrest and its last stint's summary fields
are all null, the output is a bare `NO ARREST RECORD IN THIS DATASET` timeline
with no further context — the silent path through B1. Correct behaviour;
recorded because the silence is undetectable by a reader.

### C6 — Open stints and stays · P3

61,888 stints (2025-08 era) have no book-out; 61,594 stays have at least one open
stint. **2026-09: 65,894 stints open, 65,741 stays affected** — the rate is
essentially unchanged despite 22% more stints, so this is a stable property of
the source rather than a growing gap. In a small number of stays the open stint
is followed by a later one, so
`Stay.end` returns `None` and the stay reads as ongoing even though a later
stint closed. Rare, and arguably the honest reading, but it means "currently
held" can appear on a stay with a subsequent completed placement.

### C7 — Local data was filtered and could not say so · P1 · FIXED in 2.0.0

The original local copy held 23,909 arrests, 100% `New York City Area of
Responsibility`, 100% `arresting_agency = ICE`; anyone arrested outside NYC AOR
was unfindable. All four files have been re-downloaded from the Deportation
Data Project and the national arrests are in place (713,464 rows across 26
AORs plus `unknown` and `HQ`). The scope of the local copy is printed by
`verify_pathway.py` and `verify_bulk.py` so a reader can always see what is
(and is not) in the copy they hold.

---

## E. Bulk verification (`verify_bulk.py`)

`verify_bulk.py` applies the *same* grouping and pairing the CLI uses to every
identifier in one streaming pass, then checks invariants across all rows
(C1–C10). Full national run, 2026-08-05:

```text
people: 1,014,866   stays: 1,087,417   stints grouped: 2,610,503
paired stays: 521,405   unpaired stays: 566,012   unmatched arrests: 54,733
stays with varying program: 10,869   varying AOR: 366,302
rows with null identifier (excluded): 0
rendering digest (sha256): 7e9e999b24c8f9d80839eae6209a88dc3b2cef601fc93008ac4305a7c1ec70bb
PASS: every check succeeded across the local datasets.
```

Highlights:

- **C1/C2/C5** — every stint is grouped into exactly one stay; stays are
  ordered oldest first; every pathway renders and rendering is deterministic
  (re-runs reproduce the digest above).
- **C3/C4** — every paired arrest precedes its stay's first book-in within the
  tolerance, and claim times never decrease across a person's stays.
- **C6** — a `stay_ID` or `stint_ID` suffix always resolves back to a stay (B3
  scoping).
- **C7** — 1,500 identifiers drawn from the multi-stay, former-shortcut, and
  random populations agree between the bulk path and the per-identifier
  `fetch_pathway` path.
- **C8** — every stay's non-duplicate stint count equals the published
  `n_stints` for all 1,087,417 stays.
- **C9** — the coverage window and the 7,341 unreachable rows are reported.
- **C10** — the delimiter contract for the machine-readable format is verified
  against the live data: no facility name/code carries a bracket or colon, no
  rendered stint field carries a semicolon, no release reason carries a
  semicolon, no arrest-location field carries an arrow or bracket, and no
  rendered value contains a newline. A future DDP data refresh that would
  silently break the documented format fails verification instead.

Data anomalies the verifier reports (source-data, not pipeline failures):

- The published stays table carries a wider nominal window than the stint rows:
  31,618 stays publish a book-in earlier than their first stint row (never
  later), 26,148 publish a book-out later than their last stint row (never
  earlier), 441 stays are closed in the stints but publish no book-out, and 1
  open stay publishes a book-out.
- 9 stints have a `stint_ID` the source places under more than one stay; they
  resolve to the earliest containing stay.

---

## D. Housekeeping

### D1 — `verify_pathway.py` and `q.py` are not in `pyproject.toml` · P4 · FIXED in 2.0.0

`[tool.setuptools] py-modules` lists three modules; neither new tool is
included, so neither ships with an install.

### D2 — Terminology drift · P4 · FIXED in 2.0.0

The core calls them **stays**; `verify_pathway.py` calls them **episodes**.
Same concept, two names, one repo.

### D3 — `q.py` is an ad-hoc helper · P4 · FIXED in 2.0.0

Written for this investigation. Either document it in the README or delete it;
right now it is untracked and unexplained.

---

## Reproducing the counts

`verify_bulk.py` is the authority for anything marked reproducible below. It runs
the real grouping and pairing code over the whole national dataset and prints the
C1–C10 results plus the data window and the absent-field list:

```bash
python verify_bulk.py                       # full pass + 1500-id cross-check
python verify_bulk.py --skip-sample         # bulk checks only, faster
python verify_bulk.py --limit 300000        # dev smoke run
```

Cross-release comparisons (A0.2) need both releases' files side by side, since the
identifiers do not carry across:

```bash
# identifier instability
python - <<'PY'
import duckdb
c = duckdb.connect()
OLD, NEW = "old/", "new/"   # paths to the two arrests/detention copies
print(c.execute(f"""
  SELECT count(*) FROM (
    SELECT unique_identifier FROM read_parquet('{OLD}detention-stints-latest.parquet')
    INTERSECT
    SELECT unique_identifier FROM read_parquet('{NEW}detention-stints-latest.parquet'))
""").fetchone())
PY
```

`q.py` remains available for ad-hoc counts:

```bash
python q.py "SELECT final_program, count(*) FROM 'detention-stints-latest.parquet' GROUP BY 1 ORDER BY 2 DESC"
python q.py "SELECT count(*) FROM 'detention-stints-latest.parquet' WHERE duplicate_drop_row"
python q.py "SELECT apprehension_aor, count(*) FROM 'arrests-latest.parquet' GROUP BY 1 ORDER BY 2 DESC"
```

Note that `q.py` queries do not probe the schema, so a query naming a dropped
column (`arresting_agency`, `book_in_aor`, `apprehension_site_landmark`) now
fails. Use the current release's column names, or
`DESCRIBE SELECT * FROM read_parquet(...)` first.

Full queries for C1, C2, and C3 are in the session that produced this file.
