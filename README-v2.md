# ICE Detention Pathway — v2

> Reconstruct an anonymized person's recorded route through ICE custody, one
> stay at a time, with every value traceable to the source row it came from.

This document covers the v2 rewrite (releases 3.0.0 and 3.1.0). It supersedes
`README.md` and is written to be attached to the GitHub release.

---

## Why v2 exists

v1 assumed one arrest per person and rendered every detention stint for an
identifier as a single continuous pathway. That assumption is wrong, and it
produced output that overstated time in custody.

A worked example, using identifier
`1bc786a1349f010c22b4bf43d82289708e52d25d`. This person has four detention
stints. v1 printed them as one chain:

```text
(DISCREPANCY: arrest date is after first detention book-in) 2025-12-30 10:36:35 UTC,
NDD - 26 FEDERAL PLAZA NY, NY-> [Book-in: 2024-09-16 …], Montgomery Processing
Center:MTGPCTX -> [Book-in: 2025-12-30 …], NYC Hold Room:NYCHOLD -> …
```

Two problems, one loud and one quiet.

The loud one is the `DISCREPANCY`. The check compared the arrest against the
earliest book-in for the whole identifier, which belonged to a detention that
ended more than a year before the arrest. It was a false positive.

The quiet one is worse. The `->` between Montgomery and NYC Hold Room is the
same arrow the tool uses for a facility transfer, but it spans **396 days
during which the person was not in custody at all**. The 2024 stint ends with
`detention_release_reason = Paroled`, a field v1 never printed. A reader sees
unbroken detention from September 2024 to the present — roughly 16 months —
where the truth is 73 days, release, then a new arrest 13 months later.

The data was never wrong. `stay_ID` records the separation, and v1 ignored it.

---

## What v2 produces

```text
[STAY 1 of 2] NO ARREST RECORD IN THIS DATASET (first stint — final_program: Border Patrol; book_in_aor: Houston Area of Responsibility)-> [Book-in: 2024-09-16 17:56:00 UTC][Book-out: 2024-11-29 10:44:00 UTC], Montgomery Processing Center:MTGPCTX
=== RELEASED (Paroled); NOT IN ICE CUSTODY FOR 396 days ===
[STAY 2 of 2] 2025-12-30 10:36:35 UTC, NDD - 26 FEDERAL PLAZA NY, NY-> [Book-in: 2025-12-30 11:17:00 UTC][Book-out: 2025-12-30 18:25:00 UTC], NYC Hold Room:NYCHOLD -> [Book-in: 2025-12-30 20:26:00 UTC][Book-out: 2026-01-23 13:44:00 UTC], Orange County Jail:ORANGNY -> [Book-in: 2026-01-23 13:45:00 UTC][Book-out: UNKNOWN - CURRENTLY HELD (?)], MDC Brooklyn:BOPBRO
```

Single-stay lookups are unchanged from v1 and carry no `[STAY n of total]`
label — the numbering appears only when stays must be told apart.

### The data model

```text
person   (unique_identifier)  — one human
  └─ stay   (stay_ID)         — one continuous period in ICE custody
       └─ stint               — one facility placement inside that stay
```

A stay ends with a release, removal, parole, or bond. Multiple stints inside a
stay are transfers between facilities.

---

## Changes in v2

### 1. Separate stays are never merged

Stints are grouped by the `stay_ID` recorded in the source data. Each stay is
rendered on its own, separated by its release reason and the measured gap.
Nothing about the grouping is inferred by this project.

### 2. The arrest record is no longer required

v1 refused to produce anything unless it found exactly one arrest row. Two
populations were lost:

- **People with more than one arrest.** v1 raised
  `Expected exactly one arrest row, but found 2` and produced nothing. 465
  identifiers hit this in the test dataset alone.
- **People with no ICE arrest row at all.** The arrests dataset covers ICE
  arrests. Anyone transferred into ICE custody another way has detention
  records and no arrest record, and was unsearchable.

v2 queries the detention table independently and fails only when both tables
come up empty. A stay with no arrest is labelled
`NO ARREST RECORD IN THIS DATASET`; an arrest with no detention is reported
under `[ARREST WITH NO RECORDED DETENTION]`.

