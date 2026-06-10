# Swarpius installer (PyInstaller bundles)

This directory holds the platform-specific PyInstaller specs and
runtime hooks for the standalone bundles.

## Building (Windows)

From `agent/` with a build venv active:

```powershell
python -m venv .venv-build
.venv-build\Scripts\Activate.ps1
python -m pip install -r requirements-server.txt
python -m pip install pyinstaller

cd ..\web-client
npm install
npm run build

cd ..\agent
pyinstaller installer\swarpius-windows.spec
```

Output: `agent\dist\Swarpius\swarpius.exe` (one-folder bundle —
runnable directly for testing, and the input to the installer step
below). The exe icon comes from the committed `installer\swarpius.ico`
— see "Branding art" below.

> Use `requirements-server.txt`, **not** `requirements.txt`. The
> server requirements omit PyAudio/numpy, which aren't needed in
> WS mode and pull in native binaries the bundle doesn't want.

Re-build cleanly by deleting `agent\dist\` and re-running the
`pyinstaller` command.

### Packaging the installer

The one-folder bundle above is the *input* to the Windows installer, not
the shipped artefact on its own. Compile it into `Swarpius-Setup.exe` with
[Inno Setup 6](https://jrsoftware.org/isinfo.php) (`ISCC.exe`):

```
ISCC.exe /DMyAppVersion=<x.y.z> installer\swarpius.iss
```

Output: `agent\dist\Swarpius-Setup.exe` — the Windows release artefact
(per-user or all-users install, Start-menu shortcut, and an Add/Remove
Programs uninstaller that offers to remove the data folder). `/DMyAppVersion`
stamps the version; omit it to default to the value in `swarpius.iss`. The
CI "Installer" workflow runs this step automatically, reading the version
from `agent/VERSION`.

## Building (Linux)

From `agent/` with a build venv active:

```bash
python3 -m venv .venv-build
source .venv-build/bin/activate
python3 -m pip install -r requirements-server.txt
python3 -m pip install pyinstaller

cd ../web-client
npm install
npm run build

cd ../agent
pyinstaller installer/swarpius-linux.spec
./installer/build-appimage.sh
```

Output: `agent/dist/Swarpius-x86_64.AppImage` (single self-contained
executable). Make it executable (`chmod +x`) and run.

The AppImage wrapper expects `appimagetool` on `PATH` (set
`APPIMAGETOOL=/path/to/appimagetool` to override). The branded app icon
`installer/swarpius.png` (256×256) is committed, so the wrapper uses it
directly — see "Branding art" below. The SVG-rasterise fallback (needs
`rsvg-convert` or ImageMagick's `convert`) only runs if that PNG is missing.

> **libfuse2 caveat (Ubuntu 24.04 LTS and similar).** AppImage's
> runtime requires libfuse2, which Ubuntu 24.04 omits from the
> default install. Users on those distros need
> `sudo apt install libfuse2` once before the AppImage will launch.

## Building (macOS, Apple Silicon)

Apple Silicon only — Intel Macs are out of scope for this release.

From `agent/` with a build venv active:

```bash
python3 -m venv .venv-build
source .venv-build/bin/activate
python3 -m pip install -r requirements-server.txt
python3 -m pip install pyinstaller

cd ../web-client
npm install
npm run build

cd ../agent
pyinstaller installer/swarpius-macos.spec
./installer/build-dmg.sh
```

Output: `agent/dist/Swarpius.app` (the bundle) and
`agent/dist/Swarpius-arm64.dmg` (the drag-to-Applications installer).

The DMG wrapper needs `create-dmg` (install with
`brew install create-dmg`).

### Branding art

The app icons (all platforms) and macOS DMG styling are driven by
committed assets in this directory, derived from the brand favicon glyph
(gold wave on the dark `#1a1612` background):

- `swarpius.icns` — macOS app-bundle icon (wired into `swarpius-macos.spec`)
  and the DMG volume / `.dmg`-file icon (`--volicon` in `build-dmg.sh`).
- `swarpius.ico` — Windows exe icon, 16–256 multi-resolution (wired into
  `swarpius-windows.spec`).
- `swarpius.png` — Linux AppImage / `.desktop` icon, 256×256
  (`build-appimage.sh` copies it into the AppDir).
- `dmg-background.png` — the macOS install-window background with the
  drag-to-Applications arrow and caption (`--background`). 600×380 — exactly
  the window size (create-dmg/Finder render it 1:1, not scaled, so a larger
  image is cropped). Cream so Finder's black icon labels stay readable.
- `swarpius.iconset/` + `swarpius-icon-1024.png` — source PNGs and the
  1024 master. All icons above are prebuilt and committed, so no generation
  step is needed at build time. To regenerate just the `.icns` on macOS:

  ```bash
  iconutil -c icns installer/swarpius.iconset -o installer/swarpius.icns
  ```

  To regenerate every asset from the glyph (needs `cairosvg` + `Pillow`):
  `python3 installer/make-art.py`.

A `.app` bundle launched from Finder routes stdout/stderr to the
system log (visible in `Console.app`). To watch the agent's startup
output live, run from Terminal directly:

```bash
/Applications/Swarpius.app/Contents/MacOS/swarpius
```

## Running and managing a built bundle

End-user instructions for installing, configuring, updating, and
removing the packaged app live in
[`docs/installed-app.md`](../../docs/installed-app.md); first-run setup
is covered by the in-app Getting Started guide. To smoke-test a build
you've just produced, run the executable directly (`swarpius.exe`,
`Swarpius.app`, or the AppImage) — it writes to the per-platform data
folder documented there, not to the bundle directory.

## Notes for distributors

- `version_info.txt` drives the name + version Windows shows in the
  firewall prompt, Task Manager, and exe properties dialog. It's a
  **template** — the version placeholders are filled from `agent/VERSION`
  by `swarpius-windows.spec` at build time, so there's nothing to bump
  here per release (bump `agent/VERSION`, the single source of truth).
- **Signing is a post-build step run by CI**, not by these scripts.
  The local build scripts produce unsigned artefacts; the release
  workflow signs Windows with Certum (signtool), macOS with the
  Apple Developer ID (codesign + notarytool), and the Linux
  `SHA256SUMS` file with the release GPG key.

### Cross-platform naming convention

All three specs apply the same split:

| Surface | Convention | Example |
|---|---|---|
| Executable filename | lowercase | `swarpius`, `swarpius.exe` |
| Install / bundle folder | capitalised | `Swarpius\`, `Swarpius.app`, `Swarpius.AppDir/` |
| OS-visible product name | capitalised "Swarpius" | Windows `FileDescription`, macOS `CFBundleName` / `CFBundleDisplayName`, Linux `.desktop` `Name=` |

The OS surfaces (firewall prompt, Dock, app menu, Activity
Monitor, GNOME app grid) read the metadata field, not the
filename — so set the metadata explicitly and let the executable
stay lowercase for shell ergonomics.
