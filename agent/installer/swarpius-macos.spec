# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the macOS arm64 (.app) bundle.

Run from agent/ with the build venv active (use requirements-server.txt,
NOT requirements.txt — we don't want PyAudio/numpy in the bundle):

    cd agent
    pyinstaller installer/swarpius-macos.spec

Output: dist/Swarpius.app — a standard macOS application bundle ready
for the .dmg wrap step (see installer/build-dmg.sh) and the codesign +
notarytool step that runs in CI once the Apple Developer ID lands.

Apple Silicon only — Intel Macs are deferred per phase-1-installer-plan.md.

Bundle contents:
    - Contents/MacOS/swarpius entry point (defaults to --ws via runtime hook)
    - Contents/Resources/ holds app/, skills/, web-client/dist/, model_profiles.yaml, .env.template
    - LiteLLM dynamic provider modules via --collect-all
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

agent_dir = Path(SPECPATH).parent
repo_dir = agent_dir.parent

app_version = (agent_dir / "VERSION").read_text(encoding="utf-8").strip()

litellm_datas, litellm_binaries, litellm_hiddenimports = collect_all("litellm")
roonapi_datas, roonapi_binaries, roonapi_hiddenimports = collect_all("roonapi")

# tiktoken finds its encodings (cl100k_base etc.) by scanning the
# tiktoken_ext namespace at runtime, which PyInstaller can't see — without
# this the frozen app raises "Unknown encoding cl100k_base".
tiktoken_hiddenimports = collect_submodules("tiktoken_ext")

a = Analysis(
    [str(agent_dir / "swarpius.py")],  # supervisor entry: catches the agent's exit-75 and respawns (Apply & Restart)
    pathex=[str(agent_dir)],
    binaries=litellm_binaries + roonapi_binaries,
    datas=[
        (str(repo_dir / "web-client" / "dist"), "web-client/dist"),
        # Silent stop-marker track, seeded into the user data dir on first
        # launch (data_paths.ensure_stop_marker_asset) for the user to add
        # to their Roon library. data_paths reads it from <_MEIPASS>/assets.
        (str(repo_dir / "assets" / "Swarpius Stop Simulation"), "assets/Swarpius Stop Simulation"),
        (str(agent_dir / "skills"), "skills"),
        (str(agent_dir / "model_profiles.yaml"), "."),
        (str(agent_dir / ".env.template"), "."),
        (str(agent_dir / "VERSION"), "."),
        (str(agent_dir / "analyser" / "analysis-guide.md"), "analyser"),
        *litellm_datas,
        *roonapi_datas,
    ],
    hiddenimports=litellm_hiddenimports + roonapi_hiddenimports + tiktoken_hiddenimports + ["agent"],  # swarpius.py imports agent at runtime (function-level)
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[
        str(agent_dir / "installer" / "_runtime_hook_default_ws.py"),
    ],
    excludes=[
        "pyaudio",
        "numpy",
        "pytest",
        "ruff",
        "torch",
        "transformers",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="swarpius",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # .app bundles route stdout/stderr to the system log when launched
    # via Finder; there's no terminal window to "keep open" on macOS.
    # Users who want to see startup messages run from Terminal:
    #   /Applications/Swarpius.app/Contents/MacOS/swarpius
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",
    # codesign_identity / entitlements_file are populated by the
    # post-build signing step (CI), not the build step.
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Swarpius",
)

# Identifier follows reverse-DNS; revisit if the public repo lands at a
# different domain than github.com/paraseva.
app = BUNDLE(
    coll,
    name="Swarpius.app",
    icon=str(agent_dir / "installer" / "swarpius.icns"),
    bundle_identifier="app.swarpius.Swarpius",
    info_plist={
        "CFBundleName": "Swarpius",
        "CFBundleDisplayName": "Swarpius",
        "CFBundleExecutable": "swarpius",
        "CFBundleIdentifier": "app.swarpius.Swarpius",
        "CFBundleShortVersionString": app_version,
        "CFBundleVersion": app_version,
        "NSHighResolutionCapable": True,
        # No file-type associations or URL schemes yet.
        "LSMinimumSystemVersion": "12.0",
    },
)
