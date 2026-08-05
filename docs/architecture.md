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
[STAY 1 of 2] NO ARREST RECORD IN THIS DATASET -> [Book-in: 2024-09-16 17:56:00 UTC][Book-out: 2024-11-29 10:44:00 UTC][Facility: Montgomery Processing Center:MTGPCTX]
[first stint — final_program: Border Patrol; book_in_aor: Houston Area of Responsibility]
[last stint — classification: Medium / Low; case_status: ACTIVE; threat_level: NA; final_order: NO]
=== RELEASED (Paroled); NOT IN ICE CUSTODY FOR 396 days ===
[STAY 2 of 2] 2025-12-30 10:36:35 UTC, NDD - 26 FEDERAL PLAZA NY, NY -> [Book-in: 2025-12-30 11:17:00 UTC][Book-out: 2025-12-30 18:25:00 UTC][Facility: NYC Hold Room:NYCHOLD] -> [Book-in: 2025-12-30 20:26:00 UTC][Book-out: 2026-01-23 13:44:00 UTC][Facility: Orange County Jail:ORANGNY] -> [Book-in: 2026-01-23 13:45:00 UTC][Book-out: UNKNOWN - CURRENTLY HELD (?)][Facility: MDC Brooklyn:BOPBRO]
[first stint — final_program: Non-Detained Docket Control; book_in_aor: New York City Area of Responsibility]
[last stint — classification: Low; case_status: ACTIVE; threat_level: NA; final_order: NO]
```

Every stay names the fields of its **first** and **last** stint. The first
stint's `final_program` is always shown — Border Patrol and ERO Criminal Alien
Program are CBP/ICE programs that the arrests table does not carry, so this is
the only place they are visible. When later stints in the same stay disagree,
the differing values are listed. The last stint's record state is quoted so a
reader sees the stay's final record without the tool narrating an outcome.

The output is a structured text format with fixed delimiters — stints joined by
` -> `, each stint a sequence of `[Label: value]` blocks, one field list per
line. It is machine-readable; the exact grammar is specified in [§14](#14-machine-readable-output-format).

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

Two false-pairing paths are closed. A lone arrest no longer pairs with a lone
stay without the time check, so an arrest logged after the stay's book-in
stays unpaired (`[ARREST WITH NO RECORDED DETENTION]`). And once an earlier
stay has claimed a newer arrest, an older leftover arrest can no longer be
dumped on a later, unrelated stay — claims must be strictly newer across a
person's stays. The bulk verifier removed 456 false pairings across the
national data; see section 11.

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
NO ARREST RECORD IN THIS DATASET -> [Book-in: 2024-09-16 17:56:00 UTC][Book-out: 2024-11-29 10:44:00 UTC][Facility: Montgomery Processing Center:MTGPCTX]
[first stint — final_program: Border Patrol; book_in_aor: Houston Area of Responsibility]
```

The first stint's program is shown for **every** stay, arrest or no arrest —
the arrests table does not carry the CBP/ICE program, so this line is the only
place Border Patrol or ERO Criminal Alien Program appears. The reader maps the
field names to the DDP codebook themselves. The tool makes no causal claim.

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

### 11. A suffixed identifier scopes the answer to one stay

Pasting a `stay_ID` (`base_YYYY-MM-DD HH:MM:SS`) or `stint_ID`
(`base_..._code`) returns the whole person's history by default. Now the
suffix names the stay being asked about: that stay renders in full and the
person's other stays collapse to one context line.

```text
  [CONTEXT — another stay for this person: 2024-09-16 17:56:00 UTC -> 2024-11-29 10:44:00 UTC, Montgomery Processing Center:MTGPCTX]
[STAY 2 of 2] 2025-12-30 10:36:35 UTC, NDD - 26 FEDERAL PLAZA NY, NY -> …
```

The CLI prints `Scoped to stay:` when a suffix matched.

### 12. Every stay names its own metadata

Each stay reports the fields of its first and last stint instead of a single
opening note:

- Every stint is a sequence of `[Label: value]` blocks: `[Book-in: …]`,
  `[Book-out: …]`, `[Facility: name:code]`. Facility names may contain commas
  (`CCA, FLORENCE CORRECTIONAL CENTER`), so they are bracketed rather than
  separated by commas; the name/code split is the first `:`.
