#!/usr/bin/env python3
"""
yt_auto :: voiceover synthesis pipeline

Reads production/scripts/batch_N.md, extracts the clean VOICEOVER block, and
synthesizes production/audio/batch_N.mp3 via edge-tts.

Prosody is pinned to agency spec:
    pitch  -4%  ->  -4Hz   (edge-tts validates pitch as ^[+-]\\d+Hz$, see
                           edge_tts/data_classes.py:76 — percentage is rejected)
    rate   +5%  ->  +5%    (natively supported)
    volume default

Usage:
    python3 tts_build.py            # build all, verify 45-60s
    python3 tts_build.py --force    # rebuild even if mp3 exists
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import edge_tts
except ImportError:
    sys.exit("edge-tts missing. Install with:  pip install edge-tts")

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE / "scripts"
AUDIO = HERE / "audio"

VOICES = {
    1: "en-US-ChristopherNeural",
    2: "en-US-ChristopherNeural",
    3: "en-US-GuyNeural",
    4: "en-US-ChristopherNeural",
    5: "en-US-GuyNeural",
}

PITCH = "-4Hz"
RATE = "+5%"
VOLUME = "+0%"

MIN_SEC, MAX_SEC = 45.0, 60.0


def extract_vo(md: str) -> str:
    """Pull the clean VO block out of a batch markdown file."""
    m = re.search(
        r"^##\s+VOICEOVER SCRIPT.*?$(.*?)^---\s*$",
        md,
        re.MULTILINE | re.DOTALL,
    )
    if not m:
        raise ValueError("no VOICEOVER SCRIPT section found")

    body = m.group(1)

    # Drop the director's-note blockquote (everything before the first blank line
    # that follows a '>' run).
    lines = body.split("\n")
    start = 0
    for i, line in enumerate(lines):
        if line.strip() and not line.lstrip().startswith(">"):
            start = i
            break
    body = "\n".join(lines[start:])

    # Strip markdown noise and the spoken-pause markers.
    body = re.sub(r"^\s*>\s?", "", body, flags=re.MULTILINE)
    body = body.replace("//", " ")
    body = re.sub(r"[*_`#]", "", body)
    body = re.sub(r"^\s*[-|]\s*$", "", body, flags=re.MULTILINE)

    # Collapse whitespace, keep sentence rhythm.
    text = re.sub(r"\s+", " ", body).strip()

    # Guard against characters the tokenizer mangles.
    text = text.replace("—", ", ").replace("–", ", ")
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    text = re.sub(r",\s*,", ",", text)
    return text


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


async def synth(text: str, voice: str, dest: Path, rate: str) -> None:
    comm = edge_tts.Communicate(
        text, voice, rate=rate, volume=VOLUME, pitch=PITCH
    )
    await comm.save(str(dest))


async def build_one(n: int, force: bool) -> dict:
    md_path = SCRIPTS / f"batch_{n}.md"
    mp3_path = AUDIO / f"batch_{n}.mp3"
    voice = VOICES[n]

    text = extract_vo(md_path.read_text(encoding="utf-8"))
    words = len(text.split())

    if mp3_path.exists() and not force:
        d = duration(mp3_path)
        return dict(batch=n, voice=voice, words=words, seconds=round(d, 2),
                    rate=RATE, status="cached", in_band=MIN_SEC <= d <= MAX_SEC)

    tmp = mp3_path.with_suffix(".part.mp3")
    rate = RATE
    await synth(text, voice, tmp, rate)
    d = duration(tmp)

    # Auto-correct only if the spec rate lands outside the 45-60s window.
    nudged = False
    if d > MAX_SEC:
        rate, nudged = "-2%", True
    elif d < MIN_SEC:
        rate, nudged = "+12%", True
    if nudged:
        tmp.unlink()
        await synth(text, voice, tmp, rate)
        d = duration(tmp)

    shutil.move(str(tmp), str(mp3_path))
    return dict(batch=n, voice=voice, words=words, seconds=round(d, 2),
                rate=rate, status="built", in_band=MIN_SEC <= d <= MAX_SEC,
                nudged=nudged)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if not shutil.which("ffprobe"):
        sys.exit("ffprobe not found — needed for duration verification")

    rows = []
    for n in sorted(VOICES):
        rows.append(await build_one(n, args.force))

    print(f"{'#':>2}  {'VOICE':<28} {'WORDS':>5} {'SEC':>6}  {'RATE':>6}  {'45-60s':>6}")
    print("-" * 68)
    for r in rows:
        flag = "OK" if r["in_band"] else "OUT"
        note = " (nudged)" if r.get("nudged") else ""
        print(f"{r['batch']:>2}  {r['voice']:<28} {r['words']:>5} "
              f"{r['seconds']:>6.2f}  {r['rate']:>6}  {flag:>6}{note}")

    bad = [r for r in rows if not r["in_band"]]
    if bad:
        print(f"\nFAIL: {len(bad)} batch(es) outside 45-60s")
        sys.exit(1)
    print(f"\nAll {len(rows)} voiceovers built and inside 45-60s.")


if __name__ == "__main__":
    asyncio.run(main())
