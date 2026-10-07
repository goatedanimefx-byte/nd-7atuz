#!/usr/bin/env python3
"""
yt_auto :: Colab Notebook Generator

Builds `yt_auto_colab.ipynb`: a self-contained Google Colab notebook that
renders the whole shorts pipeline on Colab's free CPU/GPU with 0% local load.

Cells
  0. Markdown header — what to click, what to upload, secrets needed
  1. Environment  — silent apt ffmpeg/fonts + pip edge-tts/playwright/pillow/
                    numpy/fpdf2 + playwright chromium (--with-deps)
  2. Render       — input resolve → optional Edge-TTS VO (word timings) →
                    Chromium Canvas2D vector motion graphics (1080x1920@30,
                    pure function of t) → word-synced ASS/SRT → FFmpeg mix at
                    -14 LUFS → build.json receipt; Telegram alerts on start
                    and every 10% of frames
  3. Dispatch     — Telegram video upload (curl) + Google Drive save when
                    mounted + local-download fallback

Usage:
    python3 build_colab_notebook.py                # write yt_auto_colab.ipynb
    python3 build_colab_notebook.py --out /tmp/x   # custom output path
"""

from __future__ import annotations

import argparse
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent

# --------------------------------------------------------------------------- #
# Cell 1 — environment                                                          #
# --------------------------------------------------------------------------- #
CELL_ENV = r'''#@title 1/3 — Environment (silent install)
import shutil, subprocess, sys

def _sh(*a):
    r = subprocess.run(list(a), capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-1200:])
    return r.stdout.strip()

# system ffmpeg (silent; first run only)
if not shutil.which("ffmpeg"):
    _sh("apt-get", "update", "-qq")
    _sh("apt-get", "install", "-y", "-qq", "ffmpeg")
    try:
        _sh("apt-get", "install", "-y", "-qq", "fonts-noto-core")
    except Exception:
        print("font install skipped (captions will fall back to DejaVu)")

def _pip(*p):
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q",
         "--no-warn-script-location"] + list(p), check=True)

# heavy stack runs HERE on Colab, not on the local box
_pip("edge-tts", "playwright", "pillow", "numpy", "fpdf2")
subprocess.run([sys.executable, "-m", "playwright", "install",
                "--with-deps", "chromium"], check=True)

print("env ready ->", _sh("ffmpeg", "-version").splitlines()[0])
'''

