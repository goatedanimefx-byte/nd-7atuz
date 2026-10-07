#!/usr/bin/env python3
import sys, subprocess, tempfile, json, re
from pathlib import Path
import httpx

BASE = Path('/home/prince/.dundermifflin/agencies/yt_auto')
PROD = BASE / 'production'
AUDIO = PROD / 'audio'
SCRIPTS = PROD / 'scripts'
FINAL = PROD / 'final_packs'
FINAL.mkdir(parents=True, exist_ok=True)

def getd(p):
    r = subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(p)],capture_output=True,text=True,check=True)
    return float(r.stdout.strip())
def gen_image(prompt, outpath):
    # try pollinations.ai (free, no api key)
    try:
        url = f'https://image.pollinations.ai/prompt/{prompt}?width=1080&height=1920&model=flux&private=false&seed=-1&enhance=true'
        r = httpx.get(url, timeout=120, follow_redirects=True, trust_env=False)
        if r.status_code == 200:
            outpath.write_bytes(r.content)
            return True
    except Exception:
        pass
    return False
def get_prompt(n, sp):
    try:
        txt = Path(sp).read_text()
        # extract first strong line
        for line in txt.splitlines():
            if 'CASE' in line.upper() or 'NINE' in line.upper() or 'TENT' in line.upper():
                s = line.strip().replace('#','').replace('*','')
                if s: 
                    return f"dark cinematic true crime horror vertical 9:16, moody black-red atmosphere, {s}, cinematic lighting, grainy film look, no text, no logo"
    except:
        pass
    return f"dark cinematic horror true crime vertical 9:16, moody black background with deep red shadows, eerie atmosphere, cinematic grain, film noir lighting, no text, no watermark"
