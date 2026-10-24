#!/usr/bin/env python3
"""Generate one bounded Supertonic 3 Arabic clip and encode a two-pass MP3."""
from __future__ import annotations

import json
import hashlib
import math
import re
import subprocess
import time
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import soundfile as sf
from b2c_speech.validation import AudioMeasurements, check_audio_measurements
from supertonic import TTS

TEXT = "\u0647\u0630\u0627 \u0627\u062e\u062a\u0628\u0627\u0631 \u0635\u0648\u062a \u0644\u0644\u0645\u0642\u0631\u0631 \u0627\u0644\u062a\u0639\u0644\u064a\u0645\u064a."

def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, text=True, capture_output=True)

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=56)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    wav_path = args.output_dir / "supertonic3-ar-smoke.wav"
    mp3_path = args.output_dir / "supertonic3-ar-smoke.mp3"
    report_path = args.output_dir / "supertonic3-ar-smoke.json"
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

    started = time.monotonic()
    tts = TTS(model="supertonic-3", model_dir=args.model_dir, auto_download=False,
              intra_op_num_threads=args.threads)
    style = tts.get_voice_style("M1")
    audio, durations = tts.synthesize(
        TEXT, voice_style=style, total_steps=8, speed=1.05, lang="ar", verbose=False
    )
    inference_s = time.monotonic() - started
    waveform = np.asarray(audio).squeeze()
    duration_s = len(waveform) / tts.sample_rate
    sf.write(wav_path, waveform, tts.sample_rate, subtype="PCM_16")

    target_i, target_tp, target_lra = -20.0, -3.0, 7.0
    pass1 = run([ffmpeg, "-hide_banner", "-nostats", "-i", str(wav_path),
                 "-af", f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}:print_format=json",
                 "-f", "null", "-"])
    matches = {k: re.search(rf'"{k}"\s*:\s*"([^"]+)"', pass1.stderr)
               for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
    missing = [k for k, v in matches.items() if v is None]
    if missing:
        raise RuntimeError(f"ffmpeg first-pass loudnorm lacked fields: {missing}")
    values = {k: v.group(1) for k, v in matches.items() if v}
    filter2 = (
        f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}:"
        f"measured_I={values['input_i']}:measured_TP={values['input_tp']}:"
        f"measured_LRA={values['input_lra']}:measured_thresh={values['input_thresh']}:"
        f"offset={values['target_offset']}:linear=true:print_format=json,aresample=44100"
    )
    run([ffmpeg, "-hide_banner", "-y", "-i", str(wav_path), "-af", filter2,
         "-ac", "1", "-ar", "44100", "-codec:a", "libmp3lame",
         "-b:a", "192k", "-minrate", "192k", "-maxrate", "192k", "-bufsize", "256k",
         "-write_xing", "0", str(mp3_path)])

    raw = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(mp3_path),
         "-f", "f32le", "-ac", "1", "-ar", "44100", "pipe:1"],
        check=True, capture_output=True,
    ).stdout
    decoded = np.frombuffer(raw, dtype="<f4")
    rms = float(20 * math.log10(max(float(np.sqrt(np.mean(decoded.astype(np.float64) ** 2))), 1e-12)))
    peak = float(20 * math.log10(max(float(np.max(np.abs(decoded))), 1e-12)))
    meta = run([ffmpeg, "-hide_banner", "-i", str(mp3_path), "-f", "null", "-"])
    probe = re.search(
        r"Audio: mp3.*?44100 Hz, mono, fltp, 192 kb/s", meta.stderr, flags=re.S
    )
    astats = run([ffmpeg, "-hide_banner", "-i", str(mp3_path),
                  "-af", "astats=metadata=1:reset=0", "-f", "null", "-"])
    noise_match = re.search(r"Noise floor dB:\s*([-0-9.]+)", astats.stderr)
    noise_floor = float(noise_match.group(1)) if noise_match else None
    measurements = AudioMeasurements(
        rms_dbfs=rms, peak_dbfs=peak, noise_floor_dbfs=noise_floor or 0.0,
        sample_rate=44100, codec="mp3", bitrate_bps=192000, cbr=bool(probe),
    )
    audio_check = check_audio_measurements(measurements)
    result = {
        "model": "Supertone/supertonic-3",
        "model_revision": "724fb5abbf5502583fb520898d45929e62f02c0b",
        "model_license": "OpenRAIL-M",
        "package": "supertonic==1.3.1",
        "voice": "M1 (built-in non-cloned style)",
        "lang": "ar",
        "text": TEXT,
        "sample_rate_input": tts.sample_rate,
        "sample_rate_output_hz": 44100,
        "duration_s": duration_s,
        "inference_s": inference_s,
        "rtf": inference_s / duration_s,
        "steps": 8,
        "output_codec_bitrate_probe_match": bool(probe),
        "rms_dbfs_decoded_mp3": rms,
        "sample_peak_dbfs_decoded_mp3": peak,
        "noise_floor_dbfs": noise_floor,
        "noise_floor_probe": "ffmpeg astats over fully decoded MP3" if noise_floor is not None else None,
        "acx_measurement_check": {
            "passed": audio_check.passed,
            "violations": audio_check.violations,
            "cbr_bitrate_header_pattern": bool(probe),
        },
        "mp3_sha256": hashlib.sha256(mp3_path.read_bytes()).hexdigest(),
        "loudnorm_pass1": values,
        "wav": str(wav_path),
        "mp3": str(mp3_path),
    }
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in (
        "status", "voice", "sample_rate_input", "sample_rate_output_hz", "duration_s",
        "inference_s", "rtf", "output_codec_bitrate_probe_match", "rms_dbfs_decoded_mp3",
        "sample_peak_dbfs_decoded_mp3", "noise_floor_dbfs",
        "acx_measurement_check", "mp3_sha256", "mp3"
    ) if k in result}, ensure_ascii=False))

if __name__ == "__main__":
    main()
