"""TCP socket server for F5-TTS streaming inference.

Wraps F5-TTS's built-in socket_server with voice selection and
sensible defaults for containerised deployment.
"""

import argparse
import logging
from importlib.resources import files
from pathlib import Path

import torch
from f5_tts.socket_server import TTSStreamingProcessor, start_server

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "F5TTS_v1_Base"
_DEFAULT_REF_AUDIO = str(files("f5_tts").joinpath("infer/examples/basic/basic_ref_en.wav"))
_VOICE_DIR = Path("/tts/ref")


def _resolve_checkpoint(model: str) -> str:
    """Download the default checkpoint via HuggingFace Hub."""
    from huggingface_hub import hf_hub_download

    return str(hf_hub_download(
        repo_id="SWivid/F5-TTS",
        filename=f"{model}/model_1250000.safetensors",
    ))


def _resolve_voice(voice: str) -> tuple[str, str]:
    """Resolve a voice name to (ref_audio, ref_text) paths.

    Returns the default reference audio if the voice wav is not found.
    Transcript is optional — F5-TTS auto-transcribes when empty.
    """
    wav_file = _VOICE_DIR / f"{voice}.wav"
    txt_file = _VOICE_DIR / f"{voice}.txt"

    if wav_file.is_file():
        ref_audio = str(wav_file)
        log.info("Using voice audio: %s", wav_file)
    else:
        ref_audio = _DEFAULT_REF_AUDIO
        log.warning("Voice audio not found: %s — falling back to default", wav_file)

    ref_text = ""
    if txt_file.is_file():
        ref_text = txt_file.read_text().strip()
        log.info("Using voice transcript: %s", txt_file)
    else:
        log.info("No transcript for voice '%s' — F5-TTS will auto-transcribe", voice)

    return ref_audio, ref_text


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="F5-TTS streaming socket server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9998)
    parser.add_argument("--model", default=_DEFAULT_MODEL)
    parser.add_argument("--ckpt_file", default="", help="Model checkpoint path (auto-downloaded if empty)")
    parser.add_argument("--vocab_file", default="")
    parser.add_argument("--ref_audio", default="", help="Reference audio path (overridden by --voice)")
    parser.add_argument("--ref_text", default="", help="Reference transcript (overridden by --voice)")
    parser.add_argument("--voice", default=None, help="Voice name — looks for <name>.wav and optional <name>.txt in /tts/ref/")
    parser.add_argument("--device", default=None)
    parser.add_argument("--dtype", default="float32", choices=["float16", "float32"])
    args = parser.parse_args()

    # Resolve voice (overrides ref_audio / ref_text)
    if args.voice:
        args.ref_audio, args.ref_text = _resolve_voice(args.voice)

    if not args.ref_audio:
        args.ref_audio = _DEFAULT_REF_AUDIO

    if not args.ckpt_file:
        args.ckpt_file = _resolve_checkpoint(args.model)

    dtype = torch.float16 if args.dtype == "float16" else torch.float32

    processor = TTSStreamingProcessor(
        model=args.model,
        ckpt_file=args.ckpt_file,
        vocab_file=args.vocab_file,
        ref_audio=args.ref_audio,
        ref_text=args.ref_text,
        device=args.device,
        dtype=dtype,
    )

    log.info("Starting TTS socket server on %s:%d", args.host, args.port)
    start_server(args.host, args.port, processor)


if __name__ == "__main__":
    main()
