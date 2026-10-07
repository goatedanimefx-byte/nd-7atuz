#!/usr/bin/env python3
import os, sys, subprocess, re, tempfile
from pathlib import Path
from datetime import datetime

BASE = Path("/home/prince/.dundermifflin/agencies/yt_auto")
PROD = BASE / "production"
AUDIO_DIR = PROD / "audio"
SCRIPTS_DIR = PROD / "scripts"
FINAL_DIR = PROD / "final_packs"
FINAL_DIR.mkdir(parents=True, exist_ok=True)
BG_COLOR=(10,10,15); RED=(184,20,20)

def get_duration(p):
    r=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(p)],capture_output=True,text=True,check=True)
    return float(r.stdout.strip())
def render_batch(n):
    from PIL import Image,ImageDraw,ImageFont
    ap=AUDIO_DIR/f"batch_{n}.mp3"; sp=SCRIPTS_DIR/f"batch_{n}.md"
    if not ap.exists(): raise FileNotFoundError(ap)
    if not sp.exists(): raise FileNotFoundError(sp)
    dur=get_duration(ap)
    beats=[]
    txt=sp.read_text()
    m=re.search(r'\| # \| Time \| Stickman Action \| On-Screen Text \|',txt)
    if m:
        for line in txt[m.end():].splitlines():
            if re.match(r'^\|\s*\d+\s*\|',line):
                parts=[p.strip() for p in line.split('|')[1:5]]
                if len(parts)>=4:
                    t,on=parts[1],parts[3]
                    mm=re.search(r'(\d+):(\d+)\s*-\s*(\d+):(\d+)',t)
                    if mm: beats.append({"s":int(mm.group(1))*60+int(mm.group(2)),"e":int(mm.group(3))*60+int(mm.group(4)),"text":re.sub(r'\*+','',on)})
    if not beats: beats=[{"s":0,"e":dur,"text":f"Batch {n}"}]
    w,h,fps=1080,1920,30; nf=int(dur*fps)
    fd=Path(tempfile.mkdtemp())
    try:
        try: font=ImageFont.truetype('/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf',44)
        except: font=ImageFont.load_default()
        for i in range(nf):
            t=i/fps; bt=f"Batch {n}"
            for b in beats:
                if b['s']<=t<=b['e']+0.01: bt=b['text']; break
            img=Image.new('RGB',(w,h),BG_COLOR); d=ImageDraw.Draw(img)
            cx,cy=w//2,h//2-100
            d.ellipse((cx-30,cy-80,cx+30,cy-20),outline=RED,width=4)
            d.line((cx,cy-20,cx,cy+80),fill=RED,width=6)
            d.line((cx,cy+20,cx-60,cy+40),fill=RED,width=6)
            d.line((cx,cy+20,cx+60,cy+40),fill=RED,width=6)
            d.line((cx,cy+80,cx-40,cy+180),fill=RED,width=6)
            d.line((cx,cy+80,cx+40,cy+180),fill=RED,width=6)
            d.rectangle((20,240,1060,360),outline=RED,width=3)
            d.text((40,270),"EVIDENCE",font=font,fill=RED)
            d.text((40,60),f"BATCH {n}",font=font,fill=RED)
            d.text((40,h-200),bt[:70],font=font,fill=(255,255,255))
            d.text((40,h-90),"CLASSIFIED",font=font,fill=RED)
            img.save(fd/f"f_{i:06d}.jpg")
        out=FINAL_DIR/f"short_{n}.mp4"
        # force h264 via v4l2m2m or default software - try specific codecs
        cmd=["ffmpeg","-y","-r",str(fps),"-i",str(fd/"f_%06d.jpg"),"-i",str(ap),"-vf","zoompan=z='min(zoom+0.0001,1.01)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={}:fps={}".format(nf,fps),"-c:v","libx264","preset","veryfast","pix_fmt","yuv420p","-c:a","aac","-b:a","128k","-t",str(dur),str(out)]
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=1800)
        if r.returncode!=0:
            # fallback
            cmd=["ffmpeg","-y","-r",str(fps),"-i",str(fd/"f_%06d.jpg"),"-i",str(ap),"-c:v","h264","-pix_fmt","yuv420p","-c:a","aac","-t",str(dur),str(out)]
            r=subprocess.run(cmd,capture_output=True,text=True,timeout=1800)
        if not out.exists(): raise RuntimeError("Failed")
        return str(out),dur,out.stat().st_size
    finally:
        for f in fd.glob("*"):
            try: f.unlink()
            except: pass
        try: fd.rmdir()
        except: pass