# --------------------------------------------------------------------------- #
# Cell 2 — the rendering pipeline (the big one)                                 #
# --------------------------------------------------------------------------- #
CELL_RENDER = r'''#@title 2/3 — Render: vector motion graphics + word-synced captions + mix
import asyncio
import base64
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

WORK   = Path("/content/yt_auto");  WORK.mkdir(exist_ok=True)
INP    = WORK / "input";            INP.mkdir(exist_ok=True)
os.environ.setdefault("CLIP_URL", "__CLIP_URL_DEFAULT__")
FRAMES = WORK / "frames";           FRAMES.mkdir(exist_ok=True)
OUT    = WORK / "out";              OUT.mkdir(exist_ok=True)
FONTS  = WORK / "fonts";            FONTS.mkdir(exist_ok=True)


def _sh(*a):
    r = subprocess.run([str(x) for x in a], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-1200:])
    return r.stdout.strip()


def _ffprobe_dur(p):
    return float(_sh("ffprobe", "-v", "error", "-show_entries",
                     "format=duration", "-of", "default=nw=1:nk=1", p))


# ---------------------------- telegram alerts ----------------------------- #
def tg_token():
    try:
        from google.colab import userdata
        return userdata.get("TELEGRAM_BOT_TOKEN") or ""
    except Exception:
        pass
    return os.environ.get("TELEGRAM_BOT_TOKEN", "")


TG_CHAT   = os.environ.get("TELEGRAM_CHAT_ID", "-1004375547101")
TG_THREAD = os.environ.get("TELEGRAM_THREAD_ID", "992")
# __TG_DEFAULTS__


def _tg_url(method):
    return "https://api.telegram.org/bot%s/%s" % (tg_token(), method)


def tg_send(text):
    if not tg_token():
        print("[tg] no TELEGRAM_BOT_TOKEN — alerts off")
        return
    body = urllib.parse.urlencode({
        "chat_id": TG_CHAT, "message_thread_id": TG_THREAD, "text": text,
    }).encode()
    try:
        urllib.request.urlopen(
            urllib.request.Request(_tg_url("sendMessage"), body), timeout=25)
    except Exception as e:
        print("[tg] sendMessage:", e)


def tg_send_video(path, caption=""):
    tok = tg_token()
    if not tok:
        return False
    curl = shutil.which("curl")
    if curl:
        subprocess.run([
            curl, "-s", "-F", "chat_id=" + TG_CHAT,
            "-F", "message_thread_id=" + TG_THREAD,
            "-F", "caption=" + caption, "-F", "video=@" + str(path),
            _tg_url("sendVideo")], capture_output=True, timeout=900)
        return True
    print("[tg] curl missing, video stays local/drive")
    return False


def notify(msg):
    tag = datetime.now().strftime("%H:%M:%S")
    print("[%s] %s" % (tag, msg))
    tg_send("%s\n%s" % (tag, msg))


# ------------------------------- input ------------------------------------ #
def find_input():
    """Priority: env CLIP_ZIP -> /content sidebar upload -> Drive -> upload."""
    if os.environ.get("CLIP_ZIP"):
        p = Path(os.environ["CLIP_ZIP"])
        if p.exists():
            return p
    url = os.environ.get("CLIP_URL")
    if url:
        dst = INP / "clip.zip"
        try:
            with urllib.request.urlopen(url, timeout=120) as rq:
                data = rq.read()
            if len(data) > 2000:
                dst.write_bytes(data)
                if zipfile.is_zipfile(dst):
                    notify("clip fetched from link bridge")
                    return dst
                print("[input] CLIP_URL had no zip payload")
        except Exception as e:
            print("[input] CLIP_URL fetch failed:", e)
    for root in (Path("/content"), INP):
        zips = sorted(root.glob("*.zip"))
        if zips:
            return zips[0]
    drive = Path("/content/drive/MyDrive/yt_auto")
    if drive.exists():
        zips = sorted(drive.glob("*.zip"))
        if zips:
            return zips[0]
    try:
        from google.colab import files
        up = files.upload()
        for name, blob in up.items():
            p = (INP / name)
            p.write_bytes(blob)
            if name.endswith(".zip"):
                return p
    except Exception:
        pass
    raise SystemExit("no clip zip: put clip_N.zip in /content or MyDrive/yt_auto "
                     "or upload it when prompted, or set CLIP_ZIP")


def load_clip(zip_path):
    notify("render start: %s" % zip_path.name)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(INP)
    for mp3 in INP.glob("*.mp3"):
        shutil.move(str(mp3), str(INP / mp3.name))
    cfg = INP / "clip.json"
    if not cfg.exists():
        raise SystemExit("clip.json missing inside the zip")
    return json.loads(cfg.read_text(encoding="utf-8"))


# ------------------------------ edge-tts VO -------------------------------- #
async def _synth_voice(text, clip):
    import edge_tts
    com = edge_tts.Communicate(
        text,
        clip.get("voice", "en-US-ChristopherNeural"),
        rate=clip.get("rate", "+5%"),
        pitch=clip.get("pitch", "-4Hz"),
    )
    chunks, words, sents = [], [], []
    async for ch in com.stream():
        if ch["type"] == "audio":
            chunks.append(ch["data"])
        elif ch["type"] == "WordBoundary":
            off = ch["offset"] / 1e7
            end = (ch["offset"] + ch["duration"]) / 1e7
            words.append((off, end, ch["text"]))
        elif ch["type"] == "SentenceBoundary":
            sents.append((ch["offset"] / 1e7,
                          (ch["offset"] + ch["duration"]) / 1e7, ch["text"]))
    if not words and sents:
        # voices without WordBoundary events: split each sentence window
        # across its words weighted by character length (approximate karaoke).
        for s0, s1, stext in sents:
            toks = [w for w in str(stext).split() if any(
                ch.isalnum() for ch in w)]
            if not toks:
                continue
            ln = sum(len(w) + 1 for w in toks)
            acc = s0
            for j, wd in enumerate(toks):
                dur = (s1 - s0) * (len(wd) + 1) / ln
                words.append((acc, acc + dur, wd))
                acc += dur
    if sents:
        # exact sentence audio windows (SentenceBoundary) — captions that land
        # on the real speech onset instead of the char-weighted estimate.
        toks = [[w for w in str(stext).split() if any(
            ch.isalnum() for ch in w)] for _, _, stext in sents]
        (INP / "sents.json").write_text(json.dumps(
            [[s0, s1, ts] for (s0, s1, _), ts in zip(sents, toks) if ts]),
            encoding="utf-8")
    if chunks:
        (INP / "vo.mp3").write_bytes(b"".join(chunks))
    if words:
        (INP / "words.json").write_text(json.dumps(
            [[a, b, w] for a, b, w in words]), encoding="utf-8")
    return words


def ensure_audio(clip):
    vo = INP / "vo.mp3"
    words = None
    sents = None
    wf = INP / "words.json"
    if not vo.exists():
        text = clip.get("vo_text")
        if not text:
            raise SystemExit("zip has no vo.mp3 and clip.json has no vo_text")
        notify("edge-tts: synthesizing VO (%s)" % clip.get("voice"))
        words = asyncio.run(_synth_voice(text, clip))
        sf = INP / "sents.json"
        if sf.exists():
            sents = [tuple(x) for x in json.loads(sf.read_text(encoding="utf-8"))]
        if not vo.exists():
            raise SystemExit("edge-tts produced no audio")
        notify("VO done (%.1fs)" % _ffprobe_dur(vo))
    elif wf.exists():
        words = [tuple(x) for x in json.loads(wf.read_text(encoding="utf-8"))]
        sf = INP / "sents.json"
        if sf.exists():
            sents = [tuple(x) for x in json.loads(sf.read_text(encoding="utf-8"))]
    bgm = INP / "bgm.mp3"
    return vo, bgm if bgm.exists() else None, words, sents


# ------------------------------ captions ----------------------------------- #
def _ass_ts(t):
    t = max(0.0, t)
    h = int(t // 3600); m = int((t % 3600) // 60)
    s = int(t % 60); c = int(round((t % 1) * 100)) % 100
    return "%d:%02d:%02d.%02d" % (h, m, s, c)


def _srt_ts(t):
    t = max(0.0, t)
    ms = int(round(t * 1000))
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, (ms // 60000) % 60,
                                    (ms // 1000) % 60, ms % 1000)


def _ass_escape(s):
    return re.sub(r"[\{\}]", "", s)


def resolve_captions(clip, word_times, sents=None):
    cval = clip.get("captions")
    if cval is not None:
        mode = str(cval).strip().lower()
        if mode in ("none", "off", "false"):
            return None
        if isinstance(cval, list):
            return [(float(c["start"]), float(c["end"]), str(c["text"]))
                    for c in cval]
        if mode == "phrase":
            word_times = None  # phrase-sync below, not word grouping
        elif mode not in ("word", "auto"):
            word_times = None
    if word_times:
        # papercut: one caption chip per SENTENCE, windowed by the exact
        # SentenceBoundary audio times — chips appear precisely when the
        # sentence is spoken (no drift past real speech onset/offset).
        if clip.get("motif") == "papercut":
            cpf = INP / "caps_papercut.json"
            if cpf.exists():
                caps = [tuple(x) for x in json.loads(
                    cpf.read_text(encoding="utf-8"))]
            elif sents:
                caps = [(float(s0), float(s1), " ".join(ts))
                        for s0, s1, ts in sents]
            else:
                caps = None
        else:
            # group words into readable chunks (<= 8 words, breaks near full
            # stops)
            caps, buf, n = [], [], 0
            for off, end, w in word_times:
                buf.append(w); n += 1
                if n >= 8 or w.endswith(".") or w.endswith("?"):
                    caps.append((off, end, " ".join(buf)))
                    buf, n = [], 0
            if buf:
                s0, _ = word_times[len(word_times) - len(buf)]
                caps.append((s0, word_times[-1][1], " ".join(buf)))
        if not caps:
            return None
        # keep karaoke continuous: each chunk stays on screen until the next
        # one starts (bridges the natural intonation gaps in the audio).
        hard = float(clip.get("duration") or (word_times[-1][1] + 0.5))
        for i in range(len(caps)):
            e = caps[i + 1][0] if i + 1 < len(caps) else hard
            caps[i] = ((caps[i][0], min(e, hard), caps[i][2])
                       + (caps[i][3:] if len(caps[i]) > 3 else ()))
        return caps
    # phrase-sync: one caption per beat from the on-screen text
    out = []
    for b in clip["beats"]:
        t = b.get("sub") or b.get("text")
        if t:
            out.append((float(b["start"]), float(b["end"]), str(t)))
    return out or None


def write_captions(caps, W, H):
    ass = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: %d" % W,
           "PlayResY: %d" % H, "WrapStyle: 0", "ScaledBorderAndShadow: yes",
           "",
           "[V4+ Styles]",
           "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
           "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
           "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
           "Alignment, MarginL, MarginR, MarginV, Encoding",
           "Style: Cap,Noto Sans,54,&H00FFFFFF,&H00FFFFFF,&H00000000,"
           "&H5A000000,-1,0,0,0,100,100,0,0,1,3,1,2,70,70,170,1",
           "",
           "[Events]",
           "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
           "MarginV, Effect, Text"]
    for s, e, txt in caps:
        ass.append("Dialogue: 0,%s,%s,Cap,,0,0,0,,%s"
                   % (_ass_ts(s), _ass_ts(e), _ass_escape(txt)))
    srt = []
    for i, (s, e, txt) in enumerate(caps, 1):
        srt.append("%d\n%s --> %s\n%s\n" % (i, _srt_ts(s), _srt_ts(e), txt))
    (OUT / "captions.ass").write_text("\n".join(ass), encoding="utf-8")
    (OUT / "captions.srt").write_text("\n".join(srt), encoding="utf-8")
    return OUT / "captions.ass"


# ----------------------- Canvas2D vector motion layer ---------------------- #
def build_html(clip, caps=None):
    W, H = int(clip.get("width", 1080)), int(clip.get("height", 1920))
    beat_json = json.dumps(clip["beats"]).replace("</", "<\\/")
    caps_json = json.dumps(
        [[float(c[0]), float(c[1]), str(c[2])]
         + ([list(c[3])] if len(c) > 3 else []) for c in caps]
        if caps else []).replace("</", "<\\/")
    return """<!doctype html><html><head><meta charset="utf-8">
<style>html,body{margin:0;background:#01030a;overflow:hidden}</style></head>
<body><canvas id="c" width="__W__" height="__H__"></canvas>
<script>
(function(){
var W=__W__,H=__H__,DUR=__DUR__;
var ACC='__ACCENT__',BEATS=__BEATS__,MOTIF='__MOTIF__',BADGE=__BADGE__,CTA=__CTA__;
var CAPS=__CAPS__;
var cv=document.getElementById('c'),x=cv.getContext('2d');
function cl(p){return Math.min(Math.max(p,0),1);}
function seg(t,a,d){return cl((t-a)/d);}
function ease(p){p=cl(p);return p<.5?2*p*p:1-Math.pow(-2*p+2,2)/2;}
function inwin(t,a,b){return t>=a&&t<b;}
  function rr(x,cx,cy,cw,ch,r){x.beginPath();x.roundRect(cx,cy,cw,ch,r);x.fill();}
function seed(n){var s=Math.sin(n*127.1+311.7)*43758.5453;return s-Math.floor(s);}
function fmt(t){if(t<0)t=0;var m=Math.floor(t/60),s=t%60;return m+':'+(s<10?'0':'')+Math.floor(s);}
function lines(txt,maxw){
  x.font='900 148px Arial';
  var wr=txt.split(' '),out=[],cur=wr[0];
  for(var i=1;i<wr.length;i++){
    var test=cur+' '+wr[i];
    if(x.measureText(test).width>maxw){out.push(cur);cur=wr[i];}else{cur=test;}
  }
  out.push(cur);return out.slice(0,4);
}
function frame(t){
  if(MOTIF==='forensic'){renderForensic(t);return;}
  if(MOTIF==='papercut'){renderPapercut(t);return;}
  x.clearRect(0,0,W,H);
  var sc=1+0.034*cl(t/DUR);
  x.save();x.translate(W/2,H/2);x.scale(sc,sc);x.translate(-W/2,-H/2);
  var INK=(MOTIF==='collage'||MOTIF==='pencil')?'#2b241d':'#f4f1ea';
  var SUB=(MOTIF==='collage'||MOTIF==='pencil')?'rgba(43,36,29,0.5)':'rgba(244,241,234,0.5)';
  var LITE=MOTIF==='collage'||MOTIF==='pencil';
  var PAL={
    kinetic:['#0a1120','#060a13','#01030a'],
    glitch:['#10160c','#0a0f07','#03050a'],
    matrix:['#02101e','#010a12','#00060d'],
    minimal:['#141118','#0c0a10','#050408'],
    'cloud-sea':['#122046','#2a4a8f','#7fb2e8'],
    'boiling-ink':['#0c1222','#16233f','#24405f'],
    cel:['#061022','#0b1630','#143a66'],
    collage:['#f2e6cc','#eadcba','#dfcba4'],
    pencil:['#f5ead6','#ead7b2','#dcbf8f']};
  var pl=PAL[MOTIF]||PAL.kinetic;
  var g=x.createLinearGradient(0,0,0,H);
  g.addColorStop(0,pl[0]);g.addColorStop(.55,pl[1]);g.addColorStop(1,pl[2]);
  x.fillStyle=g;x.fillRect(0,0,W,H);
  if(MOTIF==='cloud-sea'){
    for(var cb=0;cb<9;cb++){
      var cxp=seed(cb)*W,cxr=180+seed(cb+4)*420,
          cy0=H-(seed(cb+8)*H*.55+(-t*(20+seed(cb+2)*30)%(H*.7))-H*.1);
      var cg=x.createRadialGradient(cxp,cy0,10,cxp,cy0,cxr);
      cg.addColorStop(0,'rgba(255,244,214,0.30)');cg.addColorStop(.6,'rgba(255,220,170,0.10)');
      cg.addColorStop(1,'rgba(255,220,170,0)');
      x.fillStyle=cg;x.beginPath();x.arc(cxp,cy0,cxr,0,6.2832);x.fill();
    }
    x.globalCompositeOperation='lighter';
    for(var h=0;h<4;h++){x.fillStyle='rgba(255,255,230,'+(0.045+0.02*h*seed(h))+')';
      x.beginPath();x.arc(seed(h+40)*W,H*0.42+seed(h*7)*H*.4,300+seed(h+9)*380,0,6.2832);x.fill();}
    x.globalCompositeOperation='source-over';
  } else if(MOTIF==='cel'){
    x.fillStyle='rgba(255,214,140,0.25)';x.beginPath();x.arc(W*0.78,H*0.22,150,0,6.2832);x.fill();
    x.fillStyle='rgba(255,214,140,0.10)';x.beginPath();x.arc(W*0.78,H*0.22,230,0,6.2832);x.fill();
    x.fillStyle='#0a1c38';
    x.beginPath();x.moveTo(0,H*0.72);
    for(var hh=1;hh<=6;hh++){x.lineTo(W*(hh-0.5)/6,H*0.72-seed(hh)*90);x.lineTo(W*hh/6,H*0.72);}
    x.lineTo(W,H);x.lineTo(0,H);x.fill();
  } else if(MOTIF==='collage'){
    var dx=Math.sin(t*3.1)*5+Math.sin(t*7.3+1.7)*3;
    x.translate(dx,0);
    for(var hd=0;hd<900;hd++){
      var hx=(seed(hd)*W+Math.sin(t*(0.4+seed(hd+5)*0.5)+hd)*18)%W,
          hy=(seed(hd+100)*H+Math.sin(t*(0.32+seed(hd+9)*0.4)+hd*2)*14)%H;
      x.fillStyle='rgba(120,80,20,0.05)';x.beginPath();x.arc(hx,hy,1.6+seed(hd+7)*1.6,0,6.2832);x.fill();
    }
    x.translate(-dx,0);
  } else if(MOTIF==='pencil'){
    x.strokeStyle='rgba(120,70,20,0.10)';x.lineWidth=1;
    for(var hd2=0;hd2<40;hd2++){
      var hx2=seed(hd2)*W+Math.sin(t*3+hd2)*6,hy2=seed(hd2+50)*H;
      x.beginPath();x.moveTo(hx2,hy2+30);x.lineTo(hx2+90,hy2);x.stroke();
    }
  }
  x.fillStyle=LITE?'rgba(43,36,29,0.05)':(MOTIF==='matrix'?'rgba(80,255,120,0.020)':'rgba(255,255,255,0.020)');
  for(var i=0;i<130;i++){
    var px=seed(i)*W,py=(seed(i+9)*H+Math.floor(t*24)* (1+seed(i+3)*3))%H;
    x.fillRect(px,py,2,2);
  }
  if(MOTIF==='matrix'){
    x.strokeStyle='rgba(80,255,120,0.10)';x.lineWidth=2;
    for(var sl=0;sl<9;sl++){
      var sy=((t*40+sl*H/9)%H);x.beginPath();x.moveTo(0,sy);x.lineTo(W,sy);x.stroke();
    }
  }
  if(MOTIF==='boiling-ink'){
    x.strokeStyle='rgba(255,255,255,0.05)';x.lineWidth=2;
    x.beginPath();x.moveTo(W/2-320,0);x.lineTo(W/2-320,H/2+160);
    x.moveTo(W/2+320,0);x.lineTo(W/2+320,H/2+160);
    x.lineTo(W/2+320,H);x.moveTo(W/2-320,H/2+160);x.lineTo(W/2+320,H/2+160);x.stroke();
    for(var sn=0;sn<26;sn++){
      var sxp=seed(sn)*W,syp=(seed(sn+30)*H+t*(14+seed(sn+5)*26))%H;
      x.fillStyle='rgba(240,248,255,'+(0.4+0.4*Math.sin(t*2+sn))+')';
      x.fillRect(sxp,syp,3,3);
    }
  }
  var total=BEATS.length,idx=-1;
  for(var k=0;k<total;k++){if(inwin(t,BEATS[k].start,BEATS[k].end+0.001)){idx=k;break;}}
  if(idx<0)idx=0;
  if(BADGE){
    x.fillStyle='rgba(255,255,255,0.04)';x.font='900 520px Arial';x.textAlign='right';
    x.fillText((idx+1)<10?'0'+(idx+1):String(idx+1),W-50,560);
    x.textAlign='left';
  }
  var b=BEATS[idx];
  if(b){
    var sin=ease(seg(t,b.start,.38)),len=b.end-b.start,p=cl((t-b.start)/len);
    var y0=700+(1-sin)*280+Math.sin(t*1.2)*10+Math.sin(t*2.7+1.3)*6;
    if(MOTIF==='2d-explain'){
      var chh=Math.min(780,lines(b.text).length*172+66);
      x.globalAlpha=sin;
      x.fillStyle='rgba(244,241,234,0.055)';rr(x,110,y0-104,Math.min(W-220,900),chh,22);
      x.fillStyle='rgba(244,241,234,0.07)';rr(x,110,y0-104,Math.min(W-220,900),40,14);
      x.fillStyle=ACC;x.fillRect(128,y0-66,5,chh-110);
      x.font='700 24px Arial';x.fillStyle='rgba(244,241,234,0.7)';
      x.fillText('DOC',128,y0-70);
      x.globalAlpha=1;
    }
    x.font='600 38px '+(MOTIF==='matrix'?'monospace':'Arial');
    x.fillStyle=ACC;x.globalAlpha=sin;
    x.fillText((MOTIF==='matrix'?'> ':'')+'BEAT '+(idx+1<10?'0'+(idx+1):idx+1)+' / '+total,120,y0-56);
    x.globalAlpha=1;
    var lns=lines(String(b.text),840),ly=y0;
    if(MOTIF==='collage'){
      x.globalAlpha=sin*0.4;
      x.fillStyle='#fbf3df';
      x.fillRect(0,y0-140,W*ease(seg(t,b.start,.55)),lns.length*172+90);
      x.globalAlpha=1;
    }
    var mono=MOTIF==='matrix'||MOTIF==='glitch';
    var tb=Math.floor(t*8);
    for(var li=0;li<lns.length;li++){
      var wjx=(MOTIF==='boiling-ink'?(seed(li*7+tb)-.5)*16:(MOTIF==='collage'?Math.sin(t*2.6+li*2.3)*5:0)),
          wjy=(MOTIF==='boiling-ink'?(seed(li*13+tb)-.5)*12:(MOTIF==='collage'||MOTIF==='pencil'?Math.sin(t*3.1+li*1.9)*6:0));
      x.fillStyle=MOTIF==='matrix'?'#d6ffd6':INK;
      x.globalAlpha=sin*(0.96-0.05*li);
      x.font=mono?'900 148px monospace':'900 148px Arial';
      if(MOTIF==='pencil'){
        x.lineWidth=1;x.strokeStyle='rgba(120,70,20,0.5)';
        x.beginPath();x.moveTo(120,ly+6+((seed(li*5+tb)-.5)*5));
        x.lineTo(220+(seed(li+tb)*620),ly+4+((seed(li*11+tb)-.5)*5));x.stroke();
      }
      if(MOTIF==='boiling-ink'){
        x.lineWidth=1;x.strokeStyle='rgba(255,255,255,0.18)';
        for(var hk=0;hk<7;hk++){
          x.beginPath();
          x.moveTo(150+((seed(li*31+hk+tb)-.5)*20),ly+14-(hk*3));
          x.lineTo(210+hk*9+((seed(li*3+hk+tb)-.5)*20),ly+44+(hk*3));x.stroke();
        }
      }
      x.fillText(lns[li],120+wjx,ly+wjy);ly+=172;
      if(li===0&&lns.length>1&&MOTIF!=='minimal'&&MOTIF!=='cel'&&MOTIF!=='pencil'){
        x.fillStyle=ACC;x.fillRect(120,ly-164,Math.min(180,1000*(seed(idx+2))),8);
      }
    }
    x.globalAlpha=1;
    if(MOTIF==='glitch'&&seed(idx+6)>.5){
      x.globalAlpha=sin*.22;
      var gx=Math.round(seed(idx+7)*10)-5,gy=Math.round(seed(idx+8)*6)-3;
      x.font='900 148px Arial';x.fillStyle='#4be3ff';
      var gly=y0;
      for(var gl=0;gl<lns.length;gl++){x.fillText(lns[gl],120+gx,gly+gy);gly+=172;}
      x.globalAlpha=1;
    }
    x.globalAlpha=1;x.fillStyle=ACC;
    if(MOTIF==='matrix'){
      x.fillRect(120,ly+36,Math.max(1,(W-240)*ease(p)),6);
    } else if(MOTIF==='minimal'){
      x.fillRect(120,ly+20,Math.max(1,Math.min((W-240)*ease(p),240)),2);
    } else {
      x.fillRect(120,ly+36,Math.max(1,(W-240)*ease(p)),14);
    }
    x.font='600 34px Arial';x.textAlign='right';x.fillStyle=SUB;
    x.fillText(fmt(b.start)+' — '+fmt(b.end),W-120,1440);x.textAlign='left';
  }
  if(CTA&&t>DUR-1.6){
    var a2=cl((t-(DUR-1.6))/1.0),ctaL=String(CTA).split('\\n');
    x.globalAlpha=a2;
    x.fillStyle=ACC;x.font='700 120px Arial';x.textAlign='center';
    x.fillText(ctaL[0],W/2,H*0.56);
    x.fillStyle=INK;x.font='500 46px Arial';
    x.fillText(ctaL[1]||'',W/2,H*0.56+96);
    x.globalAlpha=1;x.textAlign='left';
  }
  x.globalAlpha=1;
  var tx=120,tw=W-240,ty=H-210,th=10,cx=0;
  x.fillStyle='rgba(255,255,255,0.10)';x.fillRect(tx,ty,tw,th);
  for(var k2=0;k2<total;k2++){
    var w0=tw*((BEATS[k2].end-BEATS[k2].start)/DUR);
    x.fillStyle=(k2===idx)?ACC:'rgba(255,255,255,0.55)';
    x.fillRect(tx+cx,ty,Math.max(w0-6,1),th);cx+=w0;
  }
  var pp=cl(t/DUR)*tw;x.fillStyle=INK;
  x.beginPath();x.arc(tx+pp,ty+th/2,10,0,6.2832);x.fill();
  x.font='500 30px Arial';x.textAlign='left';x.fillStyle=SUB;
  x.fillText(fmt(t)+' / '+fmt(DUR),tx,ty+56);
  x.textAlign='right';x.fillStyle=SUB;x.textAlign='left';
  x.restore();
}
function db(t){var s=seed(Math.floor(t)*91.7+Math.floor(t*30));return s*2-1;}
function renderForensic(t){
  x.clearRect(0,0,W,H);
  var tot=BEATS.length,i0=-1;
  for(var k=0;k<tot;k++){if(inwin(t,BEATS[k].start,BEATS[k].end+0.001)){i0=k;break;}}
  if(i0<0)i0=0;
  var b=BEATS[i0],bs=b.start,len=b.end-b.start;
  var PH=1.8,ph=Math.max(0,Math.floor((t-bs)/PH)),phP=cl((t-bs)/PH-ph),
      phStart=bs+ph*PH,flr=(1-ease(seg(t,phStart,0.05)));

  var CASE='CASE FILE #'+(1010+Math.floor(seed(11)*8989))+' — '+
      ['SURAT','TOKYO','DELHI','MUMBAI','LONDON','LA'][Math.floor(seed(23)*6)];

  // ---- layered dark background, Ken Burns 1.0->1.15 continuous + per-shot pan ----
  var kb=1+0.15*cl(t/DUR),panX=Math.sin(ph*2.1)*26+Math.sin(t*0.11)*8,
      panY=Math.cos(ph*1.7)*20+Math.cos(t*0.09)*6;
  x.save();
  x.translate(W/2,H/2);x.scale(kb,kb);x.translate(panX,panY);x.translate(-W/2,-H/2);
  var g=x.createLinearGradient(0,0,0,H);
  g.addColorStop(0,'#0b0b0d');g.addColorStop(.5,'#070709');g.addColorStop(1,'#030304');
  x.fillStyle=g;x.fillRect(-60,-60,W+120,H+120);
  var glx=[0,1,2];
  for(var gl=0;gl<glx.length;gl++){
    var gx0=W*(0.5+0.45*Math.sin(t*0.07+gl*2.4)),gy0=H*(0.40+0.35*Math.cos(t*0.05+gl*1.3));
    var cg=x.createRadialGradient(gx0,gy0,0,gx0,gy0,H*0.80);
    cg.addColorStop(0,'rgba(120,10,10,'+(0.045+0.02*Math.sin(t*0.3+gl))+')');
    cg.addColorStop(1,'rgba(120,10,10,0)');
    x.fillStyle=cg;x.beginPath();x.arc(gx0,gy0,H*0.80,0,6.2832);x.fill();
  }
  for(var d=0;d<260;d++){
    var dax=(seed(d)*W+Math.sin(t*(0.05+seed(d+3)*0.08)+d)*14)%W,
        day=(seed(d+50)*H+Math.cos(t*(0.04+seed(d+8)*0.07)+d)*12)%H;
    x.fillStyle='rgba(255,255,255,'+(0.010+0.008*seed(d+17))+')';
    x.fillRect(dax,day,1.5,1.5);
  }
  for(var sp=0;sp<46;sp++){
    var sy=((t*(26+seed(sp+1)*22)+sp*23)%(H*1.15))-H*0.075;
    x.fillStyle='rgba(255,232,220,'+(0.02+0.05*Math.abs(db(t+sp)))+')';
    x.fillRect(seed(sp+91)*W,sy,2,3);
  }
  var sloff=((t*6)%6);
  x.fillStyle='rgba(0,0,0,0.16)';
  for(var sli=0;sli*6-sloff<H;sli+=2){x.fillRect(0,sli*6-sloff,W,1.5);}
  var gg=Math.floor(t*30);
  x.fillStyle='rgba(255,255,255,0.025)';
  for(var gr=0;gr<520;gr++){
    x.fillRect(seed(gr*1.7+gg*13.1)*W,seed(gr*2.3+gg*7.7)*H,1,1);
  }
  x.restore();
  var vg=x.createRadialGradient(W/2,H*0.46,H*0.30,W/2,H*0.5,H*0.96);
  vg.addColorStop(0,'rgba(0,0,0,0)');vg.addColorStop(1,'rgba(0,0,0,0.55)');
  x.fillStyle=vg;x.fillRect(0,0,W,H);

  // ---- top HUD ----
  x.fillStyle='rgba(0,0,0,0.55)';x.fillRect(0,0,W,96);
  x.fillStyle=ACC;x.fillRect(0,94,W,3);
  var rec=0.5+0.5*Math.sin(t*7);
  x.beginPath();x.arc(36,40,12,0,6.2832);x.fillStyle='rgba(255,30,39,'+(0.25+0.75*rec)+')';x.fill();
  x.font='700 30px monospace';x.textAlign='left';
  x.fillStyle='rgba(255,220,220,0.92)';x.fillText('REC',62,48);
  x.font='600 30px monospace';x.fillStyle='rgba(225,225,225,0.85)';
  x.fillText(CASE,62,84);
  x.textAlign='right';
  x.fillStyle='rgba(225,225,225,0.9)';x.fillText(fmt(t)+' / '+fmt(DUR),W-40,48);
  x.fillStyle=ACC;x.fillText('TC '+Math.floor(t*30),W-40,84);x.textAlign='left';

  // ---- TOP SECRET stamp + faint CLASSIFIED mark ----
  var fl2=0.5+0.5*Math.sin(t*4.3+Math.floor(t*9));
  x.save();x.translate(W*0.80,H*0.135+i0*0.012);x.rotate(-0.14);
  x.globalAlpha=0.75*(0.55+0.45*fl2);
  x.font='900 46px Impact, Arial';x.textAlign='center';
  x.lineWidth=5;x.lineJoin='round';
  x.strokeStyle='#ff1e27';x.strokeText('TOP SECRET',0,0);
  x.fillStyle='rgba(255,20,20,0.85)';x.fillText('TOP SECRET',0,0);
  x.strokeStyle='rgba(255,30,39,0.5)';x.setLineDash([16,10]);
  x.strokeRect(-152,-58,304,86);x.setLineDash([]);
  x.restore();
  x.save();x.translate(W*0.16,H*0.60+i0*0.012);x.rotate(0.07);
  x.globalAlpha=0.11;
  x.font='900 60px Impact, Arial';x.textAlign='center';x.strokeStyle='#ff1e27';
  x.lineWidth=4;x.strokeText('CLASSIFIED',0,0);
  x.restore();x.globalAlpha=1;

  // ---- crime scene tape (corner) ----
  x.save();x.rotate(-0.16);
  for(var tp2=0;tp2<5;tp2++){
    x.fillStyle='rgba(190,150,20,0.15)';x.fillRect(W*0.60+tp2*W*0.085,-10,72,150);
    x.font='700 26px monospace';x.fillStyle='rgba(0,0,0,0.2)';
    x.fillText('CAUTION DO NOT CROSS',W*0.61+tp2*W*0.085,72);
  }
  x.restore();

  // ---- evidence polaroid (tilt + shadow, slides in per shot) ----
  var pop=0.2+0.8*ease(seg(t,phStart+0.06,.28));
  x.save();
  x.translate(W*0.14,H*0.235-(1-pop)*H*0.04);
  x.rotate((ph%2?-0.05:0.06)+phP*0.02);
  x.shadowColor='rgba(0,0,0,0.8)';x.shadowBlur=26;x.shadowOffsetY=12;
  x.fillStyle='#e6e2d9';x.fillRect(0,0,W*0.30,H*0.19);
  x.shadowColor='transparent';x.shadowBlur=0;
  x.fillStyle='#2a2622';x.fillRect(12,12,W*0.30-24,H*0.124);
  x.strokeStyle='rgba(170,20,20,0.8)';x.lineWidth=3;
  for(var fw=0;fw<3;fw++){x.beginPath();x.arc(W*0.15,H*0.074,12+fw*8,0,6.2832);x.stroke();}
  x.font='700 24px monospace';x.fillStyle='#3a342c';
  x.fillText('EXHIBIT A-'+('0'+(i0+1)),16,H*0.176);
  x.restore();

  // ---- glowing red forensic connection lines ----
  var wear=(ph%3===2)?0.28:0.12;
  for(var ed=0;ed<3;ed++){
    var ex1=W*(0.70+seed(ed)*0.24),ey1=H*(0.50+seed(ed+12)*0.12),
        ex2=W*(0.09+seed(ed+30)*0.22),ey2=H*(0.56+seed(ed+44)*0.12);
    x.strokeStyle='rgba(255,40,40,'+wear+')';x.lineWidth=2;
    x.beginPath();x.moveTo(ex1,ey1);x.lineTo(ex2,ey2);x.stroke();
    x.beginPath();x.arc(ex1,ey1,5+3*Math.sin(t*3+ed),0,6.2832);
    x.fillStyle='rgba(255,80,60,0.5)';x.fill();
  }

  // ---- redacted file sheet: black bars slide off to reveal beat lines ----
  var lns=lines(String(b.text),W*0.76),cx0=W*0.11,capY=H*0.375;
  x.font='700 26px monospace';x.fillStyle='rgba(255,255,255,0.55)';
  x.fillText('REPORT // BEAT '+('0'+(i0+1))+'/'+('0'+tot),cx0,capY-30);
  x.fillStyle=ACC;x.fillRect(cx0-14,capY-14,5,lns.length*150+40);
  for(var li=0;li<lns.length;li++){
    var rv=ease(seg(t,b.start+li*0.13,0.34));
    var lyy=capY+li*150+Math.sin(t*2.4+li*1.7)*3;
    var txt=lns[li];
    x.font='900 136px Impact, Arial';
    x.textAlign='left';
    var twd=x.measureText(txt).width;
    x.save();x.translate(cx0,0);
    x.beginPath();x.rect(0,lyy-118,twd+30,150);x.clip();
    x.shadowColor='rgba(0,0,0,0.9)';x.shadowBlur=18;x.shadowOffsetY=6;
    x.fillStyle='rgba(245,242,238,0.96)';
    x.fillText(txt,0,lyy);
    x.restore();
    var barW=(twd+30)*(1-rv);
    if(barW>1){
      x.fillStyle='rgba(8,8,10,0.96)';x.fillRect(cx0+twd+32-barW,lyy-118,barW+4,150);
      x.fillStyle='rgba(255,60,60,0.55)';x.fillRect(cx0+twd+32-barW,lyy-118,6,150);
    }
    x.globalAlpha=0.5*Math.abs(Math.sin(t*0.8+li));
    x.fillStyle=ACC;x.fillRect(cx0,lyy-118,twd+30,3);
    x.globalAlpha=1;
  }

  // ---- cut transitions: dark camera flash + directional glitch slices ----
  if(flr>0.02){
    var nsl=6+(ph%3);
    x.save();
    for(var sl2=0;sl2<nsl;sl2++){
      var syy=(seed(sl2+ph*9)*H)%H;
      x.globalAlpha=flr;
      x.fillStyle='rgba(255,255,255,0.05)';x.fillRect(0,syy,W,12);
      x.fillStyle='rgba(0,0,0,0.85)';
      x.fillRect(0,syy+12,Math.max(2,Math.floor((0.02+seed(sl2+ph*3)*0.06)*W)),8);
    }
    x.globalAlpha=flr*0.55;
    x.fillStyle='rgba(0,0,0,1)';x.fillRect(0,0,W,H);
    x.globalAlpha=flr*(i0%2?0.16:0.24);
    x.fillStyle='rgba(255,30,39,1)';x.fillRect(0,0,W,H);
    x.globalAlpha=1;
  }

  // ---- lower-third active-word captions ----
  if(CAPS&&CAPS.length){
    for(var cp=0;cp<CAPS.length;cp++){
      var c0=CAPS[cp][0],c1=CAPS[cp][1];
      if(t>=c0&&t<c1){
        var wds=String(CAPS[cp][2]).split(' ');
        var frac=cl((t-c0)/(c1-c0));
        var aw=Math.min(wds.length-1,Math.floor(frac*wds.length));
        x.font='900 62px Impact, "Arial Black", Arial';
        x.textAlign='center';
        var baseW=[],totw=0;
        for(var wq=0;wq<wds.length;wq++){baseW.push(x.measureText(wds[wq]+' ').width);totw+=baseW[wq];}
        var xx0=W/2-totw/2,cy=H*0.72;
        for(var wi=0;wi<wds.length;wi++){
          var active=(wi===aw),sca2=active?1.22:1.0;
          x.save();
          x.translate(xx0+baseW[wi]/2,cy);x.scale(sca2,sca2);
          x.font='900 62px Impact, "Arial Black", Arial';
          x.lineWidth=10;x.lineJoin='round';x.strokeStyle='rgba(0,0,0,0.95)';
          x.strokeText(wds[wi],0,0);
          x.shadowColor='rgba(0,0,0,0.9)';x.shadowBlur=12;
          x.fillStyle=active?(i0%2?'#ffe600':'#ff1e27'):'#f3f1ec';
          x.fillText(wds[wi],0,0);
          x.restore();
          xx0+=baseW[wi];
        }
        x.textAlign='left';
        break;
      }
    }
  }

  // ---- bottom forensic HUD ----
  x.fillStyle='rgba(0,0,0,0.55)';x.fillRect(0,H-150,W,150);
  x.fillStyle=ACC;x.fillRect(0,H-150,W,3);
  x.strokeStyle='rgba(255,120,110,0.5)';x.lineWidth=3;x.beginPath();
  for(var wf=0;wf<64;wf++){
    var wfx=12+wf*((W-24)/64);
    var wfy=H-74+db(wf+gg*2)*24*(0.5+0.5*Math.sin(t*1.3+wf));
    if(wf===0)x.moveTo(wfx,wfy);else x.lineTo(wfx,wfy);
  }
  x.stroke();
  var pw=(W-80)*ease(cl(t/DUR));
  x.fillStyle='rgba(255,255,255,0.14)';x.fillRect(40,H-40,W-80,4);
  x.fillStyle=ACC;x.fillRect(40,H-40,pw,4);
  x.beginPath();x.arc(40+pw,H-38,8,0,6.2832);x.fillStyle='#ff1e27';x.fill();
  x.font='600 26px monospace';x.fillStyle='rgba(230,230,230,0.6)';
  x.textAlign='left';x.fillText(fmt(t)+' / '+fmt(DUR),44,H-62);
  x.textAlign='right';x.fillText('TOP SECRET // EVID-'+('0'+(i0+1)),W-44,H-62);
  x.textAlign='left';
}
function renderPapercut(t){
  var tot=BEATS.length,i0=-1;
  for(var k=0;k<tot;k++){if(inwin(t,BEATS[k].start,BEATS[k].end+0.001)){i0=k;break;}}
  if(i0<0)i0=0;
  var b=BEATS[i0],bs=b.start,len=b.end-b.start;
  var PAP='#f7f0dd',CREAM='#f0e5cd',CREAM2='#e6d7b9',CREAM3='#dbc7a5',DEEP='#c3ab83',
      ILINE='rgba(52,32,12,0.42)',
      INK='#2b2320',MUTE='#7a6a58',RED='#a51d24',GOLD='#d8ab3e';
  function eo(p){p=cl(p);return 1-Math.pow(1-p,3);}
  function sl(p){p=cl(p);return p*p*(3-2*p);}
  function cutPath(ox,oy,jag,n){
    x.beginPath();
    for(var g=0;g<n;g++){
      var a=g/n*6.2832;
      var jx=(seed(g*13.7+3.1+i0*5+7)-.5)*jag,jy=(seed(g*7.1+1.3+i0*11+3)-.5)*jag;
      var px=Math.cos(a)*ox+jx,py=Math.sin(a)*oy+jy;
      if(g===0)x.moveTo(px,py);else x.lineTo(px,py);
    }
    x.closePath();
  }
  function paperBlob(dx,dy,rx,ry,rot,col,jag,shad,alpha){
    alpha=alpha==null?1:alpha;
    if(shad){x.save();x.translate(dx,dy+shad);x.rotate(rot);x.fillStyle='rgba(52,32,12,0.20)';cutPath(rx,ry,jag,14);x.fill();x.restore();}
    x.save();x.translate(dx,dy);x.rotate(rot);
    x.fillStyle=col;x.globalAlpha=alpha;cutPath(rx,ry,jag,14);x.fill();x.globalAlpha=1;
    x.restore();
  }
  function paperStrip(dx,dy,w,h,rot,col,jag,shad,alpha){
    alpha=alpha==null?1:alpha;
    var strip=function(yd,alp){
      x.save();x.translate(dx,dy+yd);x.rotate(rot);
      x.fillStyle=col;x.globalAlpha=alp;x.beginPath();
      x.moveTo(-w/2-jag,-h/2);
      for(var gx=-w/2;gx<=w/2;gx+=20){x.lineTo(gx,-h/2+(seed(gx*0.7+13+i0*3)-.5)*jag);}
      x.lineTo(w/2+jag,-h/2);x.lineTo(w/2+jag,h/2);
      for(var gx2=w/2;gx2>=-w/2;gx2-=20){x.lineTo(gx2,h/2+(seed(gx2*0.9+29+i0*5)-.5)*jag);}
      x.lineTo(-w/2-jag,h/2);x.closePath();x.fill();x.restore();
    };
    if(shad)strip(shad,0.18);strip(0,alpha);
  }
  function paperText(txt,cx,cy,size,rot,col,alpha,shw){
    alpha=alpha==null?1:alpha;shw=shw==null?6:shw;col=col||INK;
    x.save();x.translate(cx,cy);x.rotate(rot||0);x.textAlign='center';x.textBaseline='middle';
    x.font='900 '+size+'px Impact,"Arial Black",Arial';
    if(shw){x.save();x.translate(0,shw*0.6);
      x.lineWidth=Math.max(2,size*0.035);x.lineJoin='round';
      x.strokeStyle=ILINE;x.strokeText(txt,0,0);
      x.fillStyle='rgba(52,32,12,0.26)';x.fillText(txt,0,0);x.restore();}
    x.lineWidth=Math.max(2,size*0.035);x.lineJoin='round';
    x.strokeStyle='rgba(43,35,32,0.30)';x.globalAlpha=alpha;x.strokeText(txt,0,0);
    x.fillStyle=PAP;x.fillText(txt,0,0);
    x.fillStyle=col;x.fillText(txt,0,0);
    x.globalAlpha=1;x.restore();
  }
  function wrapH(txt,size,maxW){
    x.font='900 '+size+'px Impact,"Arial Black",Arial';
    var wd=txt.split(' '),ls=[],cur=wd[0];
    for(var i=1;i<wd.length;i++){
      var tt=cur+' '+wd[i];
      if(x.measureText(tt).width>maxW){ls.push(cur);cur=wd[i];}else cur=tt;
    }
    ls.push(cur);return ls;
  }
  function easeBack(p){p=cl(p);var c1=1.70158,c3=c1+1;return 1+c3*Math.pow(p-1,3)+c1*Math.pow(p-1,2);}
  function pi(d){return Math.max(0,easeBack(cl((t-bs-d)/0.42)));}
  function tape(dx,dy,s,rot){x.save();x.translate(dx,dy);x.rotate(rot||0.7);
    x.fillStyle='rgba(232,216,182,0.8)';x.fillRect(-s/2,-s/8,s,s/4);x.restore();}

  // ---- turntable stand: the evidence-board sits on a spinning display base ----
  function drawStand(){
    var sx=W/2,sy=H*0.865;
    x.fillStyle='rgba(52,32,12,0.16)';
    x.beginPath();x.ellipse(sx,sy+10,W*0.30,24,0,0,6.2832);x.fill();
    x.fillStyle='#96764a';x.fillRect(sx-11,sy-118,22,148);
    x.fillStyle='rgba(120,92,52,0.5)';x.fillRect(sx-15,sy-40,30,9);
    var grb=x.createLinearGradient(0,sy-28,0,sy+28);
    grb.addColorStop(0,'#b08a55');grb.addColorStop(1,'#8a6a3a');
    x.fillStyle=grb;x.beginPath();x.ellipse(sx,sy,W*0.32,30,0,0,6.2832);x.fill();
    x.fillStyle='#a5824e';x.beginPath();x.ellipse(sx,sy-10,W*0.32,16,0,0,6.2832);x.fill();
    for(var rs=0;rs<3;rs++){
      var rw=W*(0.15+rs*0.075),ph=t*(0.55+rs*0.3)+rs*1.8;
      x.strokeStyle='rgba(200,172,126,'+(0.42-rs*0.09)+')';x.lineWidth=2.5;
      for(var sa=0;sa<5;sa++){
        var a0=ph+sa/5*6.2832;
        x.beginPath();x.ellipse(sx,sy-30-rs*8,rw,rw*0.34,a0*0.5,0,Math.PI*0.62);x.stroke();
      }
    }
    x.fillStyle='rgba(60,42,20,0.4)';x.font='700 15px Arial';x.textAlign='center';
    x.fillText('TURNTABLE 0'+(i0+1)+' / 0'+tot,sx,sy+Math.min(44,30+rs*8));
    x.textAlign='left';
  }
  // red office stamp seal (evidence stamp on the board)
  function stampSeal(dx,dy,s,rot,txt){
    var sc=pi(0.10);
    if(sc<=0)return;
    x.save();x.globalAlpha=0.85*sc;x.translate(dx,dy);x.rotate(rot);
    x.strokeStyle=RED;x.lineWidth=Math.max(2,s*0.055);x.lineCap='round';
    x.beginPath();x.arc(0,0,s/2,0,6.2832);x.stroke();
    x.beginPath();x.arc(0,0,s/2-5,0,6.2832);x.stroke();
    x.font='900 '+Math.round(s*0.15)+'px Impact,Arial';x.textAlign='center';x.textBaseline='middle';
    x.fillStyle=RED;x.fillText(txt,0,0);
    x.fillStyle='rgba(255,255,255,0.18)';x.fillText(txt,1,1);
    x.restore();
  }

  // ---- camera: no start zoom; per-beat point A->B slide + turntable sway ---
  var PAN=[0,-36,42,-34,40,8,-40,30];
  var ROT=[0,-0.012,0.010,-0.012,0.012,0.006,-0.010,0.009];
  var st=cl((t-bs)/0.34),sm=eo(st),imp=1-sm;
  var kickX=Math.sin(i0*2.6)*15*imp,kickY=Math.cos(i0*1.4)*10*imp,rotPunch=Math.sin(i0*3.7)*0.009*imp;
  var tbx=Math.sin(t*1.9)*8.5+Math.sin(t*3.7)*4,tby=Math.cos(t*2.3)*7+Math.cos(t*4.1)*3;
  var finalPhase=(i0===tot-1)&&(t>bs+0.9);
  var kz=(finalPhase?Math.max(0.80,1-0.20*eo(cl((t-bs-0.9)/1.4))):1)
         *(1+0.018*sm+0.019*Math.sin(t*1.4)+0.006*Math.sin(t*0.5));
  var panA=i0>0?PAN[i0-1]:0,panB=PAN[i0],panX=(panA+(panB-panA)*eo(cl((t-bs)/0.5)))*0.85+Math.sin(t*0.3+i0)*3;
  var rotT=ROT[i0]*(0.6+0.4*Math.sin(t*0.35+i0*2.4))+0.006*Math.sin(t*0.4+i0*2.1);
  x.save();
  x.translate(W/2+kickX+tbx+panX,H/2+kickY+tby);
  x.rotate(rotT+rotPunch*0.6+0.003*Math.sin(t*0.5));
  x.scale(kz,kz);
  x.translate(-W/2,-H/2);

  // ---- L0 billboard: aged kraft paper (radial light) ----
  var g0=x.createRadialGradient(W/2,H*0.42,H*0.12,W/2,H*0.5,H*1.05);
  g0.addColorStop(0,'#f6efdc');g0.addColorStop(.55,'#ecdfc2');g0.addColorStop(1,'#d9c5a0');
  x.fillStyle=g0;x.fillRect(-96,-96,W+192,H+192);
  for(var mt=0;mt<460;mt++){x.fillStyle='rgba(120,90,40,'+(0.010+0.018*seed(mt*1.3))+')';x.fillRect(seed(mt*2.1)*(W+192)-60,seed(mt*3.7)*(H+192)-80,2,2);}
  for(var fb=0;fb<78;fb++){
    var fxb=seed(fb*1.9)*W,fyb=seed(fb*5.3)*H;
    x.strokeStyle='rgba(140,100,45,'+(0.028+0.045*seed(fb*7.7))+')';x.lineWidth=1;
    x.beginPath();x.moveTo(fxb,fyb);x.lineTo(fxb+8+seed(fb*2.3)*16,fyb+2+seed(fb*3.1)*5);x.stroke();
  }
  for(var st2=0;st2<4;st2++){
    x.strokeStyle='rgba(150,115,60,'+(0.05+0.03*Math.sin(t*0.2+st2))+')';x.lineWidth=2;
    x.beginPath();x.arc(seed(st2*7.7)*W,H*0.28+seed(st2*3.1)*H*0.5,60+seed(st2*1.7)*70,0,6.2832);x.stroke();
  }
  // ---- L1 floating paper scraps + drifting dust ----
  for(var sp=0;sp<8;sp++){
    var sx=(seed(sp*3.7)*W+Math.sin(t*(0.5+seed(sp)*0.6)+sp*2)*30)%W,
        sy=(seed(sp*6.1)*H+Math.sin(t*(0.35+seed(sp)*0.4)+sp*3)*36+sp*14)%H;
    paperStrip(sx,sy,44+seed(sp*1.1)*22,18+seed(sp*2.9)*12,Math.sin(t*0.6+sp*1.7)*0.4,sp%2?CREAM2:PAP,7,0,0.55+0.25*Math.sin(t+sp*1.3));
  }
  for(var sn=0;sn<40;sn++){
    var snx=(seed(sn*1.3)*W+Math.sin(t*(0.6+seed(sn)*0.5)+sn)*9)%W,
        sny=(seed(sn*2.7)*H+t*(30+seed(sn)*40))%(H*1.15)-50;
    x.fillStyle='rgba(255,252,240,'+(0.18+0.30*Math.sin(t*2+sn))+')';
    x.beginPath();x.arc(snx,sny,1.5+seed(sn*3.1)*2.4,0,6.2832);x.fill();
  }
  for(var sn2=0;sn2<22;sn2++){
    var snx2=(seed(sn2*7.7)*W+Math.sin(t*(1.3+seed(sn2)*0.6)+sn2)*12)%W,
        sny2=(seed(sn2*9.1)*H+t*(70+seed(sn2)*40))%(H*1.05)-40;
    x.fillStyle='rgba(255,250,235,'+(0.12+0.22*Math.sin(t*3+sn2))+')';
    x.beginPath();x.arc(snx2,sny2,1+seed(sn2*11.3)*1.6,0,6.2832);x.fill();
  }

  // ---- scene tableaus (paper-cut dioramas, contact-pop per beat) ----
  function scene0(){
    var pb=pi(0.06);
    paperBlob(W*0.77,H*0.33,120,120,0.10+Math.sin(t*0.3)*0.02,PAP,16,7,pb);
    paperBlob(W*0.77+30,H*0.33-16,90,90,0,GOLD,12,0,pb*0.5);
    paperBlob(W*0.77-10,H*0.33+4,40,30,0,CREAM2,10,0,pb);
    for(var L=0;L<3;L++){
      var baseY=H*0.64,lw=Math.min(W-300,420+L*46),h0=H*(0.30+L*0.06),h1=H*(0.34+L*0.09);
      x.save();x.translate(W*0.46+((L%2?-1:1)*Math.sin(t*0.12+L*1.7)*8),0);
      x.fillStyle=L===0?CREAM3:(L===1?CREAM2:DEEP);
      x.beginPath();x.moveTo(-30,baseY);
      for(var q=0;q<11;q++){
        var bx=q/10*lw;
        var bhh=h0+(h1-h0)*seed(q*3.3+L*7)+Math.sin(t*0.5+q*2+L)*6;
        x.lineTo(bx,baseY-bhh);x.lineTo(bx+30+seed(q*1.1+L)*10,baseY-bhh);
      }
      x.lineTo(lw+30,baseY);x.lineTo(lw+30,H*0.66);x.lineTo(-30,H*0.66);x.closePath();x.fill();
      if(L===0){
        x.fillStyle='rgba(150,110,55,0.5)';
        for(var q2=0;q2<9;q2++){x.fillRect(q2/8*lw*1.06-4,baseY-22-(h0+seed(q2*2.3+L*4)*(h1-h0))*0.62,7,10);}
      }
      x.restore();
    }
    x.strokeStyle='rgba(60,42,20,0.55)';x.lineWidth=2.5;
    for(var br=0;br<2;br++){
      var bxx=W*(0.28+br*0.20+seed(br)*0.05)+Math.sin(t*0.5+br*3)*6,byy=H*0.20+Math.sin(t*0.3+br)*5;
      x.beginPath();x.moveTo(bxx-16,byy);x.quadraticCurveTo(bxx-8,byy-10,bxx,byy);
      x.quadraticCurveTo(bxx+8,byy-10,bxx+16,byy);x.stroke();
    }
  }
  function scene1(){
    var pb=pi(0.06);
    x.save();x.translate(Math.sin(t*0.5)*5,0);
    paperBlob(W*0.50,H*0.64,250,44,0,DEEP,20,8,pb);
    paperBlob(W*0.50,H*0.52,170,120,0,CREAM,18,8,pb);
    paperBlob(W*0.50,H*0.385,196,78,-0.03,CREAM3,22,8,pb);
    paperStrip(W*0.50,H*0.585,56,84,0,CREAM2,14,6,pb);
    for(var wi=0;wi<4;wi++){
      var wx2=W*0.50+(wi%2?78:-78),wy2=H*0.505+(wi<2?0:46);
      var glow=0.35+0.65*Math.sin(t*(4+wi*0.7)+wi*2.4);
      paperBlob(wx2,wy2,26,26,Math.sin(t*0.6+wi)*0.05,wi%2?GOLD:CREAM2,10,3,pb);
      x.fillStyle='rgba(165,29,36,'+(0.15+0.25*glow)+')';
      x.beginPath();x.arc(wx2,wy2,12+(0.5+0.5*Math.sin(t*4+wi*1.3))*8,0,6.2832);x.fill();
    }
    x.strokeStyle='rgba(165,29,36,0.7)';x.lineWidth=3;
    for(var dd=0;dd<6;dd++){
      var dw=W*0.50-170+dd*62;
      for(var dl=0;dl<3;dl++){
        var ly=H*0.58+dl*16+seed(dd*3.1+dl)*8;
        x.beginPath();x.moveTo(dw,ly);x.lineTo(dw+10+seed(dd*1.7)*6,ly+14);x.stroke();
      }
    }
    x.restore();
    paperStrip(W*0.21,H*0.42,120,46,-0.12,CREAM,12,5,pb*pi(0.22));
    paperText('EVID-0'+(i0+1),W*0.21,H*0.42,26,-0.12,MUTE,0.9*pi(0),2);
  }
  function scene2(){
    var pb=pi(0.06);
    x.save();
    paperBlob(W*0.50,H*0.52,178,220,0,CREAM3,20,9,pb);
    x.strokeStyle='rgba(80,55,25,0.6)';x.lineWidth=4;
    x.beginPath();x.moveTo(W*0.50-150,H*0.64);x.lineTo(W*0.50+150,H*0.64);x.stroke();
    paperBlob(W*0.50,H*0.52,120,150,0,CREAM,16,6,pb);
    paperBlob(W*0.50,H*0.46,26,26,0,PAP,8,3,pb);
    x.beginPath();x.arc(W*0.50,H*0.46,12,0,6.2832);x.strokeStyle='rgba(80,55,25,0.6)';x.stroke();
    x.restore();
    paperBlob(W*0.50,H*0.30,64,52,-0.03+Math.sin(t*0.4)*0.02,CREAM2,18,7,pi(0.18));
    paperBlob(W*0.50,H*0.255,30,30,0,CREAM2,10,4,pi(0.18));
    x.strokeStyle='rgba(90,62,30,0.8)';x.lineWidth=6;
    x.beginPath();x.arc(W*0.50,H*0.255,20,-0.9,0.9);x.stroke();
    paperBlob(W*0.50,H*0.255,8,8,0,RED,4,0,pi(0.2));
    paperBlob(W*0.80,H*0.36,86,40,0.16,PAP,14,6,pi(0.26));
    paperText('VERIFIED',W*0.80,H*0.36,30,0.16,RED,0.95*pi(0),2);
    paperText('NO FORCED ENTRY',W*0.80,H*0.40,20,0.16,MUTE,0.9*pi(0),1);
  }
  function scene3(){
    var pb=pi(0.06);
    x.save();x.translate(Math.sin(t*0.4)*4,0);
    paperBlob(W*0.50,H*0.56,150,200,0,CREAM3,20,8,pb);
    paperBlob(W*0.66,H*0.50,64,130,-0.16,CREAM,14,6,pb);
    for(var s=0;s<3;s++)paperStrip(W*0.50,H*(0.44+0.07*s),250,10,0,CREAM2,9,3,pb*pi(s*0.06));
    paperStrip(W*0.42,H*0.585,54,76,0.06,PAP,12,5,pb);
    paperStrip(W*0.60,H*0.60,40,40,0.08,CREAM2,10,4,pb);
    x.fillStyle=INK;x.beginPath();x.arc(W*0.60,H*0.60,8,0,6.2832);x.fill();
    paperStrip(W*0.52,H*0.66,18,6,0.6,GOLD,6,2,pb);
    for(var cr=0;cr<14;cr++){x.fillStyle='rgba(60,40,15,0.5)';
      x.fillRect(W*0.44+seed(cr*1.1)*W*0.14,Math.sin(t*0.8+cr)*1+H*0.67+seed(cr*2.3)*14,4,4);}
    x.restore();
    paperBlob(W*0.84,H*0.30,56,56,0,CREAM2,12,6,pi(0.2));
    x.strokeStyle='rgba(60,42,20,0.6)';x.lineWidth=3;
    x.beginPath();x.moveTo(W*0.84,H*0.30);x.lineTo(W*0.84,H*0.20);x.stroke();
    x.beginPath();x.moveTo(W*0.84,H*0.30);x.lineTo(W*0.87,H*0.30);x.stroke();
    x.fillStyle=MUTE;x.font='700 22px Arial';x.textAlign='center';
    x.fillText('4 AM',W*0.84,H*0.365);x.textAlign='left';
  }
  function scene4(){
    var pb=pi(0.06);
    paperBlob(W*0.50,H*0.50,186,120,0,CREAM,14,7,pb);
    paperBlob(W*0.50,H*0.40,210,44,0.03,CREAM2,16,6,pb);
    x.strokeStyle='rgba(90,62,30,0.8)';x.lineWidth=7;
    x.beginPath();x.moveTo(W*0.58,H*0.38);x.lineTo(W*0.56,H*0.27);
    x.lineTo(W*0.565,H*0.27);x.stroke();
    x.lineWidth=3;
    x.beginPath();x.arc(W*0.565,H*0.27,3,0,6.2832);x.fillStyle=RED;x.fill();
    x.strokeStyle='rgba(124,21,24,0.85)';x.lineWidth=4;
    for(var sw=0;sw<5;sw++){
      var rr0=16+sw*12,aa0=t*(0.6+sw*0.12)+sw*1.6;
      x.beginPath();x.arc(W*0.50,H*0.52,rr0,aa0,aa0+2.0);x.stroke();
    }
    x.strokeStyle='rgba(124,21,24,0.7)';x.lineWidth=5;
    for(var dr2=0;dr2<5;dr2++){
      var dx2=W*(0.38+dr2*0.11)+Math.sin(t*0.7+dr2)*3;
      var dy2=H*(0.57+0.02*Math.sin(t*0.5+dr2));
      x.beginPath();x.moveTo(dx2,dy2);x.lineTo(dx2,dy2+18+seed(dr2*3.3)*16);x.stroke();
      x.beginPath();x.arc(dx2,dy2+22+seed(dr2*3.3)*16,5,0,6.2832);x.fill();
    }
    paperStrip(W*0.79,H*0.42,104,36,-0.10,CREAM2,10,5,pb*pi(0.18));
    paperText('BLOOD.',W*0.79,H*0.42,26,-0.10,RED,0.92*pi(0),2);
  }
  function scene5(){
    var pb=pi(0.06);
    x.save();x.translate(Math.sin(t*0.5)*6,0);
    paperBlob(W*0.62,H*0.53,92,200,-0.05,CREAM3,20,8,pb);
    paperBlob(W*0.62,H*0.335,34,34,-0.05,PAP,12,5,pb);
    paperBlob(W*0.92,H*0.50,30,44,0.3,CREAM2,10,4,pb);
    for(var lg=0;lg<4;lg++){
      x.strokeStyle='rgba(52,32,12,0.5)';x.lineWidth=6;
      x.beginPath();
      x.moveTo(W*0.62,H*0.62);
      x.lineTo(W*0.62+(lg%2?34:-34),H*0.68+Math.sin(t*0.9+lg*1.5)*4);x.stroke();
    }
    var tags3=['JACKET','BELT','KNIFE'];
    for(var tg=0;tg<3;tg++){
      var sx5=W*(0.30+tg*0.11)+Math.sin(t*0.6+tg*2)*4,sy5=H*(0.42+tg*0.075);
      paperStrip(sx5,sy5,118,40,0.55-tg*0.06,CREAM,10,4,pb*pi(tg*0.08));
      paperText(tags3[tg],sx5,sy5,24,0.55-tg*0.06,INK,0.9*pi(0),2);
    }
    x.restore();
    x.strokeStyle='rgba(70,90,120,0.35)';x.lineWidth=3;
    for(var rn=0;rn<12;rn++){
      var rx2=(seed(rn*5.1)*W+Math.sin(t*1.6+rn)*4)%W,ry2=(seed(rn*3.3)*H+t*(240+seed(rn)*180))%(H*0.7);
      x.beginPath();x.moveTo(rx2,ry2);x.lineTo(rx2-22,ry2+40);x.stroke();
    }
  }
  function scene6(){
    var pb=pi(0.06);
    x.save();x.translate(Math.sin(t*0.4)*4,0);
    paperStrip(W*0.40,H*0.50,210,120,-0.10,CREAM,14,7,pb);
    paperStrip(W*0.60,H*0.52,210,130,0.10,CREAM2,14,7,pb*pi(0.08));
    paperStrip(W*0.28,H*0.34,64,40,-0.5,CREAM3,10,4,pb);
    paperBlob(W*0.76,H*0.36,66,84,0,PAP,10,6,pi(0.2));
    x.fillStyle=MUTE;x.font='700 26px Arial';x.textAlign='center';
    x.fillText('2000',W*0.76,H*0.33);x.fillText('... 25yrs',W*0.76,H*0.37);
    x.font='700 18px Arial';x.fillStyle=RED;x.fillText('2026',W*0.76,H*0.40);x.textAlign='left';
    x.strokeStyle=GOLD;x.lineWidth=3;
    x.beginPath();x.arc(W*0.76,H*0.50,30,0,6.2832);x.stroke();
    paperBlob(W*0.76,H*0.50,20,20,0,CREAM2,8,2,pi(0.26));
    x.strokeStyle='rgba(60,42,20,0.7)';x.lineWidth=5;
    x.beginPath();x.arc(W*0.76,H*0.50,16,0,6.2832);x.stroke();
    x.restore();
    paperStrip(W*0.50,H*0.62,120,34,0.02,PAP,12,5,pi(0.3));
    paperText('OPEN CASE',W*0.50,H*0.62,28,0.02,RED,0.95*pi(0),2);
  }
  function scene7(){
    var pb=pi(0.06);
    x.save();x.translate(Math.sin(t*0.4)*5,0);
    x.strokeStyle=ILINE;x.lineWidth=10;x.lineCap='round';
    for(var r=0;r<2;r++){
      x.globalAlpha=1;
      x.beginPath();
      for(var q=0;q<26;q++){
        var qp=q/26*6.2832*2,yy=H*0.40+qp/12.5664*(H*0.22);
        var xx=W*0.50+Math.sin(qp*1.5708+r*3.1416)*(70+Math.sin(t*0.6+r)*8);
        if(q===0)x.moveTo(xx,yy);else x.lineTo(xx,yy);
      }
      x.stroke();
      x.globalAlpha=1;
    }
    var ph=i0*3.1416;
    for(var qb=0;qb<34;qb++){
      var qpb=qb/34*6.2832*2,yyb=H*0.40+qpb/12.5664*(H*0.22);
      var sfb=Math.sin(qpb*1.5708+ph);
      x.strokeStyle=GOLD;x.lineWidth=4;x.globalAlpha=0.9;
      x.beginPath();x.moveTo(W*0.50+(70+Math.sin(t*0.6+qb)*6)*sfb,yyb);
      x.lineTo(W*0.50-(70+Math.sin(t*0.6+qb)*6)*sfb,yyb);x.stroke();
      x.globalAlpha=1;
    }
    paperBlob(W*0.28,H*0.32,46,56,0.2,PAP,10,4,pi(0.24));
    x.strokeStyle='rgba(60,42,20,0.6)';x.lineWidth=2;
    for(var fp=0;fp<3;fp++){x.beginPath();x.arc(W*0.28,H*(0.32+fp*0.022),14-fp*4,0,6.2832);x.stroke();}
    x.restore();
    paperStrip(W*0.50,H*0.66,150,40,0.00,PAP,12,6,pi(0.3));
    paperText('THE CLOCK NEVER STOPS',W*0.50,H*0.66,26,0,RED,0.95*pi(0),2);
  }
  drawStand();
  // paper-tremble weave across the whole diorama
  x.save();x.translate(Math.sin(t*1.7)*4+Math.sin(t*0.6)*3,Math.cos(t*2.3)*3.5+Math.cos(t*0.8)*2);
  switch(i0){
    case 0:scene0();break; case 1:scene1();break; case 2:scene2();break;
    case 3:scene3();break; case 4:scene4();break; case 5:scene5();break;
    case 6:scene6();break; case 7:scene7();break; default:scene0();
  }
  // red evidence seals stamped onto the board, one per beat
  var sealPos=[[0.83,0.23,-0.13,54],[0.18,0.29,0.15,56],[0.815,0.345,0.20,52],
               [0.155,0.265,0.11,56],[0.815,0.30,0.13,52],[0.21,0.20,0.11,54],
               [0.845,0.235,0.15,52],[0.79,0.625,0.10,58]];
  var sp=sealPos[i0];
  stampSeal(W*sp[0],H*sp[1],sp[3],sp[2],'SEAL 0'+(i0+1));
  x.restore();

  // ---- headline: paper-cut type, snapped in (no alpha fade) + beat jump ----
  var beatTxt=String(b.text);
  var hsz=beatTxt.length>44?62:(beatTxt.length>26?84:112);
  var hlns=wrapH(beatTxt,hsz,Math.min(W-230,920));
  while(hlns.length>3&&hsz>46){hsz-=6;hlns=wrapH(beatTxt,hsz,Math.min(W-230,920));}
  var hseq=eo(cl((t-bs)/0.30)),hk=1+0.10*(1-hseq),hof=(1-hseq)*(1-hseq)*-20;
  var hcy=H*0.135;
  for(var hl=0;hl<hlns.length;hl++){
    var cly=hcy+hl*(hsz*1.02);
    x.save();x.translate(W/2,hof+cly);x.scale(hk,hk);
    paperText(hlns[hl],0,0,hsz,(hl%2?-0.008:0.008)+Math.sin(t*0.3+hl)*0.008,INK,hseq,6);
    x.restore();
  }
  // beat jump line (red) under headline (hidden during final whole-board reveal)
  if(!finalPhase){
    var pp=ease(cl((t-bs)/len));
    x.strokeStyle=RED;x.lineWidth=6;x.lineCap='round';
    x.beginPath();x.moveTo(W/2-150,H*0.30);x.lineTo(W/2-150+Math.max(6,(W-300)*pp),H*0.30);x.stroke();
    x.fillStyle=RED;x.beginPath();x.arc(W/2-150+Math.max(6,(W-300)*pp),H*0.30,7,0,6.2832);x.fill();
  }

  x.restore();

  // ---- beat-cut shock ring ----
  var ri=cl(seg(t,bs,0.55));
  if(ri>0&&ri<1){
    var rr=1-ri;
    x.strokeStyle='rgba(165,29,36,'+(0.45*rr)+')';x.lineWidth=Math.max(2,14*rr);
    x.beginPath();x.arc(W/2,H*0.45,40+(W*0.42)*easeBack(ri),0,6.2832);x.stroke();
  }

  // ---- full torn-paper sheet wipe on beat cuts (sheet lifts up) ----
  var wd=eo(cl(seg(t,bs,0.30)));
  var coverTop=H*(1-wd)*1.25;
  if(coverTop>6){
    x.save();
    var gr=x.createLinearGradient(0,coverTop,0,H);
    gr.addColorStop(0,'#fbf6e6');gr.addColorStop(.25,'#f3ead2');gr.addColorStop(1,'#e3d2ae');
    x.fillStyle=gr;x.beginPath();x.moveTo(0,H);
    for(var iw=0;iw<=W;iw+=14){x.lineTo(iw,H);}
    x.lineTo(W,Math.min(H,coverTop));
    for(var ix=W;ix>=0;ix-=14){x.lineTo(ix,Math.min(H,coverTop+(seed(ix*0.13+i0*7)-.5)*64));}
    x.lineTo(0,H);x.closePath();x.fill();
    x.strokeStyle='rgba(165,29,36,'+(0.9*(1-wd))+')';x.lineWidth=3;
    x.beginPath();x.moveTo(0,Math.min(H,coverTop));
    for(var iy=0;iy<=W;iy+=14){x.lineTo(iy,Math.min(H,coverTop+(seed(iy*0.13+i0*7)-.5)*64));}
    x.stroke();
    for(var tr=0;tr<6;tr++){
      var txx=seed(tr*3.1+i0)*W,tyv=Math.min(H,coverTop+ (seed(tr*1.7)-.5)*90 - 20 - tr*10);
      paperStrip(txx,tyv,26+seed(tr)*18,12,Math.sin(tr*2+t*0.5)*0.5,tr%2?CREAM:PAP,6,0,0.7);
    }
    x.restore();
  }

  // ---- caption chip: paper note pinned in the karaoke band (auto-fitting) ----
  if(CAPS&&CAPS.length){
    for(var cp=0;cp<CAPS.length;cp++){
      var c0=CAPS[cp][0],c1=CAPS[cp][1];
      if(t>=c0&&t<c1){
        var wts=String(CAPS[cp][2]).split(' ');
        var fs=58,LINES=null,maxW=W-200;
        while(fs>=34&&!LINES){
          x.font='900 '+fs+'px Impact,"Arial Black",Arial';x.textAlign='left';x.textBaseline='alphabetic';
var lns=[],cur=wts[0],cw=0;
          for(var wi2=1;wi2<wts.length;wi2++){
            var tt=cur+' '+wts[wi2];
            if(x.measureText(tt).width>maxW&&cur!==''){lns.push(cur);cur=wts[wi2];}else cur=tt;
          }
          lns.push(cur);
          if(lns.length<=2)LINES=lns;else fs-=2;
        }
        if(!LINES){fs=30;x.font='900 '+fs+'px Impact,"Arial Black",Arial';LINES=wts.join(' ').match(/.{1,24}/g)||[wts.join(' ')];}
        x.font='900 '+fs+'px Impact,"Arial Black",Arial';x.textAlign='left';x.textBaseline='alphabetic';
        var mLW=0,lGap=[];
        for(var li=0;li<LINES.length;li++){
          var lW=0,wtt=LINES[li].split(' ');
          for(var wj=0;wj<wtt.length;wj++)lW+=x.measureText(wtt[wj]+' ').width;
          lW-=x.measureText(' ').width;lGap.push(lW);if(lW>mLW)mLW=lW;
        }
        var chipW=Math.min(mLW+170,W-140);
        var nL=LINES.length,chipH=nL>1?178:120,cyy=H*0.73;
        paperStrip(W/2,cyy,chipW,chipH,0.0,CREAM,18,9,0.97);
        tape(W/2-chipW/2+16,cyy-chipH/2+12,30,0.85);tape(W/2+chipW/2-16,cyy-chipH/2+12,30,-0.85);
        tape(W/2-chipW/2+16,cyy+chipH/2-12,30,-0.85);tape(W/2+chipW/2-16,cyy+chipH/2-12,30,0.85);
        var WT=CAPS[cp][3];
        var aw=0;
        if(WT&&WT.length===wts.length){
          for(var wk=0;wk<WT.length;wk++){ if(WT[wk][1]<=t+0.002&&wk<WT.length-1){aw=wk+1; } }
        } else {
          var frac=cl((t-c0)/(c1-c0));
          aw=Math.min(wts.length-1,Math.floor(frac*wts.length));
        }
        var gi=0;
        x.textAlign='left';x.textBaseline='middle';x.font='900 '+fs+'px Impact,"Arial Black",Arial';
        for(var li2=0;li2<nL;li2++){
          var tw=LINES[li2].split(' '),totw=lGap[li2],xx0=W/2-totw/2,lcy=cyy-(nL>1?fs*0.62:0)+(li2*(fs*1.12));
          var bsx=[];
          for(var wi=0;wi<tw.length;wi++){bsx.push(x.measureText(tw[wi]+' ').width);}
          for(var wid=0;wid<tw.length;wid++){
            var act=(gi===aw);
            x.save();x.translate(xx0+bsx[wid]/2,lcy);
            if(act){x.scale(1.24,1.24);x.rotate(0.022*Math.sin(t*7));}
            x.font='900 '+fs+'px Impact,"Arial Black",Arial';x.textAlign='center';x.textBaseline='middle';
            x.lineWidth=4;x.lineJoin='round';x.strokeStyle='rgba(43,35,32,0.28)';
            x.strokeText(tw[wid],0,0);
            x.fillStyle=act?RED:INK;x.fillText(tw[wid],0,0);
            x.restore();
            if(act){
              x.strokeStyle=RED;x.lineWidth=6;x.lineCap='round';
              x.beginPath();x.moveTo(xx0+4,lcy+fs*0.66);x.lineTo(xx0+bsx[wid]-8,lcy+fs*0.72);x.stroke();
            }
            xx0+=bsx[wid];gi++;
          }
        }
        x.textAlign='left';x.textBaseline='alphabetic';
        break;
      }
    }
  }

  // ---- top-left: red case stamp + STATUS paper tag ----
  var STATUS=['SITE UNSEALED','ENTRY','VICTIMS','KILLER INSIDE','STAYED','EVIDENCE','OPEN','STILL OPEN'];
  var stp=eo(cl((t-bs)/0.55));
  if(stp>0.03){
    x.save();x.translate(66,98);x.rotate(-0.05);
    if(stp<1)x.scale(Math.max(0.05,stp),Math.max(0.05,stp));
    x.strokeStyle=RED;x.lineWidth=4;x.lineCap='round';
    x.beginPath();x.arc(0,0,36,0,6.2832);x.stroke();
    x.beginPath();x.arc(0,0,31,0,6.2832);x.stroke();
    x.font='900 14px Impact,Arial';x.textAlign='center';x.textBaseline='middle';
    x.fillStyle=RED;x.fillText('CASE',0,-17);x.fillText('FILE',0,-3);
    x.font='900 12px Impact,Arial';x.fillText('0'+(i0+1)+'/'+('0'+tot),0,12);
    x.strokeStyle=RED;x.lineWidth=2;x.beginPath();x.moveTo(-30,24);x.lineTo(30,24);x.stroke();
    x.restore();
  }
  var SSTAT=STATUS[i0];
  paperStrip(166,76,Math.min(300,60+SSTAT.length*12.5),38,0.02,MUTE,10,4,0.95*cl((t-bs)/0.5));
  x.font='700 20px Arial';x.textAlign='left';x.textBaseline='middle';
  x.fillStyle=PAP;x.fillText('STATUS: '+SSTAT,174,76);
  x.textBaseline='alphabetic';x.textAlign='left';

  // ---- bottom progress strip ----
  var bx0=60,bx1=W-60,by0=H-46;
  x.strokeStyle='rgba(90,70,40,0.35)';x.lineWidth=4;
  x.beginPath();x.moveTo(bx0,by0);x.lineTo(bx1,by0);x.stroke();
  var pw2=(bx1-bx0)*eo(cl(t/DUR));
  x.strokeStyle=RED;x.lineWidth=8;x.lineCap='round';
  x.beginPath();x.moveTo(bx0,by0);x.lineTo(bx0+pw2,by0);x.stroke();
  x.fillStyle=MUTE;x.font='700 24px Arial';
  x.textAlign='left';x.fillText(fmt(t)+' / '+fmt(DUR),bx0,by0-16);
  x.textAlign='right';x.fillText('PAPER CUT',bx1,by0-16);
  x.textAlign='left';

  // ---- finish: grain dust + vignette ----
  var gg2=Math.floor(t*30);
  for(var gr2=0;gr2<460;gr2++){x.fillStyle='rgba(90,70,40,0.040)';x.fillRect(seed(gr2*1.7+gg2*13.1)*W,seed(gr2*2.3+gg2*7.7)*H,1,1);}
  for(var gr3=0;gr3<70;gr3++){x.fillStyle='rgba(60,40,18,0.06)';x.fillRect(seed(gr3*3.1+gg2*5.3)*W,seed(gr3*4.7+gg2*3.1)*H,2,2);}
  var vg=x.createRadialGradient(W/2,H*0.5,H*0.3,W/2,H*0.5,H*0.95);
  vg.addColorStop(0,'rgba(70,50,20,0)');vg.addColorStop(1,'rgba(60,40,18,0.22)');
  x.fillStyle=vg;x.fillRect(0,0,W,H);
}



window.__render=frame;
})();
</script></body></html>""".replace("__W__", str(W)).replace("__H__", str(H)).replace(
        "__DUR__", str(float(clip["duration"]))).replace(
        "__ACCENT__", clip.get("accent", "#ff4b3a")).replace(
        "__MOTIF__", clip.get("motif", "kinetic")).replace(
        "__BADGE__", "true" if clip.get("badge", True) else "false").replace(
        "__CTA__", json.dumps(clip.get("cta", ""))).replace(
        "__BEATS__", beat_json).replace(
        "__CAPS__", caps_json)


# ------------------------------- frame render ------------------------------ #
def render_frames(clip, html, fps):
    notify("frames: %dx%d @ %d (n=%d)" % (
        W := int(clip.get("width", 1080)),
        H := int(clip.get("height", 1920)),
        fps, int(clip["duration"] * fps)))
    from playwright.sync_api import sync_playwright
    nf = int(clip["duration"] * fps)
    shutil.rmtree(FRAMES, ignore_errors=True); FRAMES.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=[
            "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
            "--force-color-profile=srgb"])
        page = b.new_page(viewport={"width": W, "height": H},
                          device_scale_factor=1)
        page.set_content(html)
        step = max(1, nf // 10)
        for i in range(nf):
            t = i / fps
            page.evaluate("__render(%s)" % repr(t))
            page.screenshot(path=str(FRAMES / ("f_%05d.png" % i)),
                            clip={"x": 0, "y": 0, "width": W, "height": H})
            if i and i % step == 0:
                notify("frames %d%% (%d/%d)" % (i * 100 // nf, i, nf))
        b.close()


# ------------------------------- ffmpeg mix -------------------------------- #
def _enc_ok(name):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                       capture_output=True, text=True)
    return any(l.strip().startswith(name + " ") or (" " + name + " ") in l
               for l in r.stdout.splitlines())


def _codec_args():
    want = [os.environ.get("YT_ENCODER")] if os.environ.get("YT_ENCODER") \
        else ["libx264", "libx265", "mpeg4"]
    enc = next((c for c in want if _enc_ok(c)), "mpeg4")
    args = ["-c:v", enc]
    if enc in ("libx264", "libx265"):
        args += ["-preset", "medium", "-crf", "19"]
    else:
        args += ["-q:v", "5"]
    notify("encoder: %s" % enc)
    return args


def mix(clip, fps, vo, bgm, ass):
    visual = OUT / "visual.mp4"
    _sh("ffmpeg", "-y", "-framerate", str(fps), "-i",
        str(FRAMES / "f_%05d.png"), *_codec_args(),
        "-pix_fmt", "yuv420p", "-g", str(fps * 2),
        str(visual))
    notify("visual encoded")
    final = OUT / "final.mp4"
    burn = ["-vf", "subtitles=" + str(ass) + ":fontsdir=" + str(FONTS)] \
        if ass and ass.exists() else []
    if bgm:
        fc = ("[1:a]aformat=sample_fmts=fltp:sample_rates=48000:"
              "channel_layouts=stereo[vo];"
              "[2:a]aformat=sample_fmts=fltp:sample_rates=48000:"
              "channel_layouts=stereo[bg];"
              "[vo][bg]sidechaincompress=threshold=0.03:ratio=6:attack=20:"
              "release=350[duck];"
              "[duck]loudnorm=I=-14:TP=-1.5:LRA=11[a0];"
              "[a0]afade=t=out:st=%.2f:d=0.35[aout]" % max(0.4, clip["duration"] - 0.35))
        _sh("ffmpeg", "-y", "-i", visual, "-i", vo, "-i", bgm,
            "-filter_complex", fc, "-map", "0:v", "-map", "[aout]",
            *burn, *_codec_args(),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags",
            "+faststart", final)
    else:
        _sh("ffmpeg", "-y", "-i", visual, "-i", vo,
            *burn, *_codec_args(),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k",
            "-af", "loudnorm=I=-14:TP=-1.5:LRA=11,"
                   "afade=t=out:st=%.2f:d=0.35" % max(0.4, clip["duration"] - 0.35),
            "-shortest", "-movflags", "+faststart", final)
    notify("final mix done: %s (%.1f MB)" % (final.name,
            final.stat().st_size / 1e6))
    return final


def receipt(clip, final, fps):
    sha = hashlib.sha256(final.read_bytes()).hexdigest()
    doc = {
        "title": clip.get("title", ""),
        "duration": clip["duration"], "fps": fps,
        "resolution": "%dx%d" % (clip.get("width", 1080), clip.get("height", 1920)),
        "frames": int(clip["duration"] * fps),
        "beats": [b["text"] for b in clip["beats"]],
        "output": final.name, "bytes": final.stat().st_size,
        "sha256": sha,
        "builtAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "channel": "yt_auto", "platform": "colab",
    }
    (OUT / "build.json").write_text(
        json.dumps(doc, indent=2), encoding="utf-8")
    return doc


# ---------------------------------- main ----------------------------------- #
def _norm(w):
    return "".join(ch for ch in str(w).lower() if ch.isalnum())


def _vo_sync_beats(clip, words):
    """Tile every beat's on-screen window over the narration, proportionally
    to that beat's text length. NEVER word-matches: narration paraphrases the
    headlines ("e-commerce trick" for "E-COM PRODUCT TRICK"), so greedy word
    anchoring lands headlines on the wrong words. Proportional tiling keeps
    every headline on screen while its section is being narrated — ordered,
    monotonic, can't misfire."""
    if not words or not clip.get("beats"):
        return
    beats = clip["beats"]
    ws = [max(1, len(str(b["text"]).split())) for b in beats]
    total = float(sum(ws))
    t0, w1 = words[0][0], words[-1][1]
    acc = 0.0
    for i, b in enumerate(beats):
        frac = ws[i] / total
        s = t0 + acc * (w1 - t0) - 0.30
        e = t0 + (acc + frac) * (w1 - t0) + 0.30
        b["start"] = float("%.2f" % max(0.0, s))
        b["end"] = float("%.2f" % e)
        acc += frac
    for b in beats:
        if float(b["end"]) < float(b["start"]) + 0.6:
            b["end"] = float("%.2f" % (float(b["start"]) + 0.6))


def main(clip_path=None, fps=None):
    shutil.rmtree(INP, ignore_errors=True); INP.mkdir(exist_ok=True)
    src = clip_path or find_input()
    clip = load_clip(src)
    fps = fps or int(clip.get("fps", 30))
    notify("clip: %s  beats=%d" % (clip.get("title", src.name),
                                   len(clip["beats"])))
    vo, bgm, words, sents = ensure_audio(clip)
    vo_dur = _ffprobe_dur(vo)
    if vo_dur > 0.5:
        # finishing: video = narration + pad, last line lands with the last word —
        # no dead frozen tail, no abrupt open-ended freeze.
        clip["duration"] = float("%.3f" % (vo_dur + 0.45))
    else:
        clip["duration"] = float(clip.get("duration") or 3)
    _vo_sync_beats(clip, words)
    clip["beats"][-1]["end"] = clip["duration"]
    caps = resolve_captions(clip, words, sents)
    if caps:
        notify("captions: %d (word-sync=%s)" % (
            len(caps), "yes" if words else "phrase"))
    else:
        notify("captions: none")
    html = build_html(clip, caps=caps)
    render_frames(clip, html, fps)
    final = mix(clip, fps, vo, bgm, None)
    doc = receipt(clip, final, fps)
    notify("RENDER COMPLETE — %s %s %.1fMB sha256:%s" % (
        doc["title"], doc["resolution"], doc["bytes"] / 1e6,
        doc["sha256"][:12]))


main()
'''

