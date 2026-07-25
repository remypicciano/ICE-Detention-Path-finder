# Contributing to ICE Detention Pathway

Thank you for helping make public data easier to inspect responsibly.

## Before opening a change

- Search existing issues before reporting a bug or proposing a feature.
- Do not commit Parquet datasets, real identifiers, generated pathways, or other
  potentially sensitive records.
- Use fabricated identifiers and events in tests, examples, and screenshots.
- Keep uncertainty visible. Do not convert missing data into factual claims.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-build.txt
python -m pytest -q
```

On Windows, activate the environment with
`.venv\Scripts\Activate.ps1`. The desktop app requires a Python installation
with Tk support.

## Pull requests

1. Keep each change focused.
2. Add or update tests for behavior changes.
3. Run `python -m pytest -q`.
4. Update the README or build guide when user-facing commands change.
5. Explain how you tested the change and note any data assumptions.

Report security or privacy concerns privately through Rémy Picciano's
[GitHub profile](https://github.com/remypicciano) instead of posting sensitive
details in a public issue.