### 3. Each stay is paired with its own arrest

A stay takes the **latest** unclaimed arrest preceding its first book-in — not
the earliest. For someone arrested more than once, an arrest from a year
earlier must not claim a recent stay.

### 4. Chronology warnings are scoped to one stay

Warnings compare an arrest only against the stay it opened, so an unrelated
earlier detention can no longer trigger a false `DISCREPANCY`.

### 5. Discrepancies are flagged at day scale, not second scale

**This is the largest behavioural change in 3.1.0.**

v1 flagged any inversion, however small. In the test dataset, 9,058 stays have
an arrest timestamped after their own first book-in. The distribution is
sharply bimodal:

| Gap | Stays |
|---|---|
| any | 9,058 |
| ≥ 1 day | 1,191 |
| ≥ 7 days | 1,171 |
| ≥ 30 days | 1,160 |

7,867 cases — 87% — are under 24 hours, and past one day the curve flattens
almost completely: only 20 cases fall between 1 and 7 days. A sub-day inversion
is the order in which paperwork was filed, not an impossible sequence. Flagging
them put `DISCREPANCY` on roughly a third of all lookups, which taught readers
to ignore the label entirely.

v2 flags a contradiction only once it reaches a **full day**, via a single
constant, `DISCREPANCY_MINIMUM_GAP`, applied uniformly to all three chronology
checks: arrest after book-in, book-out before book-in, and a stint beginning
before the previous stint's book-out.

Nothing is hidden or altered. Sub-day inversions remain fully visible in the
printed timestamps and in the source rows; they are simply not labelled as
impossible. The same threshold cuts stint overlaps from 204 to 104.

### 6. Field names instead of narration

An earlier draft rendered the opening of an unexplained stay as
`(entered via Border Patrol, Houston Area of Responsibility)`. That was wrong
in a way worth spelling out, because it is the kind of error this project
exists to avoid.

`final_program` is the program of record for a case. It is **not** an
arresting-agency field. Nothing in the ICE data confirms which agency made an
apprehension. Worse, the label sat in the arrest slot while both values came
from the *detention* table, and `final_program` exists in the arrests file too,
with a different vocabulary — 8 distinct values there against 19 in detention.

v2 names the fields and their source instead:

```text
NO ARREST RECORD IN THIS DATASET (first stint — final_program: Border Patrol; book_in_aor: Houston Area of Responsibility)
```

The reader maps those field names to the DDP codebook themselves. The tool
makes no causal claim.

### 7. Duplicate rows are labelled, not dropped

The Deportation Data Project flags some detention rows as duplicates and
excludes them from its published `n_stints`. v2 keeps every row and appends
`[FLAGGED DUPLICATE ROW]` to the facility name, so counts here can be
reconciled against DDP's figures instead of quietly diverging.

### 8. The NYC arrest-cohort filter is removed

`nyc_filter.py`, its tests, its GUI action, and its documentation are gone.

**Removing the feature does not restore filtered data.** If the filter was ever
run, the local Parquet files were overwritten in place and the removed rows are
unrecoverable. Download fresh national files and keep backups.

### 9. `verify_pathway.py` — provenance receipts

New tool. Prints the source spreadsheet, sheet, and row behind every value in a
pathway, plus SHA-256 prefixes of the input files and the scope of the local
arrests data:

```bash
python verify_pathway.py UNIQUE_IDENTIFIER
python verify_pathway.py UNIQUE_IDENTIFIER --html pathway.html
```

It delegates stay grouping and arrest pairing to `ice_detention_pathway`, so
the receipt cannot reach a different conclusion than the pathway it verifies.
A verifier that can contradict the thing it verifies is worse than none.

### 10. `q.py` — ad-hoc SQL

A 20-line runner for checking any claim in this document against the data:

```bash
python q.py "SELECT final_program, count(*) FROM 'detention-stints-latest.parquet' GROUP BY 1 ORDER BY 2 DESC"
python q.py "SELECT stay_ID, book_in_date_time FROM 'detention-stints-latest.parquet' WHERE unique_identifier = ?" IDENTIFIER
```

