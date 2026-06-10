const SAMPLE_RATE = 24000;
const TTS_TIMEOUT_MS = 30_000;
// User-facing wait for the WebSocket handshake. Decoupled from the
// socket's lifetime — when this fires we surface 'error' but keep the
// socket alive so a late `onopen` can still emit the recovered event.
const TTS_OPEN_TIMEOUT_MS = 5_000;
export const TTS_ERROR_EVENT_NAME = "swarpius:tts-error";
export const TTS_RECOVERED_EVENT_NAME = "swarpius:tts-recovered";
type AudioContextConstructor = new (contextOptions?: AudioContextOptions) => AudioContext;
type WindowWithWebkitAudio = Window & { webkitAudioContext?: AudioContextConstructor };

const emitTtsError = (error: unknown) => {
  if (typeof window === "undefined") {
    return;
  }
  const message =
    error instanceof Error
      ? error.message
      : typeof error === "string"
      ? error
      : "Unknown TTS error.";
  window.dispatchEvent(
    new CustomEvent(TTS_ERROR_EVENT_NAME, {
      detail: { message },
    }),
  );
};

const emitTtsRecovered = () => {
  if (typeof window === "undefined") {
    return;
  }
  window.dispatchEvent(new CustomEvent(TTS_RECOVERED_EVENT_NAME));
};

let sharedAudioContext: AudioContext | null = null;
let isBrowserSpeaking = false;

const getAudioContext = () => {
  if (typeof window === "undefined") {
    return null;
  }
  const typedWindow = window as WindowWithWebkitAudio;
  const AudioContextCtor =
    globalThis.AudioContext || typedWindow.webkitAudioContext;
  if (!AudioContextCtor) {
    return null;
  }
  if (!sharedAudioContext) {
    sharedAudioContext = new AudioContextCtor({ sampleRate: SAMPLE_RATE });
  }
  return sharedAudioContext;
};

/** Phase of an in-flight TTS utterance.
 *  - `sending`: WebSocket opened, server has not yet begun streaming audio.
 *  - `playing`: first audio chunk received; playback has started.
 *  - `complete`: utterance finished playing successfully.
 *  - `error`: utterance aborted by error or timeout. */
export type TtsStatusPhase = 'sending' | 'playing' | 'complete' | 'error';

export async function playServerTts(
  text: string,
  wsUrl: string,
  onStatus?: (phase: TtsStatusPhase) => void,
): Promise<void> {
  if (typeof window === "undefined") {
    return;
  }

  if (isBrowserSpeaking) {
    return;
  }

  const audioContext = getAudioContext();
  if (!audioContext) {
    throw new Error("Web Audio API is not supported in this browser.");
  }

  if (!wsUrl) {
    throw new Error("TTS is not configured.");
  }

  isBrowserSpeaking = true;
  onStatus?.('sending');

  try {
    return await new Promise<void>((resolve, reject) => {
      const websocket = new WebSocket(wsUrl);
      websocket.binaryType = "arraybuffer";

      let isClosed = false;
      let isSettled = false;
      let hardCappedOut = false;
      let hasEmittedPlaying = false;
      let nextStartTime = audioContext.currentTime;

      const closeSocket = () => {
        if (isClosed) return;
        isClosed = true;
        try { websocket.close(); } catch { /* ignore */ }
      };

      // See TTS_OPEN_TIMEOUT_MS — surfaces 'error' to the caller but
      // leaves the socket alive so a late `onopen` can still recover.
      const openTimer = window.setTimeout(() => {
        settleReject(new Error("TTS server did not respond."), { keepSocket: true });
      }, TTS_OPEN_TIMEOUT_MS);

      // Hard cap covering the full utterance lifetime — and a ceiling on
      // how long we keep the dangling late-open listener alive.
      const safetyTimer = window.setTimeout(() => {
        if (!isSettled) {
          settleReject(new Error("TTS playback timed out."));
        } else {
          hardCappedOut = true;
          closeSocket();
        }
      }, TTS_TIMEOUT_MS);

      const settleResolve = () => {
        if (isSettled) return;
        isSettled = true;
        window.clearTimeout(openTimer);
        window.clearTimeout(safetyTimer);
        closeSocket();
        onStatus?.('complete');
        emitTtsRecovered();
        resolve();
      };

      const settleReject = (
        error: unknown,
        options: { keepSocket?: boolean } = {},
      ) => {
        if (isSettled) return;
        isSettled = true;
        window.clearTimeout(openTimer);
        if (options.keepSocket) {
          // Leave safetyTimer running as the upper bound on the late-open
          // listener; do NOT close the socket.
        } else {
          window.clearTimeout(safetyTimer);
          closeSocket();
        }
        onStatus?.('error');
        emitTtsError(error);
        reject(error);
      };

      const settleAfterPlayback = () => {
        const remainingMs = Math.max(0, (nextStartTime - audioContext.currentTime) * 1000);
        window.setTimeout(settleResolve, remainingMs);
      };

      websocket.onopen = () => {
        if (isSettled) {
          // Late open: emit recovered (unless the hard cap fired
          // first) and close the socket without sending text.
          if (!hardCappedOut) emitTtsRecovered();
          window.clearTimeout(safetyTimer);
          closeSocket();
          return;
        }
        window.clearTimeout(openTimer);
        try {
          websocket.send(text);
        } catch (error) {
          settleReject(error);
        }
      };

      websocket.onmessage = async (event: MessageEvent) => {
        if (isSettled) {
          // We never sent the text after late-open, so the server
          // shouldn't have anything to send. Tear down on any terminal
          // marker just in case.
          if (typeof event.data === "string" && (event.data === "END" || event.data === "ERROR")) {
            window.clearTimeout(safetyTimer);
            closeSocket();
          }
          return;
        }
        try {
          if (typeof event.data === "string") {
            if (event.data === "END") {
              settleAfterPlayback();
              return;
            }
            if (event.data === "ERROR") {
              settleReject(new Error("TTS server unavailable."));
              return;
            }
            return;
          }

          let buffer: ArrayBuffer;

          if (event.data instanceof ArrayBuffer) {
            buffer = event.data as ArrayBuffer;
          } else if (event.data instanceof Blob) {
            buffer = await (event.data as Blob).arrayBuffer();
          } else {
            return;
          }

          const floatData = new Float32Array(buffer);

          if (!hasEmittedPlaying) {
            hasEmittedPlaying = true;
            onStatus?.('playing');
          }

          const audioBuffer = audioContext.createBuffer(
            1,
            floatData.length,
            SAMPLE_RATE
          );
          audioBuffer.getChannelData(0).set(floatData);

          const source = audioContext.createBufferSource();
          source.buffer = audioBuffer;
          source.connect(audioContext.destination);

          // Schedule sequential playback to avoid overlapping chunks which
          // can sound like static. If we've fallen behind, snap to
          // currentTime.
          if (nextStartTime < audioContext.currentTime) {
            nextStartTime = audioContext.currentTime;
          }
          source.start(nextStartTime);
          nextStartTime += audioBuffer.duration;
        } catch (error) {
          settleReject(error);
        }
      };

      websocket.onerror = () => {
        if (isSettled) {
          window.clearTimeout(safetyTimer);
          closeSocket();
          return;
        }
        settleReject(new Error("Error in TTS WebSocket connection."));
      };

      websocket.onclose = () => {
        window.clearTimeout(openTimer);
        window.clearTimeout(safetyTimer);
        if (!isSettled) {
          settleAfterPlayback();
        }
      };
    });
  } finally {
    isBrowserSpeaking = false;
  }
}
