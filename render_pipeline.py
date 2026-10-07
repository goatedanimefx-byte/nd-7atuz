#!/usr/bin/env python3
import os
import sys
import subprocess
import json
import re
import tempfile
import glob
from pathlib import Path

# Paths
BASE = Path("/home/prince/.dundermifflin/agencies/yt_auto")
PROD = BASE / "production"
AUDIO_DIR = PROD / "audio"
SCRIPTS_DIR = PROD / "scripts"
FINAL_DIR = PROD / "final_packs"
CASE_DIR = PROD / "case_files"
TTS_BUILD = PROD / "tts_build.py"

FINAL_DIR.mkdir(parents=True, exist_ok=True)

# Colors
BG_COLOR = (10, 10, 15)
RED = (184, 20, 20)

def get_duration(audio_path):
    try:
        cmd = [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration", "-of", "default=nw=1:nk=1",
            str(audio_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        raise RuntimeError(f"ffprobe failed for {audio_path}: {e}")

def generate_subtitles(audio_path, script_path):
    """Try Whisper (openai-whisper/faster-whisper). Fallback to naive word timing."""
    try:
        import whisper
        model = whisper.load_model("base", device="cpu")
        result = model.transcribe(str(audio_path), language="en")
        subs = []
        for seg in result["segments"]:
            subs.append({
                "start": float(seg["start"]),
                "end": float(seg["end"]),
                "text": seg["text"].strip()
            })
        return subs
    except Exception as e1:
        try:
            from faster_whisper import WhisperModel
            model = WhisperModel("base", device="cpu", compute_type="int8")
            segments, _ = model.transcribe(str(audio_path), language="en")
            subs = []
            for seg in segments:
                subs.append({
                    "start": float(seg.start),
                    "end": float(seg.end),
                    "text": seg.text.strip()
                })
            return subs
        except Exception as e2:
            # Fallback: split script into words with uniform timing
            return generate_fallback_subs(audio_path, script_path)

def generate_fallback_subs(audio_path, script_path, words_per_sec=2.5):
    try:
        dur = get_duration(audio_path)
        text = Path(script_path).read_text()
        # extract voiceover lines
        vo_lines = []
        in_vo = False
        for line in text.splitlines():
            if "VOICEOVER SCRIPT" in line:
                in_vo = True; continue
            if in_vo:
                if line.strip().startswith(">") or line.strip() == "" or line.startswith("##"):
                    if line.strip().startswith(">"):
                        l = line.lstrip(">").strip()
                        if l: vo_lines.append(l.replace("//", " "))
                    continue
                if line.strip():
                    vo_lines.append(line.replace("//", " "))
        full = " ".join(vo_lines)
        # split words
        words = re.findall(r'\S+', full)
        if len(words) == 0:
            return []
        wps = max(1.0, len(words) / dur) if dur > 0 else words_per_sec
        wps = words_per_sec  # simple
        total_w = len(words)
        sec_per_word = dur / total_w if total_w > 0 else 0.4
        subs = []
        for i, w in enumerate(words):
            start = i * sec_per_word
            end = min((i + 1) * sec_per_word, dur)
            subs.append({"start": start, "end": end, "text": w})
        return subs
    except Exception as e:
        return []

def make_frame_base(size=(1080,1920)):
    from PIL import Image, ImageDraw, ImageFilter
    w,h = size
    img = Image.new('RGBA', size, (*BG_COLOR, 255))
    draw = ImageDraw.Draw(img)
    # grain noise (simple)
    for i in range(500):
        x = (i*127) % w
        y = (i*313) % h
        v = ((i*7) % 20) - 10
        c = tuple(max(0,min(255, BG_COLOR[k] + v)) for k in range(3))
        draw.point((x,y), fill=(*c, 30))
    return img

def draw_beat(frame_img, beat_text, beat_num):
    from PIL import ImageDraw, ImageFont
    draw = ImageDraw.Draw(frame_img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", 60)
        font_small = ImageFont.truetype("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", 40)
    except:
        font = ImageFont.load_default()
        font_small = font
    # "CLASSIFIED" stamp
    if "CLASSIFIED" in beat_text.upper() or "9 HIKERS" in beat_text.upper():
        draw.text((50, 80), "CLASSIFIED", font=font, fill=RED + (255,))
    # evidence tag
    if "EVIDENCE" in beat_text.upper() or "PHOTO" in beat_text.upper() or "DOCUMENT" in beat_text.upper():
        draw.rectangle([(50,250),(400,320)], outline=RED, width=4)
        draw.text((70,265), "EVIDENCE", font=font_small, fill=RED)
    # timestamp style
    draw.text((50, h:=frame_img.height-120), beat_text[:80], font=font_small, fill=(255,255,255,255))
    return frame_img

def render_batch(batch_num):
    from PIL import Image, ImageDraw
    audio_path = AUDIO_DIR / f"batch_{batch_num}.mp3"
    script_path = SCRIPTS_DIR / f"batch_{batch_num}.md"
    if not audio_path.exists():
        raise FileNotFoundError(f"Missing {audio_path}")
    if not script_path.exists():
        raise FileNotFoundError(f"Missing {script_path}")
    
    dur = get_duration(audio_path)
    subs = generate_subtitles(audio_path, script_path)
    
    # Parse beats
    beats = []
    try:
        txt = script_path.read_text()
        m = re.search(r'\| # \| Time \| Stickman Action \| On-Screen Text \|', txt)
        if m:
            # parse table lines after
            lines = txt[m.end():].splitlines()
            for line in lines:
                if re.match(r'^\|\s*\d+\s*\|', line):
                    parts = [p.strip() for p in line.split('|')[1:5]]
                    if len(parts) >= 4:
                        bnum, time, action, ontext = parts[0], parts[1], parts[2], parts[3]
                        # parse time range
                        m2 = re.search(r'(\d+):(\d+)\s*-\s*(\d+):(\d+)', time)
                        if m2:
                            s = int(m2.group(1))*60 + int(m2.group(2))
                            e = int(m2.group(3))*60 + int(m2.group(4))
                        else:
                            s, e = 0, 5
                        beats.append({"s": s, "e": e, "text": ontext.replace('**', '').replace('*', '')})
    except Exception:
        beats = [{"s": 0, "e": dur, "text": f"Batch {batch_num}"}]
    
    # Render frames
    w,h = 1080,1920
    fps = 30
    nframes = int(dur * fps)
    frame_dir = Path(tempfile.mkdtemp())
    try:
        for i in range(nframes):
            t = i / fps
            # find current beat
            beat_text = f"Batch {batch_num}"
            for b in beats:
                if b['s'] <= t <= b['e'] + 0.01:
                    beat_text = b['text']
                    break
            img = make_frame_base((w,h))
            draw = ImageDraw.Draw(img)
            # draw stickman figure
            cx, cy = w//2, h//2 - 100
            draw.ellipse((cx-30, cy-80, cx+30, cy-20), outline=RED, width=4)  # head
            draw.line((cx, cy-20, cx, cy+80), fill=RED, width=6)  # body
            draw.line((cx, cy+20, cx-60, cy+40), fill=RED, width=6)  # arm
            draw.line((cx, cy+20, cx+60, cy+40), fill=RED, width=6)
            draw.line((cx, cy+80, cx-40, cy+180), fill=RED, width=6)  # leg
            draw.line((cx, cy+80, cx+40, cy+180), fill=RED, width=6)
            # draw text
            try:
                font = ImageFont.truetype("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf", 48)
            except:
                font = ImageFont.load_default()
            # word
            draw.text((w//2 - 200, h-200), beat_text[:40], font=font, fill=(255,255,255))
            # timestamp
            draw.text((50,50), f"BATCH {batch_num}", font=font, fill=RED)
            draw.text((50,h-60), "CLASSIFIED", font=font, fill=RED)
            img.save(frame_dir / f"frame_{i:06d}.png")
    except Exception as e:
        raise RuntimeError(f"Frame rendering failed: {e}")
    
    # Build SRT
    srt_path = frame_dir / "subs.srt"
    with open(srt_path, 'w') as f:
        for idx, s in enumerate(subs, 1):
            start = s['start']
            end = s['end']
            if end - start < 0.1 and idx < len(subs):
                end = min(start + 0.8, dur)
            def fmt(t):
                h = int(t//3600); m = int((t%3600)//60); sec = t%60
                return f"{h:02d}:{m:02d}:{sec:06.3f}".replace('.', ',')
            f.write(f"{idx}\n{fmt(start)} --> {fmt(end)}\n{s['text']}\n\n")
    
    # Video with zoompan + subtitles + audio
    out_mp4 = FINAL_DIR / f"short_{batch_num}.mp4"
    # base video
    cmd = [
        "ffmpeg", "-y", "-r", str(fps), "-i", str(frame_dir / "frame_%06d.png"),
        "-i", str(audio_path),
        "-vf", f"zoompan=z='min(zoom+0.0002,1.02)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={nframes}:fps={fps},drawtext=text='':subtitles={srt_path}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-t", str(dur),
        str(out_mp4)
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if res.returncode != 0:
            # simpler fallback
            cmd2 = [
                "ffmpeg", "-y", "-r", str(fps), "-i", str(frame_dir / "frame_%06d.png"),
                "-i", str(audio_path),
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k",
                "-t", str(dur),
                str(out_mp4)
            ]
            res2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=900)
            if res2.returncode != 0:
                raise RuntimeError(f"ffmpeg failed: {res.stderr[-200:]}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffmpeg timed out")
    
    # clean temp
    for f in frame_dir.glob("*"): 
        try: f.unlink()
        except: pass
    try: frame_dir.rmdir()
    except: pass
    
    return str(out_mp4), dur, out_mp4.stat().st_size if out_mp4.exists() else 0

def main():
    report = []
    errors = []
    for i in range(1, 6):
        try:
            path, dur, sz = render_batch(i)
            report.append({
                "file": path,
                "batch": i,
                "duration_sec": round(dur, 2),
                "resolution": "1080x1920",
                "filesize_bytes": sz,
                "filesize_mb": round(sz/1e6,2),
                "error": None
            })
            print(f"OK batch {i}: {path} ({dur:.1f}s)")
        except Exception as e:
            errors.append(f"batch_{i}: {e}")
            report.append({
                "file": "ERROR",
                "batch": i,
                "duration_sec": 0,
                "resolution": "1080x1920",
                "filesize_bytes": 0,
                "filesize_mb": 0,
                "error": str(e)
            })
            print(f"ERR batch {i}: {e}", file=sys.stderr)
    # write report
    rep_path = BASE / "RENDER_REPORT.md"
    with open(rep_path, 'w') as f:
        f.write("# RENDER_REPORT\n\n")
        f.write(f"Generated: {__import__('datetime').datetime.now().isoformat()}\n\n")
        for r in report:
            f.write(f"## short_{r['batch']}.mp4\n")
            f.write(f"- Path: {r['file']}\n")
            f.write(f"- Duration: {r['duration_sec']}s\n")
            f.write(f"- Resolution: {r['resolution']}\n")
            f.write(f"- File size: {r['filesize_mb']} MB ({r['filesize_bytes']} bytes)\n")
            if r['error']: f.write(f"- Error: {r['error']}\n")
            f.write("\n")
        if errors:
            f.write("## Errors\n")
            for er in errors: f.write(f"- {er}\n")
    print(f"Report written to {rep_path}")
    return 0 if all(r['error'] is None for r in report) else 1

if __name__ == '__main__':
    sys.exit(main())
