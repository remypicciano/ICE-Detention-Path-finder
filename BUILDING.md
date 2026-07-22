# Building and testing the desktop application

PyInstaller builds for the operating system on which it runs. Build the `.app`
on macOS and the `.exe` on Windows. The Parquet datasets are external data and
must not be committed, uploaded to GitHub Actions, or bundled into the app.

## Data beside the application

For normal lookup, place these files beside the `.app` or `.exe`:

- `arrests-latest.parquet`
- `detention-stints-latest.parquet`

The in-app NYC filtering tool additionally requires:

- `joined-arrests-detention-stays-latest.parquet`

## Local macOS build

Install Python 3.14 and Tk support, then create the environment:

```bash
brew install python@3.14 python-tk@3.14
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-build.txt
```

Test and build:

```bash
python -m pytest -q
./build_macos.sh
dist/NYCDetentionLookup.app/Contents/MacOS/NYCDetentionLookup --self-test
```

Open `dist/NYCDetentionLookup.app`, paste a known valid identifier, verify its
timeline, and test Copy to Clipboard. An unsigned app may require Control-click,
Open on first launch.

## Local Windows build

Install 64-bit Python 3.14 from python.org with Tcl/Tk enabled. In PowerShell:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt -r requirements-build.txt
python -m pytest -q
.\build_windows.ps1
```

Run the bundled dependency check:

```powershell
$process = Start-Process -FilePath ".\dist\NYCDetentionLookup.exe" `
  -ArgumentList "--self-test" -Wait -PassThru
$process.ExitCode
```

An exit code of `0` passes. Then open the `.exe` normally and perform the same
identifier, timeline, and clipboard checks.

## GitHub Actions builds

1. Create a repository and push the source files. Confirm no `.parquet` files
   are staged; `.gitignore` excludes them.
2. Open the repository's **Actions** tab.
3. Select **Build desktop applications**.
4. Select **Run workflow**.
5. When both jobs pass, open the run and download the macOS and Windows
   artifacts from its **Artifacts** section.
6. Extract the appropriate ZIP and place the required Parquet files beside the
   resulting `.app` or `.exe`.

The workflow also runs when a tag beginning with `v` is pushed. Artifacts are
retained for 14 days and contain application code only.
