# Swarpius TTS Server

If desired, you can make use of voice-cloning text-to-speech for Swarpius via **[F5-TTS](https://github.com/SWivid/F5-TTS)** by SWivid — the third-party model and inference code that does the actual speech synthesis. This directory adds a thin TCP socket API on top so the agent can stream audio in and out. All credit for the synthesis quality belongs to the F5-TTS authors; please consider starring or contributing to that project if you find Swarpius's spoken output useful.

> The Docker image built from this directory clones F5-TTS at a pinned tag (default `1.1.16`, configurable via the `F5_TTS_VERSION` build arg) from its GitHub repository at build time and installs it into a venv inside the image — Swarpius does not redistribute F5-TTS. F5-TTS's **code** is MIT-licensed, but its **pretrained model weights are released under CC-BY-NC** (non-commercial, owing to the datasets they were trained on) — so commercial use of the default model is your responsibility to clear. Review the [F5-TTS licence and model card](https://github.com/SWivid/F5-TTS) before any commercial deployment.

## What it does

- `run_socket_server.py`: F5-TTS inference server that accepts text over a TCP socket and returns synthesised audio.

The browser doesn't talk to this server directly — the agent connects to it over TCP and exposes a WebSocket proxy on its own `/tts` path. One `TTS_URL` setting on the agent drives both CLI-mode speech and browser playback.

## Prerequisites

- Python 3.12+
- NVIDIA GPU with CUDA 12.8
- Docker with the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) (for Docker usage)

The TTS server image is large (~28GB) due to CUDA libraries and the F5-TTS model. The first build will take a while.

## Voice files

You may optionally place voice reference files in `voices/`:

- `<name>.wav`: reference audio clip (maximum 12 seconds of clear speech, including 1 second of trailing silence)
- `<name>.txt`: transcript of the audio (optional but recommended; avoids an auto-transcription step on first use)

The container mounts this directory at `/tts/ref`. See **[F5-TTS](https://github.com/SWivid/F5-TTS)** and [`voices/README.md`](./voices/README.md) for details on creating voice samples.

> [!IMPORTANT]
> Only clone voices you have explicit permission to use! Generating speech in someone's likeness without their consent is unethical and may violate applicable laws.

## Configuration

```bash
cp .env.template .env
```

| Variable | Description |
|---|---|
| `TTS_VOICE` | Voice name to use (matches a `<name>.wav` in `voices/`). Leave empty for the default voice. |

## Docker (recommended)

From the monorepo root:

```bash
docker compose --profile tts up -d
```

### Port

| Port | Service |
|---|---|
| 9998 | TTS TCP socket API |

## Connecting to the agent

Set `TTS_URL` in `agent/.env` (or via the Settings UI's Speech tab):

```
TTS_URL="tts-server:9998"   # inside Docker network
TTS_URL="localhost:9998"    # from host machine
```

Leave `TTS_URL` unset to disable TTS entirely. The agent handles both CLI-mode speech (direct TCP) and browser playback (WebSocket proxy on its own `/tts` path) from this one setting.
