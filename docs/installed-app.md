# Installing and managing the Swarpius app

This guide covers the packaged Swarpius application — the downloadable Windows, macOS, and Linux builds. To run Swarpius with Docker or from source instead, see the main [README](../README.md).

Once Swarpius is running, the in-app **Getting Started** guide (shown automatically on first launch, and reopenable from the Settings header) walks you through connecting an LLM provider and your Roon Core.

## Installing

The macOS and Linux builds are self-contained — no installer, no registry entries, no system services. The Windows build uses a small standard installer. None of them install background system services.

**Windows**

1. Download `Swarpius-Setup.exe` and run it.
2. The installer offers a **per-user** install (no admin required) or an **all-users** install (requires admin) — pick whichever you prefer. It installs Swarpius and adds a Start-menu shortcut (and, optionally, a desktop icon).
3. Launch Swarpius from the Start menu. A console window shows the local URL, and your browser opens to the Settings page on first launch.

**macOS** (Apple Silicon)

1. Open the downloaded `.dmg`.
2. Drag `Swarpius.app` into your Applications folder.
3. Launch it from Applications. Your browser opens to the Settings page on first launch.

**Linux**

1. Make the downloaded AppImage executable: `chmod +x Swarpius-x86_64.AppImage`.
2. Run it. Your browser opens to the Settings page on first launch.

> On Ubuntu 24.04 LTS and similar, the AppImage runtime requires libfuse2: run `sudo apt install libfuse2` once.

First-run setup happens entirely on the Settings page — add an LLM API key and approve the Swarpius extension in Roon. The in-app Getting Started guide covers this.

## Network access

By default Swarpius listens only on your own computer (`127.0.0.1`), so the app is reachable only from a browser on the same machine. That's the safe default: the connection between the browser and the agent has **no authentication**.

To use Swarpius from another device on your network — a phone or tablet, say — set `SWARPIUS_WS_HOST="0.0.0.0"` in the `.env` in your data folder (see [Where your data lives](#where-your-data-lives) below) and restart the app. **Only do this on a network you trust:** anyone who can reach the app can control playback and read your configured API keys. See [SECURITY.md](../SECURITY.md) for the full picture.

## Verifying your download

Verification is optional but confirms a download came from Paraseva (authenticity) and arrived intact (integrity). Each release publishes a `SHA256SUMS` file — checksums of all three platform builds — and a detached GPG signature, `SHA256SUMS.asc`. The signing public key is published as `RELEASE-GPG-KEY.asc` in the repository.

1. Download `SHA256SUMS`, `SHA256SUMS.asc`, and `RELEASE-GPG-KEY.asc` alongside the build you want.
2. Import the key once, then verify the signature:

   ```bash
   gpg --import RELEASE-GPG-KEY.asc
   gpg --verify SHA256SUMS.asc SHA256SUMS
   ```

   A `Good signature from "Paraseva Ltd (Swarpius releases)"` line confirms authenticity. A `this key is not certified with a trusted signature` warning is expected — it only means you have not personally signed the key; the signature itself is still valid.

3. Confirm the downloaded build matches its published checksum. Run the command in the folder holding the download:

   | OS | Command |
   |---|---|
   | Linux | `sha256sum --ignore-missing -c SHA256SUMS` |
   | macOS | `shasum -a 256 Swarpius-arm64.dmg` — compare the output to the matching line in `SHA256SUMS` |
   | Windows (PowerShell) | `(Get-FileHash Swarpius-Setup.exe -Algorithm SHA256).Hash` — compare to the matching line in `SHA256SUMS` |

   `SHA256SUMS` lists all three platform builds. On Linux, `--ignore-missing` checks only the file(s) you actually downloaded and skips the rest — without it, `sha256sum -c` reports the absent builds as failures and exits non-zero. `shasum` on macOS has no equivalent flag, so hash your downloaded file directly and compare it to its line. If you downloaded several builds into one folder, the same commands verify each one that's present.

### Application Signing
Each build also carries the signing its platform supports, on top of the checksum verification above:

#### Windows
The installer (`Swarpius-Setup.exe`) is Authenticode-signed. Right-click it → **Properties → Digital Signatures** lists **Paraseva Ltd**.

#### macOS 
`Swarpius.app` is signed with a Paraseva Ltd Developer ID certificate and notarised by Apple, with the notarisation ticket stapled to the `.dmg`, so it opens without a Gatekeeper warning. To confirm explicitly, run this in Terminal before installing:

```bash
spctl -a -t open --context context:primary-signature -v Swarpius-arm64.dmg
```

A `source=Notarized Developer ID` line confirms both the signature and the notarisation. After installing, `codesign -dv --verbose=4 /Applications/Swarpius.app` reports `Authority=Developer ID Application: Paraseva Ltd`.

#### Linux 
The AppImage carries no OS-level signature (the platform has no equivalent); verify it with the GPG-signed `SHA256SUMS` described above.

## Where your data lives

Configuration, logs, the Roon pairing token, and the conversation database live **outside** the application folder, so they survive updates and don't require the app folder to be writable.

| OS | Data folder |
|---|---|
| Windows | `%LOCALAPPDATA%\Swarpius\` (typically `C:\Users\<you>\AppData\Local\Swarpius\`) |
| macOS | `~/Library/Application Support/Swarpius/` |
| Linux | `$XDG_DATA_HOME/swarpius/` (defaults to `~/.local/share/swarpius/`) |

The configuration file is the `.env` inside that folder. Set the `SWARPIUS_DATA_DIR` environment variable to relocate everything (for example to a USB stick or shared drive).

Inside the data folder:

| Path | Purpose |
|---|---|
| `.env` | Your configuration (created on first launch; edit via the Settings page or directly) |
| `config/roon_core_id`, `config/roon_core_token` | Roon pairing |
| `logs/` | Conversation and server logs (retained 7 days by default) |
| `messages.db` | Conversation history |
| `play_history.json` | Per-zone recent-track history (only updated while Swarpius is running) |
| `cli_history` | CLI-mode command history |
| `model_profiles.yaml` | (Optional) per-model LLM tuning overrides — see [model-profiles.md](model-profiles.md) |

## Updating

Download the latest version and install it over the previous one:

- **Windows** — run the new `Swarpius-Setup.exe`; it installs over the previous version, closing Swarpius first if it's still running.
- **macOS** — quit the app, then replace `Swarpius.app`.
- **Linux** — replace the AppImage.

Your configuration, Roon pairing, history, and logs live in the separate data folder above and are untouched by replacing the app. An existing `.env` is never overwritten.

## Uninstalling

- **Windows** — uninstall via **Settings → Apps → Installed apps** (or the Start-menu uninstall entry). The uninstaller offers to remove your data folder (`%LOCALAPPDATA%\Swarpius`) as well; decline to keep your configuration and history.
- **macOS / Linux** — delete the application (`Swarpius.app` or the AppImage). No registry keys or system services are created.

To remove your configuration, logs, and Roon pairing on macOS/Linux (or if you declined the prompt on Windows), delete the data folder shown above.

## Resetting to a fresh state

To start over as if on first launch:

**Windows (PowerShell):**

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Swarpius"
```

**macOS / Linux:**

```bash
rm -rf "$HOME/Library/Application Support/Swarpius"     # macOS
rm -rf "${XDG_DATA_HOME:-$HOME/.local/share}/swarpius"  # Linux
```

The next launch recreates the folder with fresh defaults. To re-run only the Roon pairing without wiping everything else, delete `config/roon_core_id` and `config/roon_core_token` from the data folder, then clear `ROON_CORE_URL` and `ROON_CORE_NAME` in Settings.
