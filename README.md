# ICE Detention Pathway

> Reconstruct an anonymized person's recorded route through ICE custody—from
> arrest through every known detention-facility transfer.

[![Tests](https://github.com/remypicciano/ICE-Detention-Path-finder/actions/workflows/build-desktop.yml/badge.svg)](https://github.com/remypicciano/ICE-Detention-Path-finder/actions/workflows/build-desktop.yml)
![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![Platforms](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-555)
![Data stays local](https://img.shields.io/badge/data-stays%20local-2E7D32)

ICE Detention Pathway turns linked Parquet records from the
[Deportation Data Project](https://deportationdata.org/data/processed/ice.html)
into a readable chronology. Give it a `unique_identifier`, `stay_ID`, or
`stint_ID`; it finds the corresponding ICE arrest and orders every recorded
detention stint by book-in time, resolving facility codes to canonical names.

The project includes a desktop application, a terminal interface, a
memory-efficient Parquet inspector, an optional NYC arrest-cohort filter, tests,
and native build automation for macOS, Windows, and Linux.

## What it produces

```text
arrest
  → first detention facility
  → transfer
  → latest recorded facility
```

Example output (fabricated):

```text
2025-01-12 14:30:00 UTC, NEW YORK, NY-> [Book-in: 2025-01-12 18:10:00 UTC][Book-out: 2025-01-14 09:00:00 UTC], Facility A:AAA -> [Book-in: 2025-01-14 11:45:00 UTC][Book-out: UNKNOWN - CURRENTLY HELD (?)], Facility B:BBB
```

### Highlights

- Reconstructs all recorded facility stints for one anonymized identifier.
- Accepts base identifiers as well as suffixed stay and stint identifiers.
- Normalizes timestamps to UTC and facility codes to canonical names.
- Preserves questionable chronology and labels it with `DISCREPANCY`.
- Marks a missing latest book-out as a possible current placement.
- Keeps searches local: the application makes no network requests.
- Supports an editable, clipboard-ready timeline in the desktop app.

## Quick start

### 1. Get the data

Download the current Parquet datasets from the Deportation Data Project's
[ICE data page](https://deportationdata.org/data/processed/ice.html) and place
these files in the project directory:

```text
arrests-latest.parquet
detention-stints-latest.parquet
facilities-latest.parquet
```

The optional NYC cohort tool also needs:

```text
joined-arrests-detention-stays-latest.parquet
```

The datasets are intentionally excluded from Git and from application bundles.
Keep the filenames exactly as shown.

### 2. Install

```bash
git clone https://github.com/remypicciano/ICE-Detention-Path-finder.git
cd ICE-Detention-Path-finder
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-lock.txt
```

The lock file reproduces the tested Python 3.14 environment. The package itself
supports Python 3.11 or newer. On macOS, the desktop interface also requires Tk:

```bash
brew install python-tk@3.14
```

### 3. Run

Desktop application:

```bash
python ice_detention_pathway_gui.py
```

Terminal interface:

```bash
python ice_detention_pathway.py UNIQUE_IDENTIFIER
```

You can point the terminal interface at files stored elsewhere:

```bash
python ice_detention_pathway.py UNIQUE_IDENTIFIER \
  --arrests-file /path/to/arrests-latest.parquet \
  --detention-file /path/to/detention-stints-latest.parquet \
  --facilities-file /path/to/facilities-latest.parquet
```

Run `python ice_detention_pathway.py --help` for every option.

## Desktop workflow

1. Paste a `unique_identifier`, `stay_ID`, or `stint_ID`.
2. Optionally enter a more precise arrest location. This changes only the
   displayed result.
3. Select **Search** or press Return.
4. Review any `DISCREPANCY` or `UNKNOWN` labels.
5. Edit the generated text if needed, then select **Copy to Clipboard**.

The **Keep NYC Arrest Cohort…** action reduces the local data to arrests made
under the New York City Area of Responsibility while retaining every associated
detention stint, including transfers outside New York. It overwrites three
local Parquet files only after generating and validating replacements. Start
with fresh originals and keep backups; filtering cannot restore previously
removed records.

## How it works

1. The input is normalized to the base identifier before its first underscore.
2. DuckDB requires exactly one matching arrest record.
3. Every detention stint with that anonymized identifier is joined to the
   facilities table.
4. Stints are sorted chronologically and formatted as one continuous pathway.
5. Impossible dates are retained and clearly labeled instead of silently fixed.

The lookup runs in an in-memory DuckDB database. Source Parquet files are
read-only during normal searches.

## Data interpretation and safety

This tool reconstructs **recorded detention history**, not a confirmed
deportation outcome or a real-time location. A missing book-out date means only
that the source data has no later release or transfer recorded; the person may
have since been moved, released, removed, or affected by a reporting delay.

The Deportation Data Project notes that the source data comes from ICE through
Freedom of Information Act requests, contains known limitations, and may be
revised. Read its [ICE core data codebook](https://deportationdata.org/docs/ice/codebook)
before drawing conclusions. Treat generated pathways as investigative leads,
verify consequential claims against primary records, and handle even
anonymized data responsibly.

Suggested data attribution:

> Government data provided by ICE in response to a FOIA request, processed by
> the Deportation Data Project, and analyzed with ICE Detention Pathway.

This project is independent and is not affiliated with ICE or the Deportation
Data Project.

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

See [BUILDING.md](BUILDING.md) for native application builds and platform
troubleshooting. Contributions are welcome; start with
[CONTRIBUTING.md](CONTRIBUTING.md).

## Project structure

```text
ice_detention_pathway.py       Core query, validation, and CLI
ice_detention_pathway_gui.py   Tk desktop application
nyc_filter.py                  Validated NYC arrest-cohort filtering
parquet_viewer.py              Bounded Parquet schema/row inspector
ICEDetentionPathway.spec       Cross-platform PyInstaller definition
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