- The first stint's program and AOR are always named:
  `[first stint — final_program: …; book_in_aor: …]`. When later stints
  disagree, the differing values are listed: `[stint fields — …]`.
- The last stint's record state is rendered verbatim:
  `[last stint — classification; case_status; threat_level; final_order;
  final_order_date; departed; charge]`.

These are quotes from the detention record, not outcomes. A missing `departed`
means only that no later record exists in the source data.

### 13. `verify_bulk.py` — national bulk verification

The core tool answers one identifier per query. `verify_bulk.py` applies the
*same* grouping and pairing to every identifier in one streaming pass, then
checks invariants over all 1,014,866 people (C1–C10):

```bash
python verify_bulk.py                  # full pass + 1500-id cross-check
python verify_bulk.py --skip-sample    # bulk checks only, faster
python verify_bulk.py --limit 400000   # cap stints read (dev smoke runs)
```

The full national run passes every check and reproduces a deterministic
rendering digest (sha256) — the same identifiers render to the same text every
run. It also reports source-data anomalies that no pipeline can fix: ~3% of
stays whose published boundary in the stays table is wider than the stint rows
(31,618 earlier book-ins, 26,148 later book-outs, 441 closed-without-book-out),
and 9 `stint_ID`s the source places under two stays. C10 enforces the output
grammar's delimiter contract against the live data, so a future DDP data
refresh cannot silently break the machine-readable format. Counts and wording
live in `AUDIT.md`.

### 14. Machine-readable output format

The rendered pathway is a line-oriented text format with fixed delimiters. Any
line can be parsed independently; a field value never spans lines. The
delimiters below were chosen so that no value in the source data can collide
with them — C10 in `verify_bulk.py` verifies that property against the live
Parquet files on every run.

**Line types** (a pathway is lines in this order: header, stays, orphan arrests):

| Line | Grammar |
| --- | --- |
| header | `Identifier: <base>` · `Scoped to stay: <stay_ID>` · `Stays: <n>   Detention rows: <m>` · `Arrests with no recorded detention: <n>` |
| stay | `[STAY <n> of <m>] ` + timeline, then its field lines |
| field line | `[first stint — <pairs>]` · `[stint fields — <pairs>]` · `[last stint — <pairs>]` |
| gap | `=== RELEASED (<reason>); NOT IN ICE CUSTODY FOR <span> ===` · `=== NOT IN ICE CUSTODY FOR <span> ===` · `=== SEPARATE STAY; GAP NOT MEASURABLE ===` |
| context | `  [CONTEXT — another stay for this person: <start> -> <end>, <label>]` |
| orphan | `[ARREST WITH NO RECORDED DETENTION] ` + timeline |

A lone stay (single-stay lookup) carries no `[STAY n of m]` prefix — the
timeline line stands alone. Header lines (`Identifier:`, `Stays:`, …) are CLI
output and are not part of the pathway text itself.

**Timeline grammar**

```
timeline  := opening ( " -> " stint )*
opening   := <timestamp> ", " <location>        # split on the FIRST ", "; locations may contain ", "
           | "NO ARREST RECORD IN THIS DATASET"
           | <opening> " -> NO DETENTION RECORD IN THIS DATASET"
stint     := [ "(DISCREPANCY: " warnings ")" " " ]
             "[Book-in: " <ts> "]"
             "[Book-out: " <ts> "]"
             "[Facility: " <name> [ ":" <code> ] [ " [FLAGGED DUPLICATE ROW]" ] "]"
ts        := "YYYY-MM-DD HH:MM:SS UTC" | "UNKNOWN - CURRENTLY HELD (?)" | "UNKNOWN UTC"
```

- ` -> ` is the only segment separator and never appears inside a value.
- Each stint is a sequence of `[Label: value]` blocks joined by `][`; a
  `(DISCREPANCY: …)` prefix, when present, precedes the first block and ends at
  the first `) `. Facility names may contain commas, so the block's closing
  bracket — not a comma — bounds the value.
- Within a `[Facility: …]` value, the first `:` separates the name from the
  code; names never contain a colon.

**Field-list grammar**

```
pairs := pair ( "; " pair )*
pair  := <label> ": " <value>      # split on the FIRST ": " occurrence
```

Labels are `final_program`, `book_in_aor`, and the seven `[last stint]` labels
(`classification`, `case_status`, `threat_level`, `final_order`,
`final_order_date`, `departed`, `charge`). A field line is terminated by
end-of-line, **not** by its closing `]`: `final_charge` values quote statute
citations that contain brackets (e.g. `[AFTER 4/1/97]`), so a parser should
read to end-of-line and then strip exactly one trailing `]`. When a stay has
more than one differing value for the same field, each value gets its own pair
(`final_program: A; final_program: B`), so `; ` always separates complete
pairs. Values may contain `:` and `[`/`]`; they never contain `; ` or a
newline, so the pair split is unambiguous.

**Parser sketch** (complete — parses the worked example above):

```python
import re

SEG = " -> "

def parse_timeline(text):
    opening, *stints = re.split(re.escape(SEG), text)
    if opening == "NO ARREST RECORD IN THIS DATASET":
        events = [{"kind": "note", "text": opening}]
    else:
        ts, _, location = opening.partition(", ")
        events = [{"kind": "arrest", "ts": ts, "location": location}]
    for stint in stints:
        if stint.startswith("NO DETENTION RECORD"):
            events.append({"kind": "note", "text": stint})
            continue
        if stint.startswith("(DISCREPANCY: "):
            prefix, _, stint = stint.partition(") ")
        else:
            prefix = None
        fields = {}
        for block in stint.split("]["):
            block = block.strip("[]")
            label, _, value = block.partition(": ")
            if label == "Facility":
                name, _, rest = value.partition(":")
                fields["facility"] = name
                fields["code"] = rest.split(" [")[0] if rest else ""
            else:
                fields[label.lower()] = value
        if prefix:
            fields["discrepancy"] = prefix
        events.append({"kind": "stint", **fields})
    return events

def parse_field_line(line):
    label, _, rest = line[1:].partition(" — ")
    rest = rest[:-1] if rest.endswith("]") else rest   # strip the closing bracket
    return label, [p.partition(": ")[::2] for p in rest.split("; ")]

def parse_pathway(lines):
    stays, current = [], None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("  [CONTEXT"):
            current["context"] = line[2:]
        elif line.startswith("[STAY "):
            m = re.match(r"\[STAY (\d+) of (\d+)\] ?(.*)", line)
            current = {"index": int(m.group(1)), "total": int(m.group(2)),
                       "timeline": parse_timeline(m.group(3)), "fields": []}
            stays.append(current)
        elif line.startswith(("[first stint —", "[stint fields —", "[last stint —")):
            current["fields"].append(parse_field_line(line))
        elif line.startswith("=== "):
            current["gap"] = line
        elif current is None and (
            line.startswith(("NO ARREST RECORD", "NO DETENTION RECORD"))
            or " -> " in line
        ):
            current = {"index": 1, "total": 1,
                       "timeline": parse_timeline(line), "fields": []}
            stays.append(current)
    return stays
```

The format is documented as the contract for the current release; identifiers
are stable, rendering text is not guaranteed stable across releases (the
`verify_bulk` digest changes whenever wording changes).

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
- **C2** — a small number of stints overlap the previous stint inside the same
  stay; they are flagged.
- **C8 boundary deltas** — the published stays table carries a wider nominal
  window than the stint rows for ~3% of stays (31,618 earlier book-ins,
  26,148 later book-outs). Reported by `verify_bulk.py`; not pipeline failures.
- **Shared stint_IDs** — the source places 9 stints' `stint_ID` under more than
  one stay; they resolve to the earliest containing stay.

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

1. **Replace your Parquet files.** The desktop app's **Download / Update
   Data…** button fetches the current national files listed in
   `data-sources.json`, or run `python fetch_data.py` from the command line.
   Required: `arrests-latest.parquet`, `detention-stints-latest.parquet`,
   `facilities-latest.parquet`. The download also fetches the optional
   `detention-stays-latest.parquet` and
   `joined-arrests-detention-stays-latest.parquet`; the joined file is not used
   by the lookup.
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
