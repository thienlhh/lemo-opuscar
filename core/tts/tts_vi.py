"""VieNeu-TTS Vietnamese voice service (v3 Turbo, 48 kHz on-device neural TTS):
    .venv/bin/python core/tts/tts_vi.py lines.json out_dir [--voice "Hải Đăng"] [--speed 1.0] [--sr 24000] [--list-voices]
lines.json = [{"id":..., "text":..., "voice":"Hải Đăng", "say":"...", "ref_audio":"...", "speed":1.0}, ...]
  "voice" (optional) = built-in preset voice name (default "Hải Đăng").
                       Run with --list-voices to view all 25 preset voices across Northern, Central, and Southern regions.
  "ref_audio" (optional) = path to 3–8s reference audio clip for instant voice cloning.
  "say" (optional) = the text actually sent to be spoken, default "text" (for numbers, abbreviations, or emotion tags
                     like [cười], [thở dài], [hắng giọng]; subtitles still use "text").
  "speed" (optional) = playback rate multiplier (default 1.0).

Outputs match tts.py and tts_zh.py:
  out_dir/<id>.wav (default 24 kHz mono for pipeline consistency, leading/trailing silence trimmed; pass --sr 48000 for 48 kHz).
  out_dir/dur.json (durations in seconds).
  Audio files are cached in out_dir/.cache/; unchanged lines are not re-synthesized.

Requires `vieneu` (pip install vieneu) and models from https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo
(auto-downloaded on first use to Hugging Face cache).
"""
import sys, json, os, re, hashlib, argparse
from pathlib import Path
import numpy as np
import soundfile as sf
import soxr

DEFAULT_VOICE = "Hải Đăng"
DEFAULT_SR = 24000
NATIVE_SR = 48000


def get_vieneu_instance(mode="v3turbo", backbone_repo=None, backend="auto", precision="fp32"):
    """Import and instantiate Vieneu, checking installed packages and local fallback."""
    try:
        from vieneu import Vieneu
    except ImportError:
        # Check if local VieNeu-TTS repo exists
        repo_hint = os.environ.get("VIENEU_PATH") or os.path.expanduser("~/VieNeu-TTS")
        if os.path.isdir(os.path.join(repo_hint, "src")):
            sys.path.insert(0, os.path.join(repo_hint, "src"))
            try:
                from vieneu import Vieneu
            except ImportError:
                sys.exit(
                    "tts_vi.py: 'vieneu' package not found.\n"
                    "  Install it with:\n"
                    "    uv pip install vieneu\n"
                    "  Or ensure /Users/thienlhh/VieNeu-TTS is accessible."
                )
        else:
            sys.exit(
                "tts_vi.py: 'vieneu' package not found.\n"
                "  Install it with:\n"
                "    uv pip install vieneu\n"
                "  Or set VIENEU_PATH=/path/to/VieNeu-TTS."
            )
    kwargs = {"mode": mode, "backend": backend, "precision": precision}
    if backbone_repo:
        kwargs["backbone_repo"] = backbone_repo
    return Vieneu(**kwargs)


def print_voices_and_exit(engine):
    try:
        voices = engine.list_preset_voices()
        print(f"\n🎙️  {len(voices)} built-in VieNeu-TTS preset voices available:")
        for label, vid in voices:
            print(f"  - {vid:15s} ({label})")
        sys.exit(0)
    except Exception as e:
        sys.exit(f"tts_vi.py: failed to list preset voices: {e}")


def file_hash(path):
    if not path or not os.path.isfile(path):
        return ""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()[:12]


