# Audit: known bugs, semantic risks, and data issues

Findings from a full review of the code and the local Parquet datasets.
Recorded 2026-08-04 against ICE Detention Pathway 2.0.0; updated 2026-08-05
against 2.0.0 and the national datasets. Items marked FIXED were resolved in
2.0.0; see README-v2.md. Unmarked items remain open.

Counts are from the **national** datasets now in place (the original NYC-only
arrests copy, 23,909 rows, was replaced): arrests 713,464 rows / 651,237
people · detention stints 2,617,844 rows / 1,014,866 people · detention stays
1,087,417 rows · facilities 844 rows. Data window: book-in 2004-12-05 →
2026-03-11, book-out 2022-10-01 → 2026-03-11. Items whose counts were measured
on the filtered copy are marked **filtered-era**. Every count is reproducible
with `q.py` or `verify_bulk.py`.

Severity: **P1** wrong output a reader would trust · **P2** refuses or degrades
work it should handle · **P3** correctness risk under conditions not yet seen ·
**P4** housekeeping.

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

### C3 — 4 facility codes missing from the facilities table · P2

`NYMARCC`, `NYMDSTC`, `NYGROVC`, `NYFISHC` appear in detention stints but not in
`facilities-latest.parquet`. The join falls back to `detention_facility`, so
output degrades to the raw uppercase name rather than the canonical one. No
failure, no warning.

### C4 — Rows flagged `duplicate_drop_row` are labelled, not excluded · P2 · FIXED in 2.0.0

DDP flags 45,869 detention rows (national) as duplicates and excludes them from
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

61,888 stints (national) have no book-out; 61,594 stays have at least one open
stint. In a small number of stays the open stint is followed by a later one, so
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

```bash
python q.py "SELECT final_program, count(*) FROM 'detention-stints-latest.parquet' GROUP BY 1 ORDER BY 2 DESC"
python q.py "SELECT count(*) FROM 'detention-stints-latest.parquet' WHERE duplicate_drop_row"
python q.py "SELECT apprehension_aor, arresting_agency, count(*) FROM 'arrests-latest.parquet' GROUP BY 1,2"
```

Full queries for C1, C2, and C3 are in the session that produced this file.