# --------------------------------------------------------------------------- #
# Cell 3 — dispatch                                                             #
# --------------------------------------------------------------------------- #
CELL_DISPATCH = r'''#@title 3/3 — Dispatch: Telegram video + Google Drive + download
import json
import shutil
from pathlib import Path

from google.colab import files

WORK = Path("/content/yt_auto")
OUT  = WORK / "out"
final = OUT / "final.mp4"

if not final.exists():
    print("final.mp4 missing — did cell 2 run?")
    raise SystemExit(1)

doc = json.loads((OUT / "build.json").read_text(encoding="utf-8"))
cap = "yt_auto render complete:\n%s\n%s @ 30fps · %d frames\n%d MB · sha256 %s" % (
    doc.get("title", "short"), doc.get("resolution"), doc.get("frames", 0),
    final.stat().st_size / 1e6, doc.get("sha256", "?")[:12])

# 1) Telegram (primary, per spec: curl)
sent = tg_send_video(final, caption=cap)

# 2) Google Drive when mounted
drv = Path("/content/drive/MyDrive/yt_auto_out")
try:
    if drv.exists():
        drv.mkdir(parents=True, exist_ok=True)
        for f in (final, OUT / "captions.srt", OUT / "build.json"):
            if f.exists():
                shutil.copy(f, drv / f.name)
        print("copied to Google Drive:", drv)
        tg_send("Drive copy: %s" % drv)
except Exception as e:
    print("drive:", e)

# 3) download fallback (always available in the browser)
files.download(str(final))
print("DONE. Local copy also at", final)
'''

