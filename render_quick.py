#!/usr/bin/env python3
import sys, subprocess, tempfile, re
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
def render(n):
    from PIL import Image,ImageDraw,ImageFont
    ap=AUDIO_DIR/f"batch_{n}.mp3"; sp=SCRIPTS_DIR/f"batch_{n}.md"
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
    w,h,fps=1080,1920,30; nf=min(int(dur*fps), 300)  # 10s max frames for speed? or use all? better use fewer keyframes style
    nf=int(dur*fps)
    fd=Path(tempfile.mkdtemp())
    try:
        try: font=ImageFont.truetype('/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf',40)
        except: font=ImageFont.load_default()
        # generate frames but maybe skip? simpler - make 1 static frame + use zoompan over single image? faster
        img=Image.new('RGB',(w,h),BG_COLOR); d=ImageDraw.Draw(img)
        d.text((40,60),f"BATCH {n}",font=font,fill=RED)
        d.text((40,h-90),"CLASSIFIED",font=font,fill=RED)
        d.rectangle((20,240,1060,360),outline=RED,width=3); d.text((40,270),"EVIDENCE",font=font,fill=RED)
        img.save(fd/'base.jpg')
        out=FINAL_DIR/f"short_{n}.mp4"
        # zoompan on single image
        cmd=["ffmpeg","-y","-loop","1","-i",str(fd/'base.jpg'),"-i",str(ap),"-vf","zoompan=z='min(zoom+0.00008,1.012)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={}:fps={}".format(nf,fps),"-c:v","libopenh264","-pix_fmt","yuv420p","-c:a","aac","-b:a","128k","-t",str(dur),"-shortest",str(out)]
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=900)
        if r.returncode!=0:
            cmd=["ffmpeg","-y","-loop","1","-i",str(fd/'base.jpg'),"-i",str(ap),"-c:v","libopenh264","-pix_fmt","yuv420p","-c:a","aac","-t",str(dur),"-shortest",str(out)]
            r=subprocess.run(cmd,capture_output=True,text=True,timeout=900)
        if not out.exists(): raise RuntimeError("no out")
        return str(out),dur,out.stat().st_size
    finally:
        for f in fd.glob("*"): 
            try: f.unlink()
            except: pass
        try: fd.rmdir()
        except: pass
