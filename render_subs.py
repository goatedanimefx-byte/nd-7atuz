#!/usr/bin/env python3
import sys, subprocess, tempfile, os
from pathlib import Path

BASE = Path("/home/prince/.dundermifflin/agencies/yt_auto")
PROD = BASE / "production"
AUDIO_DIR = PROD / "audio"
SCRIPTS_DIR = PROD / "scripts"
FINAL_DIR = PROD / "final_packs"
FINAL_DIR.mkdir(parents=True, exist_ok=True)

def get_duration(p):
    r = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(p)],capture_output=True,text=True,check=True)
    return float(r.stdout.strip())
def make_srt_from_script(sp, ap):
    try:
        import whisper
        model = whisper.load_model('base', device='cpu')
        res = model.transcribe(str(ap), language='en')
        subs = []
        for i,s in enumerate(res['segments'],1):
            start=s['start']; end=s['end']; text=s['text'].strip()
            if end-start < 0.1: end = min(start+0.7, get_duration(ap))
            def f(t):
                h=int(t//3600); m=int((t%3600)//60); sec=t%60
                return f"{h:02d}:{m:02d}:{sec:06.3f}".replace('.',',')
            subs.append((i,f(start),f(end),text))
        return subs
    except Exception:
        try:
            from faster_whisper import WhisperModel
            model = WhisperModel('base', device='cpu', compute_type='int8')
            segs, _ = model.transcribe(str(ap), language='en')
            subs=[]
            for i,s in enumerate(segs,1):
                start=float(s.start); end=float(s.end); text=s.text.strip()
                if end-start < 0.1: end = min(start+0.7, get_duration(ap))
                def f(t):
                    h=int(t//3600); m=int((t%3600)//60); sec=t%60
                    return f"{h:02d}:{m:02d}:{sec:06.3f}".replace('.',',')
                subs.append((i,f(start),f(end),text))
            return subs
        except Exception:
            return []
def render_batch(n):
    from pathlib import Path
    import subprocess, tempfile, random
    ap = AUDIO_DIR/f"batch_{n}.mp3"; sp = SCRIPTS_DIR/f"batch_{n}.md"
    dur = get_duration(ap)
    fd = Path(tempfile.mkdtemp())
    try:
        srt = fd/f'subs.srt'
        subs = make_srt_from_script(sp,ap)
        with open(srt,'w') as f:
            for i,start,end,text in subs:
                f.write(f"{i}\n{start} --> {end}\n{text}\n\n")
        # pick bg
        bgs = list((PROD/'bg_imgs').glob('*.jpg'))
        if not bgs: bgs = [Path('/tmp/bg_1.jpg')]
        bg = random.choice(bgs)
        out = FINAL_DIR/f"short_{n}.mp4"
        w,h,fps=1080,1920,30; nf=int(dur*fps)
        cmd = ["ffmpeg","-y","-loop","1","-i",str(bg),"-i",str(ap),
               "-vf",f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,zoompan=z='min(zoom+0.0001,1.03)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={nf}:fps={fps},rotate=a='0.4*sin(2*PI*t/15)*PI/180',subtitles={srt}:force_style='FontName=DejaVuSans-Bold,FontSize=48,OutlineColour=&H000000,BorderStyle=1,Outline=3,Shadow=0,Alignment=2,MarginV=80,PrimaryColour=&HFFFFFF'",
               "-c:v","libopenh264","-pix_fmt","yuv420p","-c:a","aac","-b:a","128k","-t",str(dur),"-shortest",str(out)]
        r = subprocess.run(cmd,capture_output=True,text=True,timeout=1800)
        if r.returncode!=0 or not out.exists():
            cmd2 = ["ffmpeg","-y","-loop","1","-i",str(bg),"-i",str(ap),
                    "-vf",f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,zoompan=z='min(zoom+0.00012,1.025)':d={nf}:fps={fps},subtitles={srt}:force_style='FontName=DejaVuSans-Bold,FontSize=48,OutlineColour=&H000000,BorderStyle=3,Outline=3,Alignment=2,MarginV=80,PrimaryColour=&HFFFFFF'",
                    "-c:v","libopenh264","-pix_fmt","yuv420p","-c:a","aac","-b:a","128k","-t",str(dur),"-shortest",str(out)]
            r2 = subprocess.run(cmd2,capture_output=True,text=True,timeout=1800)
            if r2.returncode!=0: raise RuntimeError(r2.stderr[-200:])
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