# --------------------------------------------------------------------------- #
# Cell 0 — header                                                               #
# --------------------------------------------------------------------------- #
CELL_HEAD = ("# yt_auto — Colab render bridge\n\n"
             "Renders the full shorts pipeline on Colab (Playwright/Chromium "
             "canvas, Edge-TTS, FFmpeg) with **0% local load**.\n\n"
             "**Run:** `Runtime -> Run all` (requests a GPU/T4 runtime)\n\n"
             "**Input (one zip):** `clip.json` + optional `vo.mp3`/`bgm.mp3`\n"
             "- put `clip_N.zip` in Drive at `MyDrive/yt_auto/`, **or**\n"
             "- it will prompt you to upload it in cell 2.\n\n"
             "**Secrets (left key icon):** create `TELEGRAM_BOT_TOKEN` "
             "(token from `~/.local/var/tg-token.env` on the host).\n"
             "Optional overrides: `TELEGRAM_CHAT_ID`, `TELEGRAM_THREAD_ID`.\n\n"
             "**Output:** `/content/yt_auto/out/final.mp4` + `captions.srt` + "
             "`build.json` — sent to Telegram and Drive in cell 3.\n\n"
             "Cells: 1) env 2) render 3) dispatch.")


def build(out_path: pathlib.Path, clip_url: str = "",
          tg_chat: str | None = None, tg_thread: str | None = None) -> None:
    def cell(kind, source):
        return {"cell_type": kind,
                "metadata": {},
                "source": source.splitlines(keepends=True),
                "outputs": [] if kind == "code" else None,
                "execution_count": None if kind == "code" else None}

    tg_block = ""
    if tg_chat or tg_thread:
        lines = []
        if tg_chat:
            lines.append("os.environ.setdefault(%r, %r)" %
                         ("TELEGRAM_CHAT_ID", tg_chat))
        if tg_thread:
            lines.append("os.environ.setdefault(%r, %r)" %
                         ("TELEGRAM_THREAD_ID", tg_thread))
        tg_block = "\n".join(lines) + "\n"

    render_src = CELL_RENDER
    if "__CLIP_URL_DEFAULT__" in render_src:
        render_src = render_src.replace("__CLIP_URL_DEFAULT__", clip_url)
    if "__TG_DEFAULTS__" in render_src:
        render_src = render_src.replace("# __TG_DEFAULTS__",
                                        tg_block or "# __TG_DEFAULTS__")
    if "__CLIP_URL_DEFAULT__" in render_src:
        raise RuntimeError("placeholder not replaced: $clip_url")

    nb = {
        "nbformat": 4,
        "nbformat_minor": 0,
        "metadata": {
            "kernelspec": {"name": "python3", "display_name": "Python 3"},
            "accelerator": "GPU",
            "colab": {"provenance": [], "gpuType": "T4", "toc_visible": True},
        },
        "cells": [
            {"cell_type": "markdown", "metadata": {},
             "source": CELL_HEAD.splitlines(keepends=True)},
            cell("code", CELL_ENV),
            cell("code", render_src),
            cell("code", CELL_DISPATCH),
        ],
    }
    out_path.write_text(json.dumps(nb, indent=1), encoding="utf-8")
    # round-trip proof we wrote a valid notebook
    json.loads(out_path.read_text(encoding="utf-8"))
    kinds = [c["cell_type"] for c in nb["cells"]]
    print("wrote %s (%d bytes) cells=%s" %
          (out_path, out_path.stat().st_size, kinds))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "yt_auto_colab.ipynb"))
    ap.add_argument("--clip-url", default="",
                    help="bake a CLIP_URL bridge (tunnel/raw zip) into cell 2")
    ap.add_argument("--tg-chat", default=None,
                    help="bake TELEGRAM_CHAT_ID default into cell 2")
    ap.add_argument("--tg-thread", default=None,
                    help="bake TELEGRAM_THREAD_ID default into cell 2")
    args = ap.parse_args()
    build(pathlib.Path(args.out), clip_url=args.clip_url,
          tg_chat=args.tg_chat, tg_thread=args.tg_thread)