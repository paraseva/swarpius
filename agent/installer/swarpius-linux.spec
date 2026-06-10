# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Linux x86_64 bundle.

Run from agent/ with the build venv active (use requirements-server.txt,
NOT requirements.txt — we don't want PyAudio/numpy in the bundle):

    cd agent
    pyinstaller installer/swarpius-linux.spec

Output: dist/Swarpius/swarpius (one-folder bundle). The web client
must already be built (cd web-client && npm run build). The AppImage
wrapper is a separate step — see installer/build-appimage.sh.

Bundle contents:
    - swarpius entry point (defaults to --ws via runtime hook)
    - app/, roon/, tools/, tts/, skills/, model_profiles.yaml
    - web-client/dist/ (served at http://localhost:8080/)
    - .env.template (user copies to .env on first launch)
    - LiteLLM dynamic provider modules via --collect-all
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

agent_dir = Path(SPECPATH).parent
repo_dir = agent_dir.parent

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
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
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
