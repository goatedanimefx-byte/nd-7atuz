#!/usr/bin/env python3
"""
yt_auto :: clip packer

Wraps one production batch (script + voiceover) into the input zip the Colab
notebook expects:

    colab_input/clip_N.zip
    └── clip.json    (title, duration, voice/rate/pitch, beats, captions, vo_text)
    └── vo.mp3       (the batch voiceover, copied)
    └── bgm.mp3      (optional — only if production/bgm/batch_N.mp3 exists)

Captions are phrase-synced (whisper segment timing) when faster-whisper is
available, otherwise per-beat. Re-run anytime; output is deterministic.

Usage:
    python3 pack_clip.py            # pack batch 1
    python3 pack_clip.py 3          # pack batch 3
    python3 pack_clip.py 1 --no-whisper   # skip transcription (phrase-sync only)
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROD = HERE / "production"
SCRIPTS = PROD / "scripts"
AUDIO = PROD / "audio"
OUTBOX = HERE / "colab_input"

VOICE = {1: "en-US-ChristopherNeural", 2: "en-US-ChristopherNeural",
         3: "en-US-GuyNeural", 4: "en-US-ChristopherNeural",
         5: "en-US-GuyNeural"}
PITCH, RATE = "-4Hz", "+5%"


def md_duration(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "default=nw=1:nk=1", str(p)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def extract_vo(md: str) -> str:
    m = re.search(r"^##\s+VOICEOVER SCRIPT.*?$(.*?)^---\s*$", md,
                  re.MULTILINE | re.DOTALL)
    if not m:
        raise ValueError("no VOICEOVER SCRIPT section")
    body = m.group(1)
    lines = body.split("\n")
    start = 0
    for i, line in enumerate(lines):
        if line.strip() and not line.lstrip().startswith(">"):
            start = i
            break
    body = "\n".join(lines[start:])
    body = re.sub(r"^\s*>\s?", "", body, flags=re.MULTILINE)
    body = body.replace("//", " ")
    body = re.sub(r"[*_`#]", "", body)
    body = re.sub(r"^\s*[-|]\s*$", "", body, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", body).strip()
    text = text.replace("—", ", ").replace("–", ", ")
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    return text


BEAT_ROW = re.compile(
    r"^\|\s*(\d+)\s*\|\s*(\d+):(\d+)\s*[–-]\s*(\d+):(\d+)\s*\|\s*"
    r"(.*?)\s*\|\s*(?:\*{1,2})?(.*?)(?:\*{1,2})?\s*\|?\s*$")


def parse_beats(md: str):
    block = re.search(r"^##\s+ANIMATION BEAT SHEET.*?(?:^##|\Z)",
                      md, re.MULTILINE | re.DOTALL)
    beats = []
    if not block:
        return beats
    for row in block.group(0).splitlines():
        m = BEAT_ROW.match(row)
        if not m:
            continue
        _, sm, ss, em, es = m[1], int(m[2]), int(m[3]), int(m[4]), int(m[5])
        action = m[6].strip()
        onscreen = re.sub(r"[\*`]", "", m[7]).strip()
        beats.append({
            "start": sm * 60 + ss,
            "end": em * 60 + es,
            "text": onscreen or "…",
            "shot": action,
        })
    return beats


def whisper_captions(vo_mp3: Path, dur: float):
    """Phrase-synced captions via faster-whisper (base.en, int8), using
    segment boundaries — word_timestamps on this 48kbps/24kHz mono delivery
    are too sparse to trust word-by-word. Returns None if unavailable; the
    caller then falls back to per-beat caption windows."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    try:
        model = WhisperModel("base.en", device="cpu", compute_type="int8")
        segs, _ = model.transcribe(str(vo_mp3))
        caps = []
        for s in segs:
            text = " ".join(s.text.split())
            if text:
                caps.append({"start": round(float(s.start), 3),
                             "end": min(round(float(s.end), 3), dur),
                             "text": text})
        return caps or None
    except Exception as e:
        print("whisper failed, phrase-sync fallback:", e)
        return None


def pack(n: int, use_whisper: bool) -> Path:
    md_path = SCRIPTS / ("batch_%d.md" % n)
    mp3 = AUDIO / ("batch_%d.mp3" % n)
    if not md_path.exists() or not mp3.exists():
        raise SystemExit("missing batch_%d assets (need script + mp3)" % n)

    title = md_path.read_text(encoding="utf-8").splitlines()[0]
    title = re.sub(r"^#\s*", "", title).strip()
    dur = md_duration(mp3)

    clip = {
        "title": title or ("Batch %d" % n),
        "width": 1080, "height": 1920, "fps": 30,
        "duration": dur,
        "voice": VOICE.get(n, "en-US-ChristopherNeural"),
        "rate": RATE, "pitch": PITCH,
        "accent": "#ff4b3a",
        "beats": parse_beats(md_path.read_text(encoding="utf-8")),
        "vo_text": extract_vo(md_path.read_text(encoding="utf-8")),
    }

    caps = whisper_captions(mp3, dur) if use_whisper else None
    if caps:
        clip["captions"] = caps
        print("captions: whisper phrase-sync (%d)" % len(caps))
    else:
        print("captions: phrase-sync (per beat)")

    OUTBOX.mkdir(exist_ok=True)
    out = OUTBOX / ("clip_%d.zip" % n)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("clip.json", json.dumps(clip, indent=2))
        z.write(mp3, "vo.mp3")
        bgm = PROD / "bgm" / ("batch_%d.mp3" % n)
        if bgm.exists():
            z.write(bgm, "bgm.mp3")
    print("wrote %s (%.1f KB, %d beats, %.1fs)" % (
        out, out.stat().st_size / 1024, len(clip["beats"]), dur))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("n", type=int, nargs="?", default=1)
    ap.add_argument("--no-whisper", action="store_true")
    args = ap.parse_args()
    pack(args.n, not args.no_whisper)