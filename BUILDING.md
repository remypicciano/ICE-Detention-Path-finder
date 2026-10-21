# Building and testing the desktop application

PyInstaller builds for the operating system on which it runs. Build the `.app`
on macOS and the `.exe` on Windows. The Parquet datasets are external data and
must not be committed, uploaded to GitHub Actions, or bundled into the app.

## Data beside the application

For normal lookup, place these files beside the `.app` or `.exe`:

- `arrests-latest.parquet`
- `detention-stints-latest.parquet`
- `facilities-latest.parquet`

Use the complete national files. A locally reduced copy silently removes people
and stays, and a search result cannot reveal that anything is missing.

## The dataset URLs beside the application

`data-sources.json` holds the download URLs of the current datasets. It is
tracked in the repository, bundled inside the application, and released next to
it, so a fresh copy finds its URLs without any setup. A copy beside the app
takes precedence over the bundled one, so a wrong URL can be corrected without
rebuilding:

```bash
cp data-sources.example.json data-sources.json   # start from the template
```

The bundled self-test checks that the URLs can be found, so a packaging
mistake fails the build rather than the first download:

```bash
dist/ICEDetentionPathway.app/Contents/MacOS/ICEDetentionPathway --self-test
```

## The application icon

The window, the Dock and the taskbar icon are drawn by the project, not
imported from a picture service. `tools/make_icons.py` draws the artwork once
and writes every size macOS and Windows ask for:

```bash
python tools/make_icons.py            # rewrites assets/ice_cube.png, app.icns, app.ico
```

The files carry no metadata about where they came from. `tools/verify_bundle_icon.py`
checks a built application instead of assuming the icon survived the build: the
`.icns` has to hold every size macOS asks for, the bundle has to declare an
icon file it can read, and the artwork in the `.icns` has to be the artwork the
window uses. macOS answers an application whose icon it cannot read with a
placeholder of its own, so this check runs in the build:

```bash
python tools/verify_bundle_icon.py dist/ICEDetentionPathway.app
```

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
dist/ICEDetentionPathway.app/Contents/MacOS/ICEDetentionPathway --self-test
```

Open `dist/ICEDetentionPathway.app`, paste a known valid identifier, verify its
timeline, and test Copy to Clipboard. An unsigned app may require Control-click,
Open on first launch.

### Choosing and opening the macOS artifact

Check the Mac processor in Terminal:

```bash
uname -m
```

- `arm64`: download `ICEDetentionPathway-v2.1.4-macOS-ARM64` (Apple Silicon).
- `x86_64`: download `ICEDetentionPathway-v2.1.4-macOS-X64` (Intel).

GitHub downloads an outer artifact ZIP containing a second, macOS-preserving
ZIP. Extract the outer ZIP, then extract the inner
`ICEDetentionPathway-v2.1.4-macOS-*.zip` before opening the `.app`. The inner ZIP is
important because it preserves the app bundle and executable permissions.

The inner archive holds the app and `data-sources.json` at its root. Leave them
together: the app finds the dataset URLs beside itself, and falls back to the
copy inside itself if the file is moved away.

On first launch, Control-click the extracted app and choose **Open**. If macOS
still blocks it, open **System Settings → Privacy & Security**, find the blocked
app notice, and select **Open Anyway**. If macOS says the application cannot be
opened rather than showing a security warning:

1. Confirm that the downloaded architecture matches `uname -m`.
2. Extract both ZIP layers again with Archive Utility.
3. Confirm the executable permission:

   ```bash
   chmod +x ICEDetentionPathway.app/Contents/MacOS/ICEDetentionPathway
   ```

4. For an app downloaded from your own trusted repository, clear its quarantine
   attribute if necessary:

   ```bash
   xattr -dr com.apple.quarantine ICEDetentionPathway.app
   ```

Then Control-click and choose **Open** again. Formal Apple code signing and
notarization would eliminate most of this friction but requires an Apple
Developer identity.

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
$process = Start-Process -FilePath ".\dist\ICEDetentionPathway.exe" `
  -ArgumentList "--self-test" -Wait -PassThru
$process.ExitCode
```

An exit code of `0` passes. Then open the `.exe` normally and perform the same
identifier, timeline, and clipboard checks.

### What to expect on Windows

1. Extract the downloaded ZIP. Do not run the application from inside the ZIP.
2. Put `ICEDetentionPathway.exe`, `data-sources.json`, and the four exactly
   named Parquet files in one writable folder, such as
   `Documents\ICEDetentionPathway`. The archive already contains
   `data-sources.json`.
3. Double-click `ICEDetentionPathway.exe`.
4. Because the app is not code-signed, Microsoft Defender SmartScreen may show
   **Windows protected your PC**. Select **More info**, verify the app name, then
   select **Run anyway**.
5. If Windows instead blocks the downloaded file, right-click the ZIP or EXE,
   choose **Properties**, select **Unblock** near the bottom of the General tab,
   then select **Apply**.
6. Defender or another antivirus program may scan a new PyInstaller executable
   on first launch. The app does not require administrator privileges or an
   installer.

Keep the app out of `Program Files` so it can read the Parquet files placed
beside the executable. A future code-signing certificate would reduce
SmartScreen warnings.

## GitHub Actions builds

1. Create a repository and push the source files. Confirm no `.parquet` files
   are staged; `.gitignore` excludes them.
2. Open the repository's **Actions** tab.
3. Select **Build desktop applications**.
4. Select **Run workflow**.
5. When all jobs pass, open the run and download the architecture-matched
   macOS, Windows, or Linux artifact from its **Artifacts** section.
6. Extract the appropriate ZIP and place the required Parquet files beside the
   resulting `.app` or `.exe`.

The workflow also runs when a tag beginning with `v` is pushed. Artifacts are
retained for 14 days and contain application code only.

## Chromebook / ChromeOS Linux build

ChromeOS runs this application through its Debian-based Linux development
environment. In ChromeOS, open **Settings → About ChromeOS → Developers → Linux
development environment**, then select **Set up**.

In the Chromebook Terminal, identify the processor architecture:

```bash
uname -m
```

Download the matching Actions artifact:

- `x86_64`: `ICEDetentionPathway-v2.1.4-Linux-x86_64`
- `aarch64` or `arm64`: `ICEDetentionPathway-v2.1.4-Linux-aarch64`

Move the downloaded archive and the required Parquet files into **Linux files**.
The archive holds the executable and `data-sources.json`; keep them together.
Then extract and run it:

```bash
tar -xzf ICEDetentionPathway-v2.1.4-Linux-*.tar.gz
chmod +x ICEDetentionPathway
./ICEDetentionPathway
```

The executable and all four exactly named Parquet files must remain in the
same Linux directory. Managed school or workplace Chromebooks may disable the
Linux development environment.

On a regular Debian or Ubuntu Linux desktop, the same commands apply. If the
system is minimal and the window cannot start, install the common Tk/X11 runtime
libraries:

```bash
sudo apt-get update
sudo apt-get install -y tk libx11-6 libxext6 libxrender1 libxft2 libfontconfig1
```

Useful troubleshooting:

- `Permission denied`: run `chmod +x ICEDetentionPathway` again.
- `Exec format error`: download the artifact matching `uname -m`.
- Data file not found: confirm the Parquet names and capitalization exactly.
- No window appears: confirm Linux GUI applications are supported and that the
  command is being run inside a graphical Linux/ChromeOS session.
