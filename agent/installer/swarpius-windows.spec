# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows x86_64 bundle.

Run from agent/ with the build venv active (use requirements-server.txt,
NOT requirements.txt — we don't want PyAudio/numpy in the bundle):

    cd agent
    pyinstaller installer/swarpius-windows.spec

Output: dist/Swarpius/swarpius.exe (one-folder bundle). The web client
must already be built (cd web-client && npm run build).

Bundle contents:
    - swarpius.exe entry point (defaults to --ws via runtime hook)
    - app/, roon/, tools/, tts/, skills/, model_profiles.yaml
    - web-client/dist/ (served at http://localhost:8080/)
    - .env.template (user copies to .env and edits next to the exe)
    - LiteLLM dynamic provider modules via --collect-all
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

agent_dir = Path(SPECPATH).parent
repo_dir = agent_dir.parent

# Generate the Windows VERSIONINFO from agent/VERSION, so version_info.txt
# stays a template rather than a second place to bump the version.
app_version = (agent_dir / "VERSION").read_text(encoding="utf-8").strip()
_v = (app_version.split(".") + ["0", "0", "0"])[:3]
_version_info = (
    (agent_dir / "installer" / "version_info.txt").read_text(encoding="utf-8")
    # FixedFileInfo is a 4-int binary field (Windows requirement); the
    # display strings follow our x.y.z convention, matching the installer.
    .replace("@VERSION_TUPLE@", ", ".join(_v + ["0"]))
    .replace("@VERSION@", ".".join(_v))
)
_build_dir = agent_dir / "build"
_build_dir.mkdir(parents=True, exist_ok=True)
_version_info_path = _build_dir / "version_info.resolved.txt"
_version_info_path.write_text(_version_info, encoding="utf-8")

# LiteLLM dynamically imports provider modules at call time; PyInstaller's
# static analyser misses them. Bundling everything LiteLLM includes is the
# right answer here vs. a per-provider --hidden-import allowlist that
# would need updating every time LiteLLM adds a provider.
litellm_datas, litellm_binaries, litellm_hiddenimports = collect_all("litellm")

# Roonapi also has dynamic discovery bits worth pulling in defensively.
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
        # Analyser runtime asset: loaded by file path
        # (analyser/analyse.py:GUIDE_PATH). PyInstaller's import-graph
        # analysis picks up the .py files automatically; non-Python
        # assets need explicit datas entries.
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
        # CLI-mode TTS deps — wrapped in try/except at the import site
        # (tts/tts.py), so the bundle behaves correctly when they're
        # absent. requirements-server.txt already omits them; explicit
        # excludes guarantee they don't sneak in via a transitive dep.
        "pyaudio",
        "numpy",
        # Dev/test deps that shouldn't be included.
        "pytest",
        "ruff",
        # Heavy ML deps not in the agent's dep tree — defensive.
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
    # Keep the console window: it shows startup messages, the WS URL,
    # and serves as a "close to stop" surface. Once we have a tray
    # wrapper this becomes False.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Version info: drives the firewall prompt label, Task Manager
    # name, and Properties dialog. Without it Windows falls back to
    # the bare exe filename.
    version=str(_version_info_path),
    icon=str(agent_dir / "installer" / "swarpius.ico"),
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
