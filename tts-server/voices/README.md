# Voice Reference Files

Place voice reference files here for the F5-TTS server. The container
mounts this directory at `/tts/ref`.

## File convention

Each voice needs one file, with an optional companion:

- `<name>.wav`: reference audio clip (maximum 12 seconds of clear speech, including 1 second of trailing silence)
- `<name>.txt`: transcript of the audio (optional but recommended)

Providing a transcript avoids an automatic transcription step on first
use, which adds noticeable latency.

## Creating voice samples

See the [F5-TTS repository](https://github.com/SWivid/F5-TTS) for
guidance on recording and preparing reference audio.

## Ethics

**Only clone voices you have explicit permission to use!** Generating
speech in someone's likeness without their consent is unethical and
may violate applicable laws.