DuckDB reads the Parquet files in place. Nothing is imported, written, or
persisted.

---

## What v2 deliberately does not do

**It does not derive a sub-agency.** This was investigated and rejected. The
candidate fields do not support it:

- `arresting_agency` exists only on arrests and holds a single value.
- `final_program` is a program of record, with an uncontrolled vocabulary — it
  carries two unmerged spellings of the same unit (`Homeland Security
  Investigations`, 48 rows; `HSI Criminal Arrest Only`, 2 rows) — and 708
  detention rows leave it blank.
- `book_in_site` is free text: 145 variants, `Sub Office` alongside
  `Sub-Office`, mixed case. Only 28.1% carry an `ERO` prefix, 63.6% contain
  `DOCKET CONTROL`, the categories overlap, and 5.5% match no recognizable
  token at all.

Parsing these into an agency taxonomy would mean inventing structure the data
does not carry. Sub-units, task forces, and joint operations are not modelled
anywhere in the source. v2 passes the recorded strings through verbatim and
merges nothing.

**It does not normalize or repair values.** Impossible dates are retained and
labelled. Duplicate rows are retained and labelled. Facility codes absent from
the facilities table (`NYMARCC`, `NYMDSTC`, `NYGROVC`, `NYFISHC`) fall back to
the raw detention-table name.

---

## Known issues

`AUDIT.md` records every finding from a full review of the code and datasets,
severity-ranked, with reproduction commands. Items fixed in 3.1.0 are marked.
Open items include:

- **B2** — program vocabulary is uncontrolled; output cannot be grouped by
  agency without a mapping this project does not have.
- **B3** — pasting a `stay_ID` still returns every stay for that person.
- **A4** — a lone arrest pairs with a lone stay without a time check.
- **A6** — stay metadata reads `final_program` from the first stint and
  `release_reason` from the last; a stay that moves between AORs shows only its
  opening one.
- **C2** — 204 stints overlap the previous stint inside the same stay.
- **C7** — a locally filtered dataset cannot report that it is filtered.

---

## Data interpretation and safety

This tool reconstructs **recorded detention history**, not a confirmed
deportation outcome or a real-time location. A missing book-out means only that
the source data has no later release or transfer recorded; the person may since
have been moved, released, removed, or affected by a reporting delay.

Absence of a record is not evidence of absence in reality. This applies with
particular force to a filtered dataset, where a search result cannot reveal
what was removed.

The source data comes from ICE through Freedom of Information Act requests,
contains known limitations, and may be revised. Read the
[ICE core data codebook](https://deportationdata.org/docs/ice/codebook) before
drawing conclusions. Treat generated pathways as investigative leads, verify
consequential claims against primary records, and handle even anonymized data
responsibly.

Suggested attribution:

> Government data provided by ICE in response to a FOIA request, processed by
> the Deportation Data Project, and analyzed with ICE Detention Pathway.

This project is independent and is not affiliated with ICE or the Deportation
Data Project.

---

## Upgrading from v1

1. **Replace your Parquet files** with fresh national downloads from the
   [DDP ICE data page](https://deportationdata.org/data/processed/ice.html).
   Required: `arrests-latest.parquet`, `detention-stints-latest.parquet`,
   `facilities-latest.parquet`. The joined file is no longer used.
2. **Re-check any saved pathway** for a person with more than one stay. v1
   output for those people understated the gaps between detentions.
3. **Expect fewer `DISCREPANCY` labels.** Sub-day inversions are no longer
   flagged. This is intentional; see change 5.
4. `nyc_filter` is gone. Scripts importing it will fail.

## Project structure

```text
ice_detention_pathway.py       Core query, stay grouping, and CLI
ice_detention_pathway_gui.py   Tk desktop application
verify_pathway.py              Source-row provenance receipts
q.py                           Ad-hoc SQL against the local Parquet files
parquet_viewer.py              Bounded Parquet schema/row inspector
AUDIT.md                       Known bugs and data issues, severity-ranked
test_*.py                      Automated tests
```
