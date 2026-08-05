# Contributing to ICE Detention Pathway

Thank you for helping make public data easier to inspect responsibly. This
project is small and friendly — a good first issue is a great way to start.

## Code of conduct

Everyone participating in this project is expected to follow the
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Be respectful, assume good intent,
and remember that this project exists to help people understand government
data about real people in custody.

## Before opening a change

- **Search existing issues** before reporting a bug or proposing a feature.
- **Do not commit Parquet datasets, real identifiers, generated pathways, or
  other potentially sensitive records.** This is the one hard rule. Anonymized
  identifiers still describe people in ICE custody; one leaked screenshot can
  do real harm and damage the project's reputation.
- Use fabricated identifiers (e.g. `UFAKE-0001`) and events in tests, examples,
  and screenshots.
- Keep uncertainty visible. Do not convert missing data into factual claims.

## Picking an issue

- Look for issues labelled `good first issue` or `help wanted`.
- Comment on the issue before starting, saying you'd like to take it. This
  avoids two people doing the same work.
- If the issue is unassigned and you're ready to work, ask the maintainer to
  assign it to you.
- If the scope isn't clear, ask questions in the issue before writing code.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-build.txt
python -m pytest -q
python -m ruff check .
```

On Windows, activate the environment with
`.venv\Scripts\Activate.ps1`. The desktop app requires a Python installation
with Tk support.

Tests must never touch the network and must not require the large national
Parquet files — they run against small fabricated fixtures.

## Branch model

- Work on a short-lived branch off `main` (e.g. `fix/help-text`, `feat/search-ui`).
- Open a pull request into `main`.
- `main` is protected: every change lands via a reviewed pull request with
  passing CI.
- Keep your branch rebased on current `main` so the merge stays clean.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```text
feat: add a transfer log to the desktop app
fix: correct gap measurement for multi-stay lookups
docs: explain the machine-readable output format
test: cover one-arrest-no-stay lookups
chore: update dataset download URLs
```

Use `feat!:` or `fix!:` for breaking changes. The commit subject is the
headline of the changelog — make it specific.

## Pull requests

1. Keep each change focused on one problem.
2. Add or update tests for behavior changes.
3. Run `python -m pytest -q` and `python -m ruff check .`.
4. Update the README, `docs/`, or build guide when user-facing commands or
   output change.
5. Explain how you tested the change and note any data assumptions.

## Definition of done

- [ ] Tests pass locally and in CI
- [ ] New behavior is covered by a test
- [ ] No real identifiers or datasets are committed
- [ ] Docs are updated where user-facing behavior changed
- [ ] Commit messages follow Conventional Commits
- [ ] Branch is rebased on `main` and the PR is scoped to one change

## Reporting vulnerabilities

Do not post security or privacy issues in a public issue. Follow the process in
[SECURITY.md](SECURITY.md).
