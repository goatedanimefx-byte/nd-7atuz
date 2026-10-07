#!/usr/bin/env python3
import os
import sys
import subprocess
import re
import tempfile
from pathlib import Path

BASE = Path("/home/prince/.dundermifflin/agencies/yt_auto")
PROD = BASE / "production"
AUDIO_DIR = PROD / "audio"
SCRIPTS_DIR = PROD / "scripts"
FINAL_DIR = PROD / "final_packs"
FINAL_DIR.mkdir(parents=True, exist_ok=True)

BG_COLOR = (10,10,15)
RED = (184,20,20)
def get_duration(audio_path):
    cmd = ["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(audio_path)]
    res = subprocess.run(cmd,capture_output=True,text=True,check=True)
    return float(res.stdout.strip())
def render_batch(batch_num):
    from PIL import Image, ImageDraw, ImageFont
    audio_path = AUDIO_DIR / f"batch_{batch_num}.mp3"
    script_path = SCRIPTS_DIR / f"batch_{batch_num}.md"
    if not audio_path.exists(): raise FileNotFoundError(f"Missing {audio_path}")
    if not script_path.exists(): raise FileNotFoundError(f"Missing {script_path}")
    dur = get_duration(audio_path)
    # parse beats
    beats = []
    txt = script_path.read_text()
    m = re.search(r'\| # \| Time \| Stickman Action \| On-Screen Text \|', txt)
    if m:
        for line in txt[m.end():].splitlines():
            if re.match(r'^\|\s*\d+\s*\|', line):
                parts = [p.strip() for p in line.split('|')[1:5]]
                if len(parts)>=4:
                    time, ontext = parts[1], parts[3]
                    mm = re.search(r'(\d+):(\d+)\s*-\s*(\d+):(\d+)', time)
                    if mm:
                        s=int(mm.group(1))*60+int(mm.group(2)); e=int(mm.group(3))*60+int(mm.group(4))
                        beats.append({"s":s,"e":e,"text":ontext.replace('*','')})
    if not beats: beats=[{"s":0,"e":dur,"text":f"Batch {batch_num}"}]
    w,h,fps=1080,1920,30
    nframes=int(dur*fps)
    fd=Path(tempfile.mkdtemp())
    try:
        try: font=ImageFont.truetype('/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf',48)
        except: font=ImageFont.load_default()
        for i in range(nframes):
            t=i/fps
            bt=f"Batch {batch_num}"
            for b in beats:
                if b['s']<=t<=b['e']+0.01: bt=b['text']; break
            img=Image.new('RGB',(w,h),BG_COLOR)
            draw=ImageDraw.Draw(img)
            # stickman
            cx,cy=w//2,h//2-100
            draw.ellipse((cx-30,cy-80,cx+30,cy-20),outline=RED,width=4)
            draw.line((cx,cy-20,cx,cy+80),fill=RED,width=6)
            draw.line((cx,cy+20,cx-60,cy+40),fill=RED,width=6)
            draw.line((cx,cy+20,cx+60,cy+40),fill=RED,width=6)
            draw.line((cx,cy+80,cx-40,cy+180),fill=RED,width=6)
            draw.line((cx,cy+80,cx+40,cy+180),fill=RED,width=6)
            draw.text((50,50),f"BATCH {batch_num}",font=font,fill=RED)
            draw.text((w//2-200,h-200),bt[:40],font=font,fill=(255,255,255))
            draw.text((50,h-60),"CLASSIFIED",font=font,fill=RED)
            img.save(fd/f"f_{i:06d}.jpg")
        out=FINAL_DIR/f"short_{batch_num}.mp4"
        cmd=["ffmpeg","-y","-r",str(fps),"-i",str(fd/"f_%06d.jpg"),"-i",str(audio_path),"-vf",f"zoompan=z='min(zoom+0.00015,1.015)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={nframes}:fps={fps}","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac","-b:a","128k","-t",str(dur),str(out)]
        res=subprocess.run(cmd,capture_output=True,text=True,timeout=900)
        if res.returncode!=0: raise RuntimeError(res.stderr[-150:])
        return str(out),dur,out.stat().st_size
    finally:
        for f in fd.glob("*"): 
            try: f.unlink()
            except: pass
        try: fd.rmdir()
        except: pass
def main():
    report=[]
    for i in range(1,6):
        try:
            p,d,sz=render_batch(i)
            report.append({"file":p,"batch":i,"dur":round(d,2),"res":"1080x1920","mb":round(sz/1e6,2),"sz":sz,"err":None})
            print(f"OK {i}")
        except Exception as e:
            report.append({"file":"ERR","batch":i,"dur":0,"res":"1080x1920","mb":0,"sz":0,"err":str(e)})
            print(f"ERR {i}: {e}")
    rep=BASE/"RENDER_REPORT.md"
    with open(rep,'w') as f:
        f.write("# RENDER_REPORT\n\n")
        from datetime import datetime
        f.write(f"Generated: {datetime.now().isoformat()}\n\n")
        for r in report:
            f.write(f"## short_{r['batch']}.mp4\n")
            f.write(f"- Path: {r['file']}\n")
            f.write(f"- Duration: {r['dur']}s\n")
            f.write(f"- Resolution: {r['res']}\n")
            f.write(f"- File size: {r['mb']} MB ({r['sz']} bytes)\n")
            if r['err']: f.write(f"- Error: {r['err']}\n")
            f.write("\n")
    return 0
if __name__=='__main__': sys.exit(main())
