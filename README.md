# NYC detention lookup v1.2

## Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-lock.txt
```

The native GUI also requires Tk support. With Homebrew Python 3.14 on macOS:

```bash
brew install python-tk@3.14
```

## Desktop GUI

```bash
source .venv/bin/activate
python detention_lookup_gui.py
```

Paste an identifier, select **Search** (or press Return), and use **Copy to
Clipboard** to copy the complete detention timeline.

Results begin with the arrest date and location, followed by detention events
from oldest to most recent. Impossible chronology is preserved and labeled
`(DISCREPANCY: ...)`; a missing book-out date is labeled
`UNKNOWN - CURRENTLY HELD (?)`.

The output format is:

```text
arrest date, arrest location-> [Book-in: date/time][Book-out: date/time], facility:code -> next facility
```

Detention locations use `facilities-latest.parquet` to display the canonical
facility name and code, such as `Delaney Hall Detention Facility:DHDFNJ`.

The GUI also accepts an optional, more precise manual arrest location. This
overrides only the displayed timeline and never edits the source Parquet file.
After a successful search, the timeline text is editable before using **Copy to
Clipboard**.

Use **? Help** for in-app instructions and exact file requirements. **Keep NYC
Arrest Cohort…** retains only NYC-AOR arrests while preserving every detention
stint for those people, including transfers to facilities outside NYC.

If an earlier application version already removed non-NYC detention stints,
replace `detention-stints-latest.parquet` with a fresh original before applying
the revised filter; deleted transfers cannot be recovered from the filtered file.

## Terminal interface

```bash
source .venv/bin/activate
python detention_lookup.py
```

## Executable builds

See [BUILDING.md](BUILDING.md) for macOS, Windows, Linux, and Chromebook setup.
The GitHub Actions workflow produces native x64 and ARM64 Linux artifacts in
addition to Windows and separate Apple Silicon and Intel macOS applications.
