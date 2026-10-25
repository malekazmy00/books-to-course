#!/usr/bin/env python3
"""R7 audio QA on existing ``generate-mp3-track`` artifacts (read-only copies on local disk).

Usage:
    uv run python eval/suites/speech_v2/measure_audio.py --artifacts DIR --ffmpeg PATH --out report.json

``DIR`` holds ``<course>/generate-mp3-track/<key>/{track.mp3,track.json,track.txt}`` copied from
MinIO. MP3 decoding and the independent loudness cross-check use an ffmpeg binary (``--ffmpeg``;
not a project dependency: the new pipeline produces PCM WAV and never decodes MP3). For every
track: the b2c_speech.audio measurements and both loudness profiles, whether a single gain would
fix loudness, the MP3 stream format against T-AUD-01, the spoken-text violations of track.txt,
the language of the text, and ffmpeg ``ebur128`` integrated loudness / true peak for comparison.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
from pathlib import Path

from b2c_speech.audio import ACX, APP_SPEECH, check_track, measure, normalize_loudness, read_wav
from b2c_speech.text import speech_text_violations, wrong_language


def decode(ffmpeg: str, mp3: Path) -> bytes:
    return subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(mp3), "-f", "wav",
                           "-acodec", "pcm_s16le", "pipe:1"], check=True, capture_output=True).stdout


def stream_info(ffmpeg: str, mp3: Path) -> dict[str, object]:
    err = subprocess.run([ffmpeg, "-hide_banner", "-i", str(mp3)], capture_output=True, text=True).stderr
    m = re.search(r"Audio: (\w+)[^,]*, (\d+) Hz, (\w+), \w+, (\d+) kb/s", err)
    if not m:
        return {"raw": err[-300:]}
    return {"codec": m.group(1), "sample_rate": int(m.group(2)), "layout": m.group(3), "bitrate_kbps": int(m.group(4))}


def ebur128(ffmpeg: str, mp3: Path) -> dict[str, float | None]:
    err = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", str(mp3), "-filter_complex",
                          "ebur128=peak=true", "-f", "null", "-"], capture_output=True, text=True).stderr
    summary = err[err.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", summary)
    p = re.search(r"Peak:\s+(-?[\d.]+) dBFS", summary)
    return {"integrated_lufs": float(i.group(1)) if i else None, "true_peak_dbtp": float(p.group(1)) if p else None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", type=Path, required=True)
    ap.add_argument("--ffmpeg", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    tracks = []
    for mp3 in sorted(args.artifacts.glob("*/generate-mp3-track/*/track.mp3")):
        folder = mp3.parent
        meta = json.loads((folder / "track.json").read_text()) if (folder / "track.json").exists() else {}
        text = (folder / "track.txt").read_text() if (folder / "track.txt").exists() else ""
        pcm = read_wav(decode(args.ffmpeg, mp3))
        stats = measure(pcm)
        app, acx = check_track(pcm, APP_SPEECH, stats), check_track(pcm, ACX, stats)
        fixed, gain = normalize_loudness(pcm, APP_SPEECH)
        after = check_track(fixed, APP_SPEECH)
        info = stream_info(args.ffmpeg, mp3)
        mp3_ok = info.get("sample_rate") == 44100 and info.get("bitrate_kbps") == 192
        chars = len("".join(text.split()))
        language = meta.get("language", "ar")
        tracks.append({
            "key": folder.name[:8], "kind": meta.get("kind"), "language": language,
            "render_policy": meta.get("render_policy", "legacy"), "verification": (folder / "verification.json").exists(),
            "stream": info, "measure": stats.as_metrics(), "app_profile": {"passed": app.passed, "violations": list(app.violations)},
            "acx_profile": {"passed": acx.passed and mp3_ok, "violations": list(acx.violations) + ([] if mp3_ok else ["mp3_format"])},
            "app_after_single_gain": {"passed": after.passed, "violations": list(after.violations), "gain_db": round(gain, 2),
                                      "integrated_lufs": after.metrics["integrated_lufs"],
                                      "true_peak_dbtp": after.metrics["true_peak_dbtp"]},
            "ffmpeg_ebur128": ebur128(args.ffmpeg, mp3),
            "text": {"chars": chars, "chars_per_second": round(chars / stats.duration_s, 3) if stats.duration_s else None,
                     "violations": speech_text_violations(text), "wrong_language": wrong_language(text, language),
                     "digits": len(re.findall(r"[0-9٠-٩]", text)), "bracket_refs": len(re.findall(r"\[\d+\]", text)),
                     "latin_words": len(re.findall(r"[A-Za-z]{2,}", text))},
        })
    rates = [t["text"]["chars_per_second"] for t in tracks if t["text"]["chars_per_second"]]
    median = statistics.median(rates) if rates else None
    for t in tracks:
        r = t["text"]["chars_per_second"]
        t["rate_outlier"] = bool(median and r and abs(r / median - 1) > 0.35)
    deltas = [(t["measure"]["integrated_lufs"] - t["ffmpeg_ebur128"]["integrated_lufs"],
               t["measure"]["true_peak_dbtp"] - t["ffmpeg_ebur128"]["true_peak_dbtp"]) for t in tracks
              if t["ffmpeg_ebur128"]["integrated_lufs"] is not None]
    summary = {
        "tracks": len(tracks),
        "app_pass": sum(t["app_profile"]["passed"] for t in tracks),
        "acx_pass": sum(t["acx_profile"]["passed"] for t in tracks),
        "app_pass_after_single_gain": sum(t["app_after_single_gain"]["passed"] for t in tracks),
        "with_text_violations": sum(bool(t["text"]["violations"]) for t in tracks),
        "wrong_language": sum(t["text"]["wrong_language"] for t in tracks),
        "rate_outliers": sum(t["rate_outlier"] for t in tracks), "median_chars_per_second": median,
        "max_abs_delta_vs_ffmpeg": {"integrated_lu": round(max(abs(d[0]) for d in deltas), 3) if deltas else None,
                                    "true_peak_db": round(max(abs(d[1]) for d in deltas), 3) if deltas else None},
        "violation_counts": {name: sum(name in t["app_profile"]["violations"] for t in tracks)
                             for name in sorted({v for t in tracks for v in t["app_profile"]["violations"]})},
        "acx_violation_counts": {name: sum(name in t["acx_profile"]["violations"] for t in tracks)
                                 for name in sorted({v for t in tracks for v in t["acx_profile"]["violations"]})},
    }
    args.out.write_text(json.dumps({"summary": summary, "tracks": tracks}, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
