#!/usr/bin/env python3
"""
yt_auto unified driver — ONE render engine, TWO hosts (three routes).

The skill ALWAYS interviews first (12 questions: topic, form, duration,
voice, motif, route, delivery, captions, bgm, badge, cta, quality) unless
--target or --answers is given. Format is derived — short === 9:16, long === 16:9.

Modes:
  run_pipeline.py pack [-n N] [--no-whisper]   build colab_input/clip_N.zip
  run_pipeline.py nb [--clip-url URL]          regenerate yt_auto_colab.ipynb
  run_pipeline.py push                         push notebook to gist/github
  run_pipeline.py local [-n N] [--fast]        run the worker here (fast smoke)
  run_pipeline.py test [--target T]            pack + nb + local E2E fast smoke
  run_pipeline.py all --answers q.json         interview answers as JSON (topic-aware), then route
  run_pipeline.py all [--target T]             pack + nb + full local render
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
CLIP_OUT = HERE / "colab_input"
TG_ENV = pathlib.Path.home() / ".local/var/tg-token.env"
SINK = pathlib.Path(tempfile.gettempdir()) / "colab-auto" / "sink"
TUNNEL_TXT = pathlib.Path(tempfile.gettempdir()) / "colab-auto" / "tunnel_url.txt"


def bridge_url() -> str:
    try:
        u = TUNNEL_TXT.read_text().strip()
        if u:
            return u.rstrip("/") + "/colab_input/"
    except OSError:
        pass
    return ""


def _dry_env() -> dict:
    e = dict(os.environ)
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY"):
        e.pop(k, None)
    return e


def _tg_env() -> dict:
    e = _dry_env()
    if TG_ENV.exists():
        try:
            for line in TG_ENV.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    e[k.strip()] = v.strip()
        except Exception:
            pass
    return e


def pack(n: int, use_whisper: bool) -> pathlib.Path:
    cmd = [sys.executable, str(HERE / "pack_clip.py"), str(n)]
    if not use_whisper:
        cmd.append("--no-whisper")
    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode:
        raise SystemExit("pack failed (returncode %d)" % r.returncode)
    return CLIP_OUT / ("clip_%d.zip" % n)


def remix_zip(n: int, drop_vo: bool = False, drop_bgm: bool = False,
              drop_caps: bool = False, accent: str | None = None,
              duration: float | None = None, voice: str | None = None,
              motif: str | None = None, badge: bool | None = None,
              cta: str | None = None, width: int | None = None,
              height: int | None = None, fps: int | None = None) -> pathlib.Path:
    """Rewrite clip_N.zip in place per the interview choices.

    clip.json already drives accent / duration / voice / motif / fps at render
    time, so the answers are patched into the zip (no engine change needed).
    """
    src = CLIP_OUT / ("clip_%d.zip" % n)
    with zipfile.ZipFile(src) as z:
        names = z.namelist()
        data = {p: z.read(p) for p in names}
    clip = json.loads(data["clip.json"])
    if accent:
        clip["accent"] = accent
    if duration:
        clip["duration"] = float(duration)
    if voice:
        clip["voice"] = voice
    if motif:
        clip["motif"] = motif
    if badge is not None:
        clip["badge"] = bool(badge)
    if cta is not None:
        clip["cta"] = cta
    if width:
        clip["width"] = int(width)
    if height:
        clip["height"] = int(height)
    if fps:
        clip["fps"] = int(fps)
    if drop_caps:
        clip["captions"] = "none"
    data["clip.json"] = json.dumps(clip, indent=2).encode()
    pam = [p for p in names if not (
        (drop_vo and p == "vo.mp3") or (drop_bgm and p == "bgm.mp3") or
        (drop_caps and (p.endswith((".srt", ".ass")) or "words" in p)))]
    tmp = src.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for p in pam:
            z.writestr(p, data[p])
    tmp.replace(src)
    changed = []
    if accent:
        changed.append("accent=%s" % accent)
    if duration:
        changed.append("duration=%.1fs" % duration)
    if voice:
        changed.append("voice=%s" % voice)
    if motif:
        changed.append("motif=%s" % motif)
    if badge is not None:
        changed.append("badge=%s" % ("on" if badge else "off"))
    if cta:
        changed.append("cta=%r" % cta)
    if width and height:
        changed.append("res=%dx%d" % (width, height))
    if fps:
        changed.append("fps=%d" % fps)
    if drop_vo:
        changed.append("drop vo.mp3")
    if drop_bgm:
        changed.append("drop bgm.mp3")
    if drop_caps:
        changed.append("drop captions")
    if changed:
        print("zip re-packed: %s" % ", ".join(changed))
    return src


def nb(out: pathlib.Path | None = None, clip_url: str | None = None,
       tg_chat: str | None = None, tg_thread: str | None = None) -> None:
    import build_colab_notebook as g
    g.build(out or (HERE / "yt_auto_colab.ipynb"),
            clip_url=(os.environ.get("YT_CLIP_URL", "")
                      if clip_url is None else clip_url),
            tg_chat=tg_chat, tg_thread=tg_thread)


def push() -> None:
    r = subprocess.run([sys.executable, str(HERE / "colab_push.py")], cwd=HERE)
    if r.returncode:
        raise SystemExit("push failed (returncode %d)" % r.returncode)


def fast_clip(n: int) -> tuple[pathlib.Path, str]:
    src = CLIP_OUT / ("clip_%d.zip" % n)
    with zipfile.ZipFile(src) as z:
        data = {p: z.read(p) for p in z.namelist()}
    clip = json.loads(data["clip.json"])
    title = clip.get("title", "clip %d" % n)
    clip.update(duration=15, width=720, height=1280, fps=15)
    data["clip.json"] = json.dumps(clip, indent=2).encode()
    out = pathlib.Path(tempfile.gettempdir()) / "colab-auto" / ("fast_%d.zip" % n)
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, blob in data.items():
            z.writestr(name, blob)
    return out, title


def local(n: int, fast: bool) -> None:
    import build_colab_notebook as g
    src = g.CELL_RENDER
    clipz, title = fast_clip(n) if fast else (CLIP_OUT / ("clip_%d.zip" % n), "clip %d" % n)
    SINK.mkdir(parents=True, exist_ok=True)
    src = src.replace("/content/yt_auto", str(SINK))
    src = src.replace("__CLIP_URL_DEFAULT__", "")
    merge = _tg_env()                     # proxy-stripped + tg-token environ
    merge["CLIP_ZIP"] = str(clipz)
    merge.pop("YT_CLIP_URL", None)
    os.environ.update(merge)              # worker reads the real os.environ
    ns: dict = {}
    err = None
    try:
        exec(compile(src, "<yt_auto-worker-local>", "exec"), ns, ns)
    except SystemExit as e:
        err = e
    final = SINK / "out" / "final.mp4"
    if not final.exists():
        raise SystemExit("final.mp4 missing — worker failed" +
                         (" (%s)" % err if err else ""))
    send = ns.get("tg_send_video")
    if send and os.environ.get("TELEGRAM_BOT_TOKEN"):
        tag = "yt_auto local pipeline test" if fast else "yt_auto local full render"
        ok = send(final, caption="%s:\n%s\n%s @ %sfps" % (
            tag, title, "720x1280" if fast else "1080x1920", 15 if fast else 30))
        print("telegram sendVideo:", "ok" if ok else "failed/skipped")
    else:
        print("telegram dispatch: no token — skipped locally")
    print("local final: %s (%.1f MB)" % (final, final.stat().st_size / 1e6))
    print("local artifacts: %s" % SINK)


# ------------------------------ interview ------------------------------- #
VOICES = [
    ("keep", "keep the packed vo.mp3 (whisper phrase-sync captions)"),
    ("en-US-ChristopherNeural", "deep American male"),
    ("en-US-GuyNeural", "warm American male"),
    ("en-GB-RyanNeural", "British male"),
    ("en-GB-SoniaNeural", "British female"),
    ("en-US-JennyNeural", "bright American female"),
]
MOTIFS = [
    ("forensic", "dark forensic / crime archive — layered grunge, scanlines, red-stamp, redacted reveals, karaoke lower-third (STRICT PRODUCTION STANDARD)"),
    ("boiling-ink", "boiling-ink explainer — anime winter-window, hatched fills (reference #2)"),
    ("cloud-sea", "above the cloud sea — raymarched-sky glow, bloom (reference #1)"),
    ("cel", "cel shading — shader-painted sky, depth-of-field ghosts (reference #3)"),
    ("collage", "paper-cutout collage — halftone fills, torn edges (reference #4)"),
    ("pencil", "Tolstoy in pencil — boil linework, cross-hatch, warm grade (reference #5)"),
    ("kinetic", "punchy kinetic type — the classic yt_auto look"),
    ("glitch", "glitch — RGB-split ghosting, techy distortion"),
    ("matrix", "matrix — terminal scanlines, monospace green"),
    ("minimal", "minimal — restrained editorial, thin elegant accents"),
]
FORMATS = [
    ("9:16", "portrait short — 1080x1920 (the classic vertical)"),
    ("16:9", "landscape — 1920x1080 (YouTube wide)"),
    ("1:1", "square — 1080x1080 (feed friendly)"),
]
CAPTIONS = [
    ("auto", "match Q3 — phrase with packed vo.mp3, word-exact with fresh TTS"),
    ("word", "force exact word-sync Edge-TTS captions"),
    ("phrase", "force phrase-sync (needs the packed vo.mp3 kept)"),
    ("none", "no burnt captions at all"),
]
QUALITY = [
    ("p30", "full quality 30fps — smooth, heavier render"),
    ("p24", "standard 24fps — filmic, faster render"),
    ("p15", "draft 15fps — quick look, chunky"),
]
DEFAULTS = {"form": "long", "time": None, "time_def": None,
            "voice": "keep", "motif": "forensic",
            "route": "github", "delivery": "telegram",
            "orient": "9:16", "captions": "auto", "bgm": "with",
            "badge": True, "cta": "", "quality": "p30", "clip": 1,
            "topic": "batch_1"}

TOPICS: list[tuple[str, str]] = []
for _n in range(1, 6):
    _md = HERE / "production" / "scripts" / ("batch_%d.md" % _n)
    if _md.exists():
        _t = re.sub(r"^#\s*", "", _md.read_text(encoding="utf-8").splitlines()[0]).strip()
        TOPICS.append(("batch_%d" % _n, _t))
TOPICS.append(("manifesto", "EVERY FRAME IS CODE — the code-manifesto short (fresh TTS)"))


def _ask(label: str, opts: list[tuple[str, str]],
         default_idx: int = 1) -> str:
    print("\n%s" % label)
    for i, (k, what) in enumerate(opts, 1):
        mark = "  [default]" if i == default_idx else ""
        print("  %d) %s — %s%s" % (i, k, what, mark))
    while True:
        pick = input("pick 1-%d (Enter = %d): " % (len(opts), default_idx)).strip()
        if pick == "":
            return opts[default_idx - 1][0]
        if pick.isdigit() and 1 <= int(pick) <= len(opts):
            return opts[int(pick) - 1][0]
        for k, _ in opts:
            if pick == k:
                return k
        print("that was not a valid pick — try again")


def _ask_seconds(label: str, default: int) -> int | None:
    print("\n%s (blank keeps the clip's own duration)" % label)
    pick = input("seconds [%d]: " % default).strip()
    if pick == "":
        return default
    if pick.isdigit() and 1 <= int(pick) <= 3600:
        return int(pick)
    print("that should be seconds 1-3600 — keeping %d" % default)
    return default


def orient_for(form: str) -> str:
    return "9:16" if form == "short" else "16:9"


def run_card(q: dict) -> str:
    res = {"9:16": "1080x1920", "16:9": "1920x1080", "1:1": "1080x1080"}.get(
        q.get("orient"), orient_for(q["form"]))
    return "\n".join([
        "Run card (proposed)",
        "  Q1 topic    : %s" % q["topic"],
        "  Q2 form     : %s" % q["form"],
        "  Q3 duration : %s" % ("clip default" if not q.get("time")
                                else "%.1fs" % q["time"]),
        "  Q4 voice    : %s" % q["voice"],
        "  Q5 animation: %s" % q["motif"],
        "  Q6 route    : %s" % q["route"],
        "  Q7 delivery : %s" % (q["delivery"] if q["delivery"] != "custom"
                                else "custom %s/%s" % (q.get("tg_chat"),
                                                       q.get("tg_thread"))),
        "  format      : %s (%s, derived from %s)" % (res, q["orient"] or res,
                                                      q["form"]),
        "  Q8 captions : %s" % q["captions"],
        "  Q9 bgm      : %s" % q["bgm"],
        "  Q10 badge   : %s" % ("on" if q["badge"] else "off"),
        "  Q11 cta     : %s" % (q["cta"] or "none"),
        "  Q12 quality : %s" % q["quality"],
        "  clip        : %s" % q["topic"],
    ])
    lines = [
        "Run card (proposed)",
        "  Q1 form     : %s" % q["form"],
        "  Q2 duration : %s" % ("clip default" if not q.get("time")
                                else "%.1fs" % q["time"]),
        "  Q3 voice    : %s" % q["voice"],
        "  Q4 animation: %s" % q["motif"],
        "  Q5 route    : %s" % q["route"],
        "  Q6 delivery : %s" % (q["delivery"] if q["delivery"] != "custom"
                                else "custom %s/%s" % (q.get("tg_chat"),
                                                       q.get("tg_thread"))),
        "  Q7 format   : %s (%s)" % (q["orient"], res),
        "  Q8 captions : %s" % q["captions"],
        "  Q9 bgm      : %s" % q["bgm"],
        "  Q10 badge   : %s" % ("on" if q["badge"] else "off"),
        "  Q11 cta     : %s" % (q["cta"] or "none"),
        "  Q12 quality : %s" % q["quality"],
        "  packed      : batch %d" % q["clip"],
    ]
    return "\n".join(lines)


def interview() -> dict:
    """The skill interview — 12 questions. Topic is Q1; format is derived
    from form (short===9:16, long===16:9) so it is not asked."""
    q = dict(DEFAULTS)

    q["topic"] = _ask("Q1 — which topic?",
                      [(k, v) for k, v in TOPICS], default_idx=1)
    if q["topic"].startswith("batch_"):
        q["clip"] = int(q["topic"].split("_")[1])

    q["form"] = _ask("Q2 — long form or short form?",
                     [("long", "the real video (its full duration)"),
                      ("short", "a punchy short bite (rolling, hooks hard)")],
                     default_idx=0)
    q["time_def"] = 15 if q["form"] == "short" else None
    if q["form"] == "short":
        q["time"] = float(_ask_seconds("Q3 — how long is the short?", 15))
    else:
        q["time"] = None

    q["voice"] = _ask("Q4 — voiceover?", VOICES, default_idx=1)
    q["motif"] = _ask("Q5 — animation style (the coding style, not colors)?",
                      MOTIFS, default_idx=1)

    q["route"] = _ask("Q6 — where should it run?",
                      [("github", "push the notebook to a GitHub gist, open the one-click Colab URL"),
                       ("colab", "build the bridge notebook and open it straight in Google Colab"),
                       ("local", "run the same engine right here on this machine")],
                      default_idx=1)
    q["delivery"] = _ask("Q7 — where does the finished MP4 go?",
                         [("telegram", "your main Telegram thread (-1004375547101 / thread 992)"),
                          ("custom", "a different Telegram chat/thread id you type next"),
                          ("none", "no Telegram — final stays in Colab as a download / Drive")],
                         default_idx=1)
    q["tg_chat"] = q["tg_thread"] = None
    if q["delivery"] == "custom":
        q["tg_chat"] = input("  TELEGRAM_CHAT_ID (e.g. -1004375547101): ").strip() or None
        q["tg_thread"] = input("  TELEGRAM_THREAD_ID (0 / blank = main): ").strip() or None

    q["orient"] = orient_for(q["form"])
    q["captions"] = _ask("Q8 — captions?", CAPTIONS, default_idx=1)
    q["bgm"] = _ask("Q9 — background music?",
                    [("with", "duck BGM under the voice (bgm.mp3 if present)"),
                     ("none", "voice only")],
                    default_idx=1)
    q["badge"] = _ask("Q10 — big beat-number badge on the side?",
                      [("on", "the huge watermark index — signature look"),
                       ("off", "clean, no giant number")],
                      default_idx=1) == "on"
    q["cta"] = _ask("Q11 — end call-to-action card?",
                    [("none", "no end card — video ends on the last beat"),
                     ("subscribe", "SUBSCRIBE / new document every day"),
                     ("follow", "FOLLOW / 0 to 10k, one doc at a time"),
                     ("custom", "type your own two lines next")],
                    default_idx=1)
    if q["cta"] == "custom":
        l1 = input("  line 1 (headline): ").strip()
        l2 = input("  line 2 (subtext): ").strip()
        q["cta"] = ("%s\n%s" % (l1, l2)).strip()
    if q["cta"] == "none":
        q["cta"] = ""

    q["quality"] = _ask("Q12 — render quality / fps?", QUALITY, default_idx=1)

    # last: suggest the whole plan and only proceed on explicit approval
    while True:
        print("\n" + run_card(q))
        ok = input("approve and run? (y / n to redo): ").strip().lower()
        if ok in ("y", "yes"):
            return q
        if ok in ("n", "no"):
            print("redoing the interview…")
            return interview()
        print("type y or n")


# -------------------------------- main --------------------------------- # 
_TOPIC_CLIPS: dict[str, dict] = {
    "setagaya family murder": {
        "title": "SETAGAYA — THE FAMILY NOBODY FOUND",
        "accent": "#ff1e27",
        "beats": [
            {"start": 0, "end": 5, "text": "TOKYO. DECEMBER 31, 2000",
             "shot": "redacted file sheet, red stamp"},
            {"start": 5, "end": 11, "text": "A FAMILY OF FOUR. ALL DEAD.",
             "shot": "crime scene tape, evidence map"},
            {"start": 11, "end": 17, "text": "NOBODY BROKE IN.",
             "shot": "front door polaroid, lock intact"},
            {"start": 17, "end": 23, "text": "HE STAYED. ATE THEIR FOOD.",
             "shot": "kitchen ice-cream evidence tag"},
            {"start": 23, "end": 29, "text": "HE WASHED HIS OWN BLOOD.",
             "shot": "sink blood trace, red lines"},
            {"start": 29, "end": 35, "text": "WALKED OUT. LEFT HIS DNA.",
             "shot": "jacket, belt, knife exhibit tags"},
            {"start": 35, "end": 47, "text": "STILL OPEN. 25 YEARS.",
             "shot": "case file #8989, live timecode"},
            {"start": 47, "end": 60, "text": "THE BODY IS GONE. THE EVIDENCE IS NOT.",
             "shot": "open case file, reward stamp"},
        ],
        "vo_text": ("Tokyo. December thirty-first, two thousand. "
                    "A family of four, asleep in their home in Setagaya. "
                    "By dawn, all of them were dead. "
                    "Nobody broke in. The killer came through the door — "
                    "and then, he stayed. He ate their ice cream. "
                    "He used the computer. He used the bathroom. "
                    "When his hand bled, he washed it — "
                    "and left his blood in the sink. "
                    "In the morning, he walked out with her wallet, "
                    "right past four bodies. "
                    "He left behind a jacket, a belt, a knife, and his DNA. "
                    "Twenty-five years later, the case is still open. "
                    "Japan's most famous unsolved murder. "
                    "If you know that face, "
                    "there is a twenty-million-yen reward. "
                    "The body is long gone. The evidence is not."),
    },
    "forensic smoke test": {
        "title": "DARK FORENSIC TEST",
        "accent": "#ff1e27",
        "beats": [
            {"start": 0, "end": 2.6, "text": "SIGNAL ACQUIRED",
             "shot": "redacted file sheet, red stamp"},
            {"start": 2.6, "end": 5.2, "text": "CASE #8492 OPEN",
             "shot": "evidence map, crime tape"},
        ],
        "vo_text": "Signal acquired. Case eight four nine two. Now open.",
    },
    "manifesto": {
        "title": "EVERY FRAME IS CODE",
        "accent": "#c8ff3d",
        "beats": [
            {"start": 0, "end": 3, "text": "EVERY FRAME IS CODE",
             "shot": "code window pulsing"},
            {"start": 3, "end": 6, "text": "NOTHING IS FILMED",
             "shot": "paper camera, lens blank"},
            {"start": 6, "end": 9, "text": "SAME INPUT",
             "shot": "identical paper stacks"},
            {"start": 9, "end": 12, "text": "SAME PIXELS",
             "shot": "identical paper grid"},
            {"start": 12, "end": 15, "text": "RENDER IT FREE",
             "shot": "paper rocket lifts"},
        ],
        "vo_text": ("Every frame is code. Nothing is filmed. "
                    "Same input, same pixels. Render it free."),
    },
    "ecom product trick": {
        "title": "THE E-COM PRODUCT TRICK",
        "accent": "#b3422c",
        "beats": [
            {"start": 0, "end": 6, "text": "THE E-COM PRODUCT TRICK",
             "shot": "paper storefront, price tag swings"},
            {"start": 6, "end": 12, "text": "SELL THE CHANGE, NOT THE THING",
             "shot": "paper before-and-after halves split"},
            {"start": 12, "end": 18, "text": "SHOW THE OLD WAY. THE PAIN.",
             "shot": "paper bag slips from crumpled hand"},
            {"start": 18, "end": 24, "text": "THEN THE FIX. THE AFTER.",
             "shot": "paper hand catches the fix"},
            {"start": 24, "end": 30, "text": "ONE TAP. ADD TO CART.",
             "shot": "paper cart fills, tick stamped"},
        ],
        "vo_text": ("Here's the e-commerce trick nobody tells you. "
                    "You're not selling the thing, you're selling the change. "
                    "Show the old way. The pain. That's the villain. "
                    "Now flip it. Show the fix, the after. "
                    "Let them feel it click. The price stops mattering. "
                    "Same product, different story. That is the whole trick. "
                    "One tap. Add to cart. Watch it fly."),
    },
}


def _custom_clip(n: int, q: dict) -> pathlib.Path:
    """A custom-topic short — clip.json only (the notebook TTS's the voice)."""
    copy = _TOPIC_CLIPS.get(q.get("topic", ""))
    if not copy:
        copy = _TOPIC_CLIPS["manifesto"]
        print("no copy for %r — falling back on the manifesto" % q.get("topic"))
    res = {"9:16": (1080, 1920), "16:9": (1920, 1080),
           "1:1": (1080, 1080)}[orient_for(q["form"])]
    dur = float(q.get("time") or 15)
    voice = q["voice"] if q["voice"] != "keep" else "en-US-ChristopherNeural"
    beats = copy["beats"]
    beats[-1]["end"] = dur
    clip = {
        "title": copy["title"],
        "width": res[0], "height": res[1],
        "fps": {"p30": 30, "p24": 24, "p15": 15}[q["quality"]],
        "duration": dur, "voice": voice, "rate": "+5%", "pitch": "-4Hz",
        "accent": "#ff1e27" if q["motif"] == "forensic" else copy["accent"],
        "motif": q["motif"],
        "badge": q["badge"], "cta": q["cta"],
        "vo_text": copy["vo_text"], "beats": beats,
    }
    out = CLIP_OUT / ("clip_%d.zip" % n)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("clip.json", json.dumps(clip, indent=2))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="test",
                    choices=("pack", "nb", "push", "local", "test", "all"))
    ap.add_argument("-n", "--clip", type=int, default=1)
    ap.add_argument("--target", choices=("github", "colab", "local"),
                    default=None,
                    help="skip the interview and force a route")
    ap.add_argument("--answers", type=pathlib.Path, default=None,
                    help="interview answers as JSON (skips the interactive survey)")
    ap.add_argument("--no-whisper", action="store_true")
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--push", action="store_true")
    a = ap.parse_args()

    if a.answers:
        q = dict(DEFAULTS)
        q.update(json.loads(a.answers.read_text(encoding="utf-8")))
        q["clip"] = a.clip
        q["orient"] = orient_for(q["form"])
        if q.get("delivery") == "telegram":
            q.setdefault("tg_chat", "-1004375547101")
            q.setdefault("tg_thread", "992")
        else:
            q.setdefault("tg_chat", None)
            q.setdefault("tg_thread", None)
        print("answers loaded — %s" % " ".join(
            "%s=%s" % (k, v) for k, v in sorted(q.items())
            if k not in ("tg_chat", "tg_thread")))
    else:
        q = interview()

    if a.cmd == "pack":
        print("packed:", pack(q["clip"], not a.no_whisper))
        return
    if a.cmd == "nb":
        nb()
        return
    if a.cmd == "push":
        push()
        return
    if a.cmd == "local":
        if not str(q.get("topic", "")).startswith("batch_"):
            print("packed:", _custom_clip(q["clip"], q))
        local(q["clip"], a.fast)
        return

    # test / all — prepare the right clip, remix with the answers, route
    if not str(q.get("topic", "")).startswith("batch_"):
        packed = _custom_clip(q["clip"], q)
    else:
        packed = pack(q["clip"], not a.no_whisper)
        q["form"], q["time"] = "long", None  # a real batch keeps its duration + packed voice
    print("packed:", packed)

    # captions-vs-voice resolution
    cap = q["captions"]
    drop_vo = q["voice"] != "keep"
    voice = None if q["voice"] == "keep" else q["voice"]
    if cap == "word":
        drop_vo, voice = True, voice or "en-US-ChristopherNeural"
    elif cap == "phrase":
        drop_vo, voice = False, None

    res = {"9:16": (1080, 1920), "16:9": (1920, 1080),
           "1:1": (1080, 1080)}[q["orient"]]
    fps = {"p30": 30, "p24": 24, "p15": 15}[q["quality"]]

    remix_zip(q["clip"], drop_vo=drop_vo, drop_bgm=q["bgm"] == "none",
              drop_caps=cap == "none", duration=q["time"], voice=voice,
              motif=q["motif"], badge=q["badge"], cta=q["cta"],
              width=res[0], height=res[1], fps=fps)

    rate = q["route"]
    if a.target:
        rate = a.target
    if rate == "local":
        nb(clip_url="", tg_chat=q["tg_chat"], tg_thread=q["tg_thread"])
        local(q["clip"], a.fast)
        print("LOCAL %s OK; cloud artifact kept separate" % a.cmd.upper())
        return

    # cloud routes bake the link bridge (+ optional telegram defaults)
    url = bridge_url() + ("clip_%d.zip" % q["clip"])
    nb(clip_url=url, tg_chat=q["tg_chat"], tg_thread=q["tg_thread"])
    if q["tg_chat"]:
        print("telegram delivery baked: chat=%s thread=%s" %
              (q["tg_chat"], q["tg_thread"] or "main"))
    print("cloud notebook built with link bridge: %s" % url)
    if rate == "colab":
        u = "https://colab.research.google.com/#create=true"
        subprocess.Popen(["xdg-open", u], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        print("open %s -> Upload -> yt_auto_colab.ipynb -> Ctrl+F9" % u)
        print("(clip_%d.zip fetch is automatic via the bridge)" % q["clip"])
    else:  # github
        push()
        print("Open the printed Colab-GitHub URL, then Ctrl+F9")


if __name__ == "__main__":
    main()