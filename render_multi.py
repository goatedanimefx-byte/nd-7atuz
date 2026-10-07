#!/usr/bin/env python3
import sys, subprocess, tempfile, random, math
from pathlib import Path
from datetime import datetime

BASE = Path("/home/prince/.dundermifflin/agencies/yt_auto")
PROD = BASE / "production"
AUDIO_DIR = PROD / "audio"
FINAL_DIR = PROD / "final_packs"
FINAL_DIR.mkdir(parents=True, exist_ok=True)

BG = (0,0,0)
WHITE = (255,255,255)
RED = (180,20,20)

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
        try: fb=ImageFont.truetype('/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf',110)
        except: fb=ImageFont.load_default()
        # create multiple frames with different text positions/rotation effects
        seed = n*123
        random.seed(seed)
        for i in range(min(nf, 90)):  # keyframes every ~3s
            img = Image.new('RGB',(w,h),BG)
            d = ImageDraw.Draw(img)
            txt = f"CASE FILE {n:02d}"
            try: tw = d.textlength(txt,font=fb)
            except: tw = len(txt)*65
            # vary position slightly
            ox = random.randint(-20,20)
            oy = random.randint(-30,30)
            d.text(((w-tw)//2+ox, h//2-55+oy), txt, font=fb, fill=WHITE)
            # subtle scanlines
            img.save(fd/f'k_{i:04d}.jpg', quality=95)
        # build concat list
        with open(fd/'list.txt','w') as f:
            for i in range(min(nf,90)):
                f.write(f"file 'k_{i:04d}.jpg'\n")
        out = FINAL_DIR/f"short_{n}.mp4"
        # crossfade between keyframes for rotation feel
        cmd = ["ffmpeg","-y","-f","concat","-safe","0","-i",str(fd/'list.txt'),"-i",str(ap),
               "-vf",f"zoompan=z='if(lte(on,1),1.0,zoom+0.00009)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={nf}:fps={fps}",
               "-c:v","libopenh264","-pix_fmt","yuv420p","-c:a","aac","-b:a","128k","-t",str(dur),"-shortest",str(out)]
        r = subprocess.run(cmd,capture_output=True,text=True,timeout=1500)
        if r.returncode!=0 or not out.exists():
            cmd2 = ["ffmpeg","-y","-loop","1","-i",str(fd/'k_0000.jpg'),"-i",str(ap),
                    "-vf",f"zoompan=z='min(zoom+0.00012,1.02)':d={nf}:fps={fps}",
                    "-c:v","libopenh264","-pix_fmt","yuv420p","-c:a","aac","-b:a","128k","-t",str(dur),"-shortest",str(out)]
            r2 = subprocess.run(cmd2,capture_output=True,text=True,timeout=1500)
            if r2.returncode!=0: raise RuntimeError(r2.stderr[-120:])
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
            f.write(f"## short_{x['b']}.mp4\n- Path: {x['p']}\n- Duration: {x['d']}s\n- Resolution: 1080x1920\n- File size: {x['mb']} MB ({x['sz']} bytes)\n")
            if x['e']: f.write(f"- Error: {x['e']}\n")
            f.write('\n')
    return 0
if __name__=='__main__': sys.exit(main())
