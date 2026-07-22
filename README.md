# NYC detention lookup

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

Use **? Help** for in-app instructions and exact file requirements. **Keep NYC
AOR Rows Only…** validates and then permanently overwrites all three datasets,
retaining only exact New York City Area of Responsibility rows.

## Terminal interface

```bash
source .venv/bin/activate
python detention_lookup.py
```

## Executable builds

See [BUILDING.md](BUILDING.md) for macOS, Windows, Linux, and Chromebook setup.
The GitHub Actions workflow produces native x64 and ARM64 Linux artifacts in
addition to the macOS and Windows applications.
