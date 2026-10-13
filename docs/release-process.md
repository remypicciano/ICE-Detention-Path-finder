# Release process

Releases are tag-driven. Pushing a `v*` tag runs the desktop build matrix and
publishes the built applications as a GitHub Release with automatically
generated notes.

## Prerequisites

- The working tree is clean on `main`.
- CI (`pytest`, `ruff`, `mypy`) is green for the exact commit being tagged.
- The version bump has been merged to `main` in its own commit.

## Bumping the version

The version lives in `pyproject.toml` and is mirrored in these files — update
all of them in the same commit:

- `pyproject.toml` — `project.version`
- `ice_detention_pathway.py` — `APP_VERSION` (the single in-code source; the
  GUI and `fetch_data.py`'s user agent both import it)
- `ICEDetentionPathway.spec` — `CFBundleShortVersionString` / `CFBundleVersion`
- `version_info.txt` — `FileVersion` / `ProductVersion`

The desktop build workflow reads the version from `pyproject.toml`, so the
artifact filenames track the release automatically. Do not hardcode it.

The version comparison is numeric (`2.0` equals `2.0.0`), but the update
checker (`update_check.py`) treats any non-dotted-integer tag as uncomparable
and reports an error rather than guessing — keep tags in `v<digits.digits…>`
form.

## Artifact names are a contract

The update checker identifies the right download by the asset name
`ICEDetentionPathway-v<version>-<os>-<arch>.<ext>` produced by
`build-desktop.yml`. Renaming those assets or changing the platform suffixes
silently degrades the in-app check: it falls back to the release page instead
of naming the correct download. If you change the naming, change
`update_check.asset_for_machine` in the same commit.

Commit the bump:

```text
chore: release 3.2.0
```

## Tagging and releasing

```bash
git switch main
git pull
git tag -a v3.2.0 -m "ICE Detention Pathway 3.2.0"
git push origin v3.2.0
```

The `Build desktop applications` workflow then:

1. Runs the test suite on every supported OS (see `build-desktop.yml`).
2. Builds native executables for macOS (Apple Silicon + Intel), Windows, and
   Linux (x86_64 + aarch64).
3. Creates the GitHub Release `v3.2.0` and uploads all artifacts to it.

Watch the workflow run on the
[Actions tab](https://github.com/remypicciano/ICE-Detention-Path-finder/actions)
and confirm the release appears with all five artifacts.

## When the publisher changes the data schema

The data files change independently of this project's version. A release that
breaks them is usually not visible in CI, because the tests use fixtures.

Before shipping a data-dependent change, run the national checks against the
real files:

```bash
python fetch_data.py                 # prints fields the new release stopped publishing
python verify_bulk.py                # C1-C10 over the whole national dataset
python verify_pathway.py <IDENTIFIER>
```

`verify_bulk.py` is the gate. Read its `FIELDS THIS RELEASE DOES NOT PUBLISH`
section and the data window before trusting the output, and record both in the
release notes: a schema change that silently removes a field is a
user-visible behaviour change even when no code changed.

Things that have actually changed between releases, and how they were handled:

- `apprehension_site_landmark` → `event_landmark` (2026-09). Handled by
  `ARREST_LANDMARK_LINEAGE`; a hardcoded name broke every lookup.
- `book_in_aor` and `detainee_classification` removed (2026-09). Reported via
  `Pathway.missing_fields`, never silently blank.
- `initial_bond_set_amount` changed from `double` to `string` (2026-09).
- `unique_identifier` re-salted — **not** a stable person key across releases.

If a query in `q.py` or a verifier starts failing with `Referenced column ... not
found`, the release renamed or dropped that field. Add it to the relevant lineage
tuple rather than special-casing the caller, and add a note to `AUDIT.md`.

## After the release

- Update the release notes with any user-facing behaviour changes from
  `docs/architecture.md` (it documents what changed in each release).
- If the release fixed issues, add `Closes #N` notes in the release body.
- Announce the release in the project Discussions.

## Retracting a bad release

A broken release is fixed forward, never rewritten:

1. Open a fix PR on `main`, merge it, and cut a new patch tag (`v3.2.1`).
2. Mark the broken release as a pre-release or delete it in the GitHub UI.
3. Tell anyone who downloaded the bad artifacts to replace them.

Do not force-push over a tag.