def trim_silence(y, sr):
    max_val = float(np.abs(y).max()) if len(y) else 0.0
    if max_val < 1e-4:
        return y
    nz = np.where(np.abs(y) > max_val * 0.02)[0]
    if len(nz):
        return y[max(0, nz[0] - int(0.03 * sr)): nz[-1] + int(0.10 * sr)]
    return y


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("lines", nargs="?", help="Path to lines.json")
    ap.add_argument("out_dir", nargs="?", help="Output directory for generated WAVs and dur.json")
    ap.add_argument("--voice", default=DEFAULT_VOICE, help=f"Default voice name (default: '{DEFAULT_VOICE}')")
    ap.add_argument("--speed", type=float, default=1.0, help="Playback speed multiplier (default: 1.0)")
    ap.add_argument("--sr", type=int, default=DEFAULT_SR, help=f"Target sample rate (default: {DEFAULT_SR}, or 48000)")
    ap.add_argument("--ref-audio", default=None, help="Reference audio path for voice cloning")
    ap.add_argument("--model", default=None, help="Hugging Face repo or local path for v3 Turbo")
    ap.add_argument("--backend", default="auto", choices=["auto", "onnx", "pytorch"], help="Backend engine: 'onnx' (torch-free CPU), 'pytorch' (CUDA), or 'auto'")
    ap.add_argument("--precision", default="fp32", choices=["fp32", "int8"], help="ONNX precision: 'fp32' (default, high fidelity) or 'int8' (faster)")
    ap.add_argument("--list-voices", action="store_true", help="List all 25 preset voices and exit")

    args = ap.parse_args()

    if args.list_voices:
        engine = get_vieneu_instance(backbone_repo=args.model, backend=args.backend, precision=args.precision)
        print_voices_and_exit(engine)

    if not args.lines or not args.out_dir:
        ap.print_help()
        sys.exit(2)

    if not os.path.isfile(args.lines):
        sys.exit(f"tts_vi.py: input file not found: {args.lines}")

    with open(args.lines, "r", encoding="utf-8") as f:
        try:
            lines = json.load(f)
        except json.JSONDecodeError as e:
            sys.exit(f"tts_vi.py: invalid JSON in {args.lines}: {e}")

    engine = get_vieneu_instance(backbone_repo=args.model, backend=args.backend, precision=args.precision)
    available_voices = {vid: label for label, vid in engine.list_preset_voices()}

    # Validate lines
    for L in lines:
        if "id" not in L:
            sys.exit("tts_vi.py: each line must contain an 'id' field.")
        if "text" not in L:
            sys.exit(f"tts_vi.py: line '{L.get('id')}' is missing 'text'.")
        v = L.get("voice", args.voice)
        ref = L.get("ref_audio", args.ref_audio)
        if not ref and v not in available_voices:
            sys.exit(
                f"tts_vi.py: line '{L['id']}': unknown voice '{v}'.\n"
                f"  Available: {', '.join(sorted(available_voices.keys()))}\n"
                f"  Or run with --list-voices to view details."
            )
        if ref and not os.path.isfile(ref):
            sys.exit(f"tts_vi.py: line '{L['id']}': reference audio file not found: '{ref}'")

    cache_dir = os.path.join(args.out_dir, ".cache")
    os.makedirs(cache_dir, exist_ok=True)
    os.makedirs(args.out_dir, exist_ok=True)

    # Clean up interrupted partial files
    for n in os.listdir(cache_dir):
        if n.endswith(".part"):
            try:
                os.remove(os.path.join(cache_dir, n))
            except OSError:
                pass

    dur = {}
    for L in lines:
        line_id = str(L["id"])
        text = L["text"]
        say = L.get("say", text)
        voice = L.get("voice", args.voice)
        speed = float(L.get("speed", args.speed))
        ref_audio = L.get("ref_audio", args.ref_audio)

        # Cache key based on input parameters
        ref_h = file_hash(ref_audio) if ref_audio else ""
        cache_key = hashlib.sha1(
            json.dumps([voice, speed, args.sr, say, ref_h], ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]

        cached_wav = os.path.join(cache_dir, f"{cache_key}.wav")
        part_wav = cached_wav + ".part"

        if os.path.exists(cached_wav):
            y, sr = sf.read(cached_wav, dtype="float32")
        else:
            infer_kwargs = {}
            if ref_audio:
                infer_kwargs["ref_audio"] = ref_audio
            else:
                infer_kwargs["voice"] = voice

            try:
                y = engine.infer(say, **infer_kwargs)
            except Exception as e:
                sys.exit(f"tts_vi.py: failed during synthesis for line '{line_id}': {e}")

            if getattr(y, "ndim", 1) > 1:
                y = y.mean(axis=1)
            y = np.asarray(y, dtype=np.float32)

            sr = NATIVE_SR

            # Time-stretch if speed != 1.0
            if abs(speed - 1.0) > 0.01:
                try:
                    import librosa
                    y = librosa.effects.time_stretch(y, rate=speed)
                except Exception as e:
                    print(f"tts_vi.py: warning: could not time-stretch line '{line_id}': {e}", file=sys.stderr)

            # Resample to target sample rate
            if args.sr != sr:
                y = soxr.resample(y, sr, args.sr, quality="HQ")
                sr = args.sr

            # Silence trimming
            y = trim_silence(y, sr)

            if len(y) == 0 or float(np.abs(y).max()) < 1e-4:
                print(f"tts_vi.py: warning: line '{line_id}' is silent", file=sys.stderr)

            # Save to cache with atomic rename
            sf.write(part_wav, y, sr, format="WAV")
            os.replace(part_wav, cached_wav)

        dest_wav = os.path.join(args.out_dir, f"{line_id}.wav")
        sf.write(dest_wav, y, sr)
        duration_s = round(len(y) / sr, 3)
        dur[line_id] = duration_s
        print(line_id, duration_s, text)

    with open(os.path.join(args.out_dir, "dur.json"), "w", encoding="utf-8") as f:
        json.dump(dur, f, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
