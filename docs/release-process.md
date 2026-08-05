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
- `ice_detention_pathway_gui.py` — `APP_VERSION`
- `ICEDetentionPathway.spec` — `CFBundleShortVersionString` / `CFBundleVersion`
- `version_info.txt` — `FileVersion` / `ProductVersion`
- `fetch_data.py` — `USER_AGENT` string

The desktop build workflow reads the version from `pyproject.toml`, so the
artifact filenames track the release automatically. Do not hardcode it.

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
