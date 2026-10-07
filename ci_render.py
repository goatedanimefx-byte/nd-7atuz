#!/usr/bin/env python3
"""CI cloud renderer — GitHub Actions build.

Renders a clip from colab_input/clip_N.zip using the exact same worker engine
as the Colab notebook (CELL_RENDER in build_colab_notebook.py), then pushes
the finished MP4 to Telegram using repo secrets:

  TELEGRAM_BOT_TOKEN   bots token
  TELEGRAM_CHAT_ID     chat id (defaults -1004375547101)
  TELEGRAM_THREAD_ID   topic thread (defaults 992)

Usage:
    python3 ci_render.py --clip 1          # existing colab_input/clip_1.zip
    python3 ci_render.py --clip 1 --out x  # override sink dir
"""

import argparse
import json
import pathlib
import os
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_colab_notebook as g          # noqa: E402  (installs deps on import)

SINK = pathlib.Path(os.environ.get("YT_SINK", "/tmp/colab-auto/sink"))


def _title_of(clip_zip: pathlib.Path) -> str:
    with __import__("zipfile").ZipFile(clip_zip) as z:
        return json.loads(z.read("clip.json")).get("title", "clip %d" % 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip", required=True, help="clip number in colab_input/")
    ap.add_argument("--out", default=None, help="override sink dir")
    args = ap.parse_args()

    clip_num = args.clip
    if not clip_num.isdigit():
        print("FATAL: clip must be a number in colab_input/ (%s given)" % clip_num)
        return 1
    clip_zip = HERE / "colab_input" / ("clip_%s.zip" % clip_num)
    if not clip_zip.exists():
        print("FATAL: %s missing" % clip_zip)
        return 1

    if args.out:
        global SINK
        SINK = pathlib.Path(args.out)
    sink = SINK
    sink.mkdir(parents=True, exist_ok=True)

    src = g.CELL_RENDER
    src = src.replace("/content/yt_auto", str(sink))
    src = src.replace("__CLIP_URL_DEFAULT__", "")
    src = src.replace("os.environ.setdefault(\"CLIP_URL\", \"\")", "")

    env = dict(os.environ)
    env.setdefault("TELEGRAM_CHAT_ID", "-1004375547101")
    env.setdefault("TELEGRAM_THREAD_ID", "992")
    env["CLIP_ZIP"] = str(clip_zip)
    os.environ.update(env)

    ns: dict = {}
    err = None
    try:
        exec(compile(src, "<yt_auto-worker-ci>", "exec"), ns, ns)
    except SystemExit as e:
        err = e

    final = sink / "out" / "final.mp4"
    if not final.exists():
        print("FATAL: final.mp4 missing — worker failed" + (" (%s)" % err if err else ""))
        return 1

    title = _title_of(clip_zip)
    import hashlib
    sha = hashlib.sha256(final.read_bytes()).hexdigest()[:12]
    size = final.stat().st_size / 1e6
    print("RENDER COMPLETE — %s  %.1fMB sha:%s" % (title, size, sha))

    if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("CI"):
        print("no token/no CI — telegram dispatch skipped")
        return 0

    import subprocess, urllib.parse, shutil
    tok = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ["TELEGRAM_CHAT_ID"]
    thread = os.environ["TELEGRAM_THREAD_ID"]
    url = "https://api.telegram.org/bot%s/sendVideo" % tok
    caption = "yt_auto CI cloud render ✓\n%s\n1080x1920 @ 30fps\nsha256:%s" % (
        title, sha)
    subprocess.run([
        shutil.which("curl") or "curl", "-s",
        "-F", "chat_id=" + chat,
        "-F", "message_thread_id=" + thread,
        "-F", "caption=" + caption,
        "-F", "video=@" + str(final), url], timeout=900)
    print("telegram sendVideo: done (chat=%s thread=%s)" % (chat, thread))
    return 0


if __name__ == "__main__":
    sys.exit(main())