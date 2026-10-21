<p align="center">
  <img src="assets/ice_cube.png" alt="ICE Detention Pathway" width="140">
</p>

<h1 align="center">ICE Detention Pathway</h1>

<p align="center">
  Reconstruct an anonymized person's recorded route through ICE custody — from
  arrest through every known detention-facility transfer.
</p>

<p align="center">
  <a href="https://github.com/remypicciano/ICE-Detention-Path-finder/actions/workflows/ci.yml"><img src="https://github.com/remypicciano/ICE-Detention-Path-finder/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/License-MIT-2E7D32" alt="License: MIT">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-555" alt="Platforms">
  <img src="https://img.shields.io/badge/data-stays%20local-2E7D32" alt="Data stays local">
  <img src="https://img.shields.io/badge/PRs-welcome-important" alt="PRs welcome">
</p>

ICE Detention Pathway turns linked Parquet records from the
[Deportation Data Project](https://deportationdata.org/data/processed/ice.html)
into a readable chronology. Give it a `unique_identifier`, `stay_ID`, or
`stint_ID`; it finds every recorded stay, pairs each with the arrest that opened
it, and orders the detention stints inside each stay by book-in time, resolving
facility codes to canonical names.

A person may be detained more than once. Separate stays are reported separately,
never chained into one pathway, because reading two detentions as continuous
would overstate time in custody.

- **Desktop app** for pasting an identifier and copying a clean timeline
- **Terminal interface** for scripts and automation
- **In-app dataset download** — one click fetches the current national files
- **Provenance receipts** that trace every printed value to its source row
- **Bulk verification** (C1–C10) that reconciles the whole national run against
  the source data on every release
- Runs 100% offline during lookups — the only network use is the optional
  download button and the **Check for Updates…** button
- **Update check** — the app tells you when a newer release is published and
  opens the download page; it never replaces files by itself

This project uses Deportation Data Project ICE exports as source data. It is
independent and is not affiliated with the project or with ICE.

## What it produces

The output is a structured, machine-readable text format: stints joined by
` -> `, each stint a sequence of `[Book-in: …][Book-out: …][Facility: …]`
blocks, one field line per stay. Example (fabricated):

```text
[STAY 1 of 2] NO ARREST RECORD IN THIS DATASET -> [Book-in: 2024-09-16 17:56:00 UTC][Book-out: 2024-11-29 10:44:00 UTC][Facility: Montgomery Processing Center:MTGPCTX]
[first stint — final_program: Border Patrol; book_in_aor: Houston Area of Responsibility]
=== RELEASED (Paroled); NOT IN ICE CUSTODY FOR 396 days ===
[STAY 2 of 2] 2025-12-30 10:36:35 UTC, NDD - 26 FEDERAL PLAZA NY, NY -> [Book-in: 2025-12-30 11:17:00 UTC][Book-out: 2025-12-30 18:25:00 UTC][Facility: NYC Hold Room:NYCHOLD] -> [Book-in: 2025-12-30 20:26:00 UTC][Book-out: UNKNOWN - CURRENTLY HELD (?)][Facility: MDC Brooklyn:BOPBRO]
[last stint — classification: Low; case_status: ACTIVE; threat_level: NA; final_order: NO]
```

The exact grammar is specified in
[`docs/architecture.md#14-machine-readable-output-format`](docs/architecture.md#14-machine-readable-output-format).

## Screenshot

The desktop app opens with a fabricated two-stay pathway already rendered, so
you can see the exact output format before downloading any data:

```bash
python ice_detention_pathway_gui.py --demo
```

<img src="assets/app-screenshot.png" alt="ICE Detention Pathway desktop app showing a fabricated two-stay pathway" width="720">

The version stands next to the title in the header, so you can tell at a glance
whether the copy in front of you is the one you read about. The window is sized
to its content: every button is inside it, and the result pane scrolls instead
of stretching the frame.

## Quick start

### 1. Install

```bash
git clone https://github.com/remypicciano/ICE-Detention-Path-finder.git
cd ICE-Detention-Path-finder
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-lock.txt
```

The lock file reproduces the tested Python 3.14 environment; the package itself
supports Python 3.11 or newer. On macOS, the desktop interface also requires
Tk:

```bash
brew install python-tk@3.14
```

### 2. Get the data

Download the current national Parquet files from inside the app
(**Download / Update Data…**), or from the terminal:

```bash
python fetch_data.py
```

This fetches `arrests-latest.parquet`, `detention-stints-latest.parquet`,
`facilities-latest.parquet`, plus the optional
`detention-stays-latest.parquet` and
`joined-arrests-detention-stays-latest.parquet`. Every file is verified as
readable Parquet with the expected columns before it replaces anything, so a
failed download leaves your existing data untouched.

Use the complete national files. Locally reduced copies silently remove people
and stays, and no search result can reveal that something is missing. The
datasets are excluded from Git and from application bundles; keep the filenames
exactly as shown.

The download URLs live in `data-sources.json`. It is tracked here, shipped
inside the application and released next to it, so the download feature works
in a fresh copy without any setup. To point the app at a different mirror, put
your own `data-sources.json` beside the app: the copy beside the app wins over
the bundled one.

The datasets are downloaded into the folder beside the app when that folder can
be written to, and into `~/.ice_pathway/data` when it cannot. macOS runs an app
opened straight from a download from a read-only place, and a download written
there fails with *read-only file system*. Files already beside the app keep
being read either way.

### 3. Run

Desktop application:

```bash
python ice_detention_pathway_gui.py
```

Terminal interface:

```bash
python ice_detention_pathway.py UNIQUE_IDENTIFIER
python ice_detention_pathway.py UNIQUE_IDENTIFIER --export-json pathway.json
```

`--export-json PATH` also writes the pathway as structured JSON (UTC-normalized
ISO-8601 timestamps, one entry per stint) for downstream tools. Run
`python ice_detention_pathway.py --help` for every option, including pointing
the tool at data stored elsewhere.

`--check-update` asks GitHub for the newest published release, compares it to
the running version, and prints where to get it — useful for scripts and for
source checkouts:

```bash
python ice_detention_pathway.py --check-update
```

## Desktop workflow

1. Paste a `unique_identifier`, `stay_ID`, or `stint_ID`.
2. Optionally enter a more precise arrest location. This changes only the
   displayed result.
3. Select **Search** or press Return.
4. Review any `DISCREPANCY`, `UNKNOWN`, or `[STAY n of total]` labels.
5. Edit the generated text if needed, then select **Copy to Clipboard**.
6. Select **Export JSON…** to save the pathway as structured JSON for
   downstream tools.

Every successful search is recorded automatically. The **Recent lookups**
panel shows the last 10; double-click a row to re-open it, or **View all…**
for the full, filterable history.

**Check for Updates…** reads GitHub's latest published release and, when a
newer version exists, offers the download page and the asset matching your
machine. It only reads release metadata — the application itself is never
replaced automatically, and your Parquet data files are never touched.

## Bulk search

**Bulk Search…** reads a JSON file of identifiers, searches each one, and
exports the results:

- **Input JSON** (any of these shapes):
  ```json
  ["id1", "id2"]
  ```
  ```json
  {"identifiers": ["id1", "id2"]}
  ```
  ```json
  [{"identifier": "id1", "label": "Case A"}, {"identifier": "id2"}]
  ```
- **Export JSON…** writes one bundle with each person's pathway plus a
  found/failed summary.
- **Export Text…** writes a readable report: every pathway with a
  `===== identifier (label) =====` header and a final summary.
- Failed lookups (unknown identifiers, no records) are reported per-row and do
  not stop the batch.

## Marked MOVES export

`emit_moves.py` turns a list of identifiers into the delimited MOVES text used
by saved outputs, stamped with the release it came from:

```bash
python emit_moves.py ids.txt --new-dir . -o moves.txt --manifest moves.manifest.json
```

Every block opens with a watermark naming its generation and how far its data
runs, for example `dataset-NEW data-up-to=2026-08-06`, then the field lines,
then the `MOVES:` line whose segments are joined by ` -> `, the identifier, and
`//`. A block is never emitted without the watermark, because a block separated
from its source still has to say which dataset produced it. Row counts, newest
timestamps, per-file date spans, and the local retrieval date behind that
watermark go to `--manifest` instead of repeating them on every block.

`ids.txt` holds one identifier per line, a JSON list, or a saved output file to
read identifiers from. `--edits edits.json` supplies manual corrections, which
are marked inline with `[MANUAL_EDIT]` and named on an `edited-manual=` line, so
a reader never has to scan for markers to know whether a block is source data.

## How it works

1. The input is normalized to the base identifier before its first underscore.
2. Every detention stint for that identifier is joined to the facilities table
   and grouped into stays by `stay_ID`.
3. Each stay takes the nearest unclaimed arrest preceding its first book-in.
   Stays with no such record are labelled
   `NO ARREST RECORD IN THIS DATASET`, and arrests that opened no recorded stay
   are reported separately.
4. Stints are sorted chronologically within each stay. Stays are rendered
   separately, with the release reason and the gap between them.
5. Impossible dates are retained and clearly labeled instead of silently fixed.
   A chronology warning is only raised at a full-day gap, so the noise of
   paperwork-ordering inversions doesn't drown out real contradictions.

The lookup runs in an in-memory DuckDB database, which reads the Parquet files
in place. Nothing is imported and the source files are never modified.

### Fields the current release does not publish

The publisher reshapes these files between releases. When a field disappears the
tool omits it and **names the omission** rather than leaving a blank that reads
as "unknown":

```
FIELDS THIS RELEASE DOES NOT PUBLISH
  book_in_aor, detainee_classification
  These are omitted from every pathway. A value is absent from the
  source, not merely unknown, so nothing in the output stands in for it.
```

Absent is different from unknown. Unknown means the publisher recorded a row and
left part of it empty; absent means there is no column at all. Conflating them
would let a reader assume an arrested person's AOR was blank when in fact the
release simply stopped publishing it. The same list appears in the terminal
output, in the desktop status bar, in `fetch_data.py` after an update, and as
`source_fields_absent` in the JSON export.

### Verifying a result

`verify_pathway.py` prints the source spreadsheet, sheet, and row behind every
value in a pathway, plus SHA-256 prefixes of the input files:

```bash
python verify_pathway.py UNIQUE_IDENTIFIER
```

`verify_bulk.py` applies the same grouping and pairing to every identifier in
one streaming pass and checks C1–C10 invariants across the national data:

```bash
python verify_bulk.py                  # full pass + 1500-id cross-check
python verify_bulk.py --skip-sample    # bulk checks only, faster
```

### Identifier scope: valid only within one release

`unique_identifier` is **not a stable person key across releases.** The
publisher re-salts it each time the tables are rebuilt. Comparing the
2025-08 and 2026-09 releases:

- every one of the 1,014,866 old `unique_identifier` values is absent from the
  new release;
- a natural-key match on `(birth_year, gender, book_in_date_time,
  detention_facility_code)` finds 3,171,995 matching stints, of which **zero**
  share a `unique_identifier`;
- all 1,087,417 old `stay_ID` values are absent from the new release.

The consequence is concrete: **an identifier copied from an older export will
not resolve after you update the data**, and two exports of the same person
from different dates cannot be linked automatically. Within a single release the
IDs are consistent — `detainers-latest.parquet` joins to
`detention-stints-latest.parquet` on 325,571 people — so a pathway built once
from a single download is internally sound. It simply is not comparable across
downloads. If you need longitudinal work, record the file hashes alongside your
results (which `verify_pathway.py` already prints) and re-derive from scratch
rather than diffing identifiers.

### Finding the new identifier for an old one

`link_releases.py` rebuilds that link from the data itself. A person is
identified by their ordered chain of `(facility, book-in, book-out)` stints,
accepted only when `birth_year` and `gender` also agree:

```bash
python link_releases.py old_ids.json --old-dir OLD_DIR --new-dir NEW_DIR \
    --out-crosswalk crosswalk.json --out-text linked.txt
```

`old_ids.json` is a JSON array of old identifiers, or a saved text export —
identifiers are read from its `Identifier:` lines. The crosswalk records
`exact` (path unchanged), `extended` (stints appended since the old export),
`ambiguous`, or `not_found` for each input.

A candidate set with more than one member is reported as ambiguous and never
guessed at, because attaching the wrong person's detention history is worse
than reporting none: a wrong pathway is indistinguishable from a true one to
whoever reads it. People who cannot be re-identified are listed with the
reason — the publisher publishes no crosswalk, so the path is the only bridge.

## Data interpretation and safety

This tool reconstructs **recorded detention history**, not a confirmed
deportation outcome or a real-time location. A missing book-out date means only
that the source data has no later release or transfer recorded; the person may
have since been moved, released, removed, or affected by a reporting delay.

Absence of a record is not evidence of absence in reality. The Deportation Data
Project notes that the source data comes from ICE through Freedom of
Information Act requests, contains known limitations, and may be revised. Read
its [ICE core data codebook](https://deportationdata.org/docs/ice/codebook)
before drawing conclusions. Treat generated pathways as investigative leads,
verify consequential claims against primary records, and handle even
anonymized data responsibly.

Suggested data attribution:

> Government data provided by ICE in response to a FOIA request, processed by
> the Deportation Data Project, and analyzed with ICE Detention Pathway.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — data model, release changes,
  and the machine-readable output spec
- [`AUDIT.md`](AUDIT.md) — known bugs and data issues, severity-ranked
- [`BUILDING.md`](BUILDING.md) — native macOS / Windows / Linux builds
- [`docs/release-process.md`](docs/release-process.md) — how releases are cut
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to contribute
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) — community guidelines
- [`SECURITY.md`](SECURITY.md) — reporting vulnerabilities
- [`LICENSE`](LICENSE) — MIT

## Development

Install development dependencies and run the test suite:

```bash
python -m pip install -r requirements.txt -r requirements-build.txt
python -m pytest -q
```

Inspect a dataset without loading it in full:

```bash
python parquet_viewer.py detention-stints-latest.parquet --rows 5
```

## Project structure

```text
ice_detention_pathway.py       Core query, stay grouping, and CLI
ice_detention_pathway_gui.py   Tk desktop application
fetch_data.py                  Downloads and validates the national datasets
verify_pathway.py              Source-row provenance receipt for one identifier
verify_bulk.py                 National bulk verification (C1–C10)
ice_history.py                 Lookup history and bulk-search helpers
emit_moves.py                  Marked, delimited MOVES export for a list of ids
link_releases.py               Old → new identifier crosswalk across releases
q.py                           Ad-hoc SQL against the local Parquet files
parquet_viewer.py              Bounded Parquet schema/row inspector
data-sources.json              Dataset download URLs (shipped with the app)
data-sources.example.json      Template for a customised copy
tools/make_icons.py            Draws the app icon in every size the OS asks for
tools/verify_bundle_icon.py    Checks that a built app carries a readable icon
assets/                        App icon, Windows icon, and the screenshot
docs/                          Architecture and output-format documentation
AUDIT.md                       Known bugs and data issues, severity-ranked
test_*.py                      Automated tests
```

## About the author

Created by [Rémy Picciano](https://github.com/remypicciano). ICE Detention
Pathway reflects his interest in turning complex public data into approachable,
practical tools through transparent workflows, careful handling of uncertainty,
and software that non-specialists can run locally.

## Contact

- Questions or bug reports: [open a GitHub issue](https://github.com/remypicciano/ICE-Detention-Path-finder/issues)
- Project and collaboration inquiries: [@remypicciano on GitHub](https://github.com/remypicciano)
