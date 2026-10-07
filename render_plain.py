#!/usr/bin/env python3
import sys, subprocess, tempfile
from pathlib import Path
from datetime import datetime

BASE = Path("/home/prince/.dundermifflin/agencies/yt_auto")
PROD = BASE / "production"
AUDIO_DIR = PROD / "audio"
FINAL_DIR = PROD / "final_packs"
FINAL_DIR.mkdir(parents=True, exist_ok=True)

BG = (0,0,0)
WHITE = (255,255,255)

def get_duration(p):
    r = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(p)],capture_output=True,text=True,check=True)
    return float(r.stdout.strip())
def render_batch(n):
    from PIL import Image, ImageDraw, ImageFont
    ap = AUDIO_DIR/f"batch_{n}.mp3"
    dur = get_duration(ap)
    w,h,fps=1080,1920,30; nf=int(dur*fps)
    fd = Path(tempfile.mkdtemp())
    try:
        try:
            fb = ImageFont.truetype('/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf', 120)
        except:
            fb = ImageFont.load_default()
        img = Image.new('RGB',(w,h),BG)
        d = ImageDraw.Draw(img)
        txt = f"CASE FILE {n:02d}"
        try: tw = d.textlength(txt, font=fb)
        except: tw = len(txt)*70
        d.text(((w-tw)//2, h//2 - 60), txt, font=fb, fill=WHITE)
        base = fd/'base.jpg'
        img.save(base, quality=95)
        out = FINAL_DIR/f"short_{n}.mp4"
        cmd = ["ffmpeg","-y","-loop","1","-i",str(base),"-i",str(ap),
               "-vf",f"zoompan=z='min(zoom+0.00007,1.01)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={nf}:fps={fps}",
               "-c:v","libopenh264","-pix_fmt","yuv420p","-c:a","aac","-b:a","128k","-t",str(dur),"-shortest",str(out)]
        r = subprocess.run(cmd,capture_output=True,text=True,timeout=1200)
        if r.returncode!=0 or not out.exists():
            raise RuntimeError(r.stderr[-100:])
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
    rep=BASE/'RENDER_REPORT.md'
    from datetime import datetime
    with open(rep,'w') as f:
        f.write('# RENDER_REPORT\n\n'); f.write(f'Generated: {datetime.now().isoformat()}\n\n')
        for x in report:
            f.write(f"## short_{x['b']}.mp4\n- Path: {x['p']}\n- Duration: {x['d']}s\n- Resolution: 1080x1920\n- File size: {x['mb']} MB ({x['sz']} bytes)\n")
            if x['e']: f.write(f"- Error: {x['e']}\n")
            f.write('\n')
    return 0
if __name__=='__main__': sys.exit(main())
