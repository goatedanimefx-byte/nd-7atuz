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

BG_DARK = (5,5,10)
RED = (184,20,20)

def get_duration(p):
    r = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(p)],capture_output=True,text=True,check=True)
    return float(r.stdout.strip())
def render_batch(n):
    from PIL import Image, ImageDraw, ImageFont
    ap = AUDIO_DIR/f"batch_{n}.mp3"; sp = SCRIPTS_DIR/f"batch_{n}.md"
    dur = get_duration(ap)
    beats=[]
    txt = sp.read_text()
    m = re.search(r'\| # \| Time \| Stickman Action \| On-Screen Text \|', txt)
    if m:
        for line in txt[m.end():].splitlines():
            if re.match(r'^\|\s*\d+\s*\|',line):
                parts = [p.strip() for p in line.split('|')[1:5]]
                if len(parts)>=4:
                    tstr,on = parts[1],parts[3]
                    mm = re.search(r'(\d+):(\d+)\s*-\s*(\d+):(\d+)',tstr)
                    if mm:
                        beats.append({"s":int(mm.group(1))*60+int(mm.group(2)),"e":int(mm.group(3))*60+int(mm.group(4)),"text":re.sub(r'\*+','',on)})
    if not beats: beats=[{"s":0,"e":dur,"text":f"CASE {n:02d}"}]
    mid=dur/2; main=beats[0]['text']
    for b in beats:
        if b['s']<=mid<=b['e']: main=b['text']; break
    w,h,fps=1080,1920,30; nf=int(dur*fps)
    fd=Path(tempfile.mkdtemp())
    try:
        try: 
            fb = ImageFont.truetype('/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf',64)
            fm = ImageFont.truetype('/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf',42)
            fs = ImageFont.truetype('/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf',32)
        except:
            fb=fm=fs=ImageFont.load_default()
        img = Image.new('RGB',(w,h),BG_DARK); d=ImageDraw.Draw(img)
        d.rectangle((0,0,w,120),fill=RED)
        d.text((40,30),"CLASSIFIED",font=fb,fill=(255,255,255))
        d.text((w-240,38),f"FILE {n:02d}",font=fm,fill=(255,255,255))
        d.rectangle((0,150,10,h-300),fill=RED)
        d.rectangle((w-10,150,w,h-300),fill=RED)
        # wrap
        words=main.split()
        lines=[]; cur=[]
        for wrd in words[:30]:
            cur.append(wrd)
            t=' '.join(cur)
            try: l=d.textlength(t,font=fm)
            except: l=len(t)*20
            if l > w-140:
                if len(cur)>1: cur.pop(); lines.append(' '.join(cur)); cur=[wrd]
                else: lines.append(wrd); cur=[]
        if cur: lines.append(' '.join(cur))
        y=h//2-(len(lines)*50)//2
        for ln in lines:
            try: tl=d.textlength(ln,font=fm)
            except: tl=len(ln)*20
            d.text(((w-tl)//2,y),ln.upper(),font=fm,fill=(240,240,240)); y+=50
        bt="UNSOLVED • REDACTED • EVIDENCE"
        try: tl=d.textlength(bt,font=fs)
        except: tl=len(bt)*15
        d.text(((w-tl)//2,h-80),bt,font=fs,fill=(160,160,160))
        base=fd/'base.jpg'; img.save(base,quality=95)
        out=FINAL_DIR/f"short_{n}.mp4"
        cmd=["ffmpeg","-y","-loop","1","-i",str(base),"-i",str(ap),"-vf",f"scale=1080:1920:force_original_aspect_ratio=cover,crop=1080:1920,zoompan=z='min(zoom+0.0001,1.018)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={nf}:fps={fps},rotate=a='0.3*sin(2*PI*t/12)*PI/180'","-c:v","libopenh264","-pix_fmt","yuv420p","-c:a","aac","-b:a","128k","-t",str(dur),"-shortest",str(out)]
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=1500)
        if r.returncode!=0 or not out.exists():
            cmd2=["ffmpeg","-y","-loop","1","-i",str(base),"-i",str(ap),"-vf",f"zoompan=z='min(zoom+0.00012,1.02)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={nf}:fps={fps}","-c:v","libopenh264","-pix_fmt","yuv420p","-c:a","aac","-b:a","128k","-t",str(dur),"-shortest",str(out)]
            r2=subprocess.run(cmd2,capture_output=True,text=True,timeout=1500)
            if r2.returncode!=0: raise RuntimeError(r2.stderr[-150:])
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
            report.append({'b':i,'p':p,'d':round(d,2),'sz':sz,'mb':round(sz/1e6,2),'e':None})
            print('ok',i)
        except Exception as e:
            report.append({'b':i,'p':'ERR','d':0,'sz':0,'mb':0,'e':str(e)})
            print('err',i,e)
    from datetime import datetime
    rep=BASE/'RENDER_REPORT.md'
    with open(rep,'w') as f:
        f.write('# RENDER_REPORT\n\n'); f.write(f'Generated: {datetime.now().isoformat()}\n\n')
        for x in report:
            f.write(f"## short_{x['b']}.mp4\n")
            f.write(f"- Path: {x['p']}\n")
            f.write(f"- Duration: {x['d']}s\n")
            f.write(f"- Resolution: 1080x1920\n")
            f.write(f"- File size: {x['mb']} MB ({x['sz']} bytes)\n")
            if x['e']: f.write(f"- Error: {x['e']}\n")
            f.write('\n')
    return 0
if __name__=='__main__': sys.exit(main())
