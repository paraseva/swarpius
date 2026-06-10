# TTS adapters

Swarpius comes ready to make use of any available F5-TTS server for speech synthesis. For those who want to set up a server locally, we include a Dockerfile that pulls in the [code](https://github.com/SWivid/F5-TTS) from the official GitHub repository and builds an image for hosts with an NVIDIA GPU and CUDA 12.8. When running the image as a service, both the frontend and the agent in CLI mode can connect to it. You can swap this for any TTS backend — a different local model (Piper, Coqui, XTTS) or a cloud API (OpenAI TTS, ElevenLabs, Azure Speech, Google Cloud TTS) — by implementing the small wire protocol below.

## Architecture

The TTS server speaks one protocol: a raw TCP socket (default port 9998). The agent connects to it directly for CLI-mode speech, and also hosts an in-process WebSocket→TCP proxy so the browser can stream audio without running a second network service.

```
                           agent process (port 8080)
                        ┌──────────────────────────────┐
  browser ── ws /tts ──▶│  proxy (agent/tts/proxy.py)  │── tcp :9998 ──┐
                        └──────────────────────────────┘                │
                                                                        ▼
  agent CLI ──────────────────── tcp :9998 ─────────────────▶  TTS engine (F5-TTS today)
```

- **Frontend** (`web-client/src/tts.tsx`) opens a WebSocket per utterance to the agent's `/tts` path — the *same host and port as the chat WebSocket* (8080 by default). The URL is derived at runtime from the chat WS URL; there is nothing to configure.
- **Agent CLI mode** (`agent/tts/tts.py::speak_text`) opens a raw TCP socket per utterance to the TTS server (`TTS_URL`, default port 9998).
- **The WS→TCP proxy** (`agent/tts/proxy.py`) runs *inside the agent process* and is served on the agent's own port at the `/tts` path. It's a thin adapter with no TTS-specific knowledge — if you replicate the TCP protocol correctly, the browser path keeps working without changes.

**So to integrate a new backend, you only need to implement the TCP protocol on port 9998.** Both the CLI and the browser path then work automatically.

## TCP wire protocol (port 9998)

Per-utterance TCP session:

1. Client opens a TCP connection.
2. Client sends the utterance as UTF-8 bytes and closes the write half (or keeps it open — the server reads until it has a complete utterance; in practice a single `write(text)` followed by `drain` is enough).
3. Server streams back audio in chunks of ≤8192 bytes.
4. The server appends the ASCII sentinel `END` (three bytes `b"END"`) to the final chunk to signal completion.
5. Server closes the connection.

**Audio format**:
- Raw PCM, float32 little-endian
- 24 000 Hz
- Mono (1 channel)
- No container, no WAV header — just the sample stream

The client concatenates chunks, strips the trailing `END`, and plays the resulting float32 samples directly (the frontend uses Web Audio API's `AudioContext`; the CLI uses PyAudio).

## Browser path (WebSocket at `/tts`, automatic)

You don't need to implement this if you implement the TCP protocol above — the agent's built-in proxy (`agent/tts/proxy.py`) bridges between them. Documented here for completeness.

Per-utterance WebSocket session (browser ⇄ the agent's `/tts` path):

1. Client opens a WebSocket to the agent's `/tts` path (same host and port as chat).
2. Client sends a single text message containing the utterance.
3. Server streams back binary frames, each containing PCM float32 @ 24 kHz samples (same format as the TCP chunks, but with the trailing `b"END"` stripped from the final chunk by the proxy).
4. When the utterance is complete, the server sends a single text frame `"END"` (or `"ERROR"` on failure) before closing the socket.

The frontend distinguishes binary frames (audio) from text frames (`"END"` / `"ERROR"`) by checking `typeof event.data === "string"`. The text-frame sentinel lets the client release the WS resource cleanly without waiting on the socket-close event.

## Swapping the backend

### Local model (Piper, Coqui, XTTS, etc.)

Replace `tts-server/run_socket_server.py` with your own TCP server that:

1. Accepts connections on its TCP port (default 9998; set via the `--port` flag).
2. Reads the request text.
3. Runs inference, emitting PCM float32 @ 24 kHz chunks as they become available.
4. Appends `b"END"` to the final chunk before closing.

Most Python TTS libraries output audio as a numpy array. Convert with `.astype(numpy.float32).tobytes()` before writing to the socket. If your model outputs at a different sample rate, resample to 24 kHz on the server side — don't change the protocol.

The agent's `/tts` proxy is part of the agent and needs no changes; only the TTS engine swaps.

### Cloud TTS (OpenAI, ElevenLabs, Azure, GCP)

Cloud TTS APIs typically return a complete audio file (MP3, WAV, OGG) via a single HTTP response rather than streaming PCM. Write a TCP shim that:

1. Listens on port 9998.
2. For each connection, reads the text.
3. Calls the cloud API and receives the audio file bytes.
4. Decodes to PCM float32 @ 24 kHz (resample if needed — most cloud TTS emits 16 kHz / 22.05 kHz / 24 kHz).
5. Writes the PCM bytes to the socket in ≤8192-byte chunks, ending with `b"END"`.

Buffering the whole response before streaming is fine — latency is dominated by the cloud round-trip, not the chunk-by-chunk delivery from your shim. A minimal Python shim is ~80 lines.

**Caveat**: cloud TTS typically authenticates with an API key via an HTTP header. Keep the secret in a container env var (e.g. `OPENAI_API_KEY`) and reference it from the shim. Don't expose it to the frontend or agent — they only see the local TCP port.

## Env vars

Swarpius needs a single setting to reach the TTS server:

- `TTS_URL` (on the agent): the TTS server address as a scheme-less `host:port` (TCP) — e.g. `tts-server:9998` inside Docker, or `localhost:9998` when running from source. This one value drives **both** CLI-mode speech and the browser `/tts` proxy. Leave unset to disable TTS entirely.

There is nothing TTS-specific to configure on the browser side — the frontend derives its `/tts` WebSocket URL from the chat WebSocket URL at runtime (same host and port).

On the TTS-server side, the provided server takes `--host` / `--port` argparse flags (default `127.0.0.1:9998`) plus the optional `TTS_VOICE` env var (mapped to `--voice` by Docker Compose). The compose service passes `--host 0.0.0.0` so the agent can reach it over the Docker network. A custom adapter can use whatever configuration it likes, as long as it listens on the TCP port that the agent's `TTS_URL` points at.

## Gotchas

- **Sample rate mismatch** is the most common adapter bug. The clients hard-code 24 000 Hz decoding; if your shim emits 22 050 or 16 000, audio will play at a pitched-up or slow rate. Resample before sending.
- **Sentinel placement**: the `b"END"` bytes must be in the *last* chunk, not a separate final write. Both consumers of the TCP stream — the agent's WS proxy and the Python CLI client — check `data.endswith(b"END")` on each received chunk to decide when to stop. (The proxy then strips the suffix and sends a separate text-frame `"END"` to the browser; the browser doesn't see the binary sentinel itself.)
- **Float32 byte order**: little-endian. Python's `numpy.ndarray.tobytes()` with default `byteorder='='` matches little-endian on x86/ARM. Be explicit if you're ever on a big-endian target.
- **Timeouts**: the frontend has a 30-second per-utterance timeout (`TTS_TIMEOUT_MS` in `tts.tsx`). Cloud adapters for long utterances may need to stream the first chunk as early as possible to avoid the timeout.
