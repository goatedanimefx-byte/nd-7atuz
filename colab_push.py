#!/usr/bin/env python3
"""
yt_auto :: One-Click Cloud Runner

Pushes `yt_auto_colab.ipynb` to a GitHub Gist (public by default) or repo and
prints the one-click Colab URL:

    https://colab.research.google.com/gist/<login>/<gist_id>/yt_auto_colab.ipynb
    https://colab.research.google.com/github/<owner>/<repo>/blob/<branch>/yt_auto_colab.ipynb

Then anyone opens that URL, clicks `Runtime -> Run all`, and all heavy
rendering runs on Colab.

Auth resolution, in order:
  1. $GITHUB_TOKEN
  2. ~/.github_token            (plain token, chmod 600)
  3. ~/.config/gh/hosts.yml     (gh CLI oauth_token, if gh was ever used)
If none exist the script prints exact setup steps and exits.

Usage:
    python3 colab_push.py                    # gist (public), creates or updates
    python3 colab_push.py --private          # private gist (Colab still opens it)
    python3 colab_push.py --repo owner/repo  # commit the notebook to a repo
    python3 colab_push.py --file other.ipynb # push a different notebook
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import urllib.request
import urllib.error

HERE = pathlib.Path(__file__).resolve().parent
STATE = HERE / ".colab_gist.json"       # remembers the gist id so re-push updates
DEFAULT_NB = HERE / "yt_auto_colab.ipynb"
COLAB = "https://colab.research.google.com"


def load_token() -> str:
    t = os.environ.get("GITHUB_TOKEN", "").strip()
    if t:
        return t
    tok = pathlib.Path.home() / ".github_token"
    if tok.exists():
        v = tok.read_text(encoding="utf-8").strip()
        if v and v[:3] in ("ghp", "gho", "ghu", "ghs", "ghr"):
            return v
    try:
        text = (pathlib.Path.home() / ".config" / "gh" / "hosts.yml").read_text(
            encoding="utf-8")
    except FileNotFoundError:
        return ""
    m = re.search(r"oauth_token:\s*([^\s]+)", text)
    return m.group(1).strip() if m else ""


def api(path: str, data: dict | None, token: str, method: str | None = None) -> dict:
    url = "https://api.github.com" + path
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method or ("POST" if data is not None else "GET"))
    req.add_header("Authorization", "token " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "yt_auto-colab-push")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:500]
        raise SystemExit("GitHub %s: %s" % (e.code, detail))


def push_gist(token: str, nb_path: pathlib.Path, public: bool) -> str:
    payload = {
        "public": public,
        "description": "yt_auto Colab render bridge — vertical shorts pipeline "
                       "(Chromium canvas / Edge-TTS / FFmpeg) on free Colab CPU/GPU",
        "files": {nb_path.name: {"content": nb_path.read_text(encoding="utf-8")}},
    }
    gid = None
    if STATE.exists():
        gid = json.loads(STATE.read_text()).get("id")
    if gid:
        res = api("/gists/%s" % gid, payload, token, method="PATCH")
        verb = "updated"
    else:
        res = api("/gists", payload, token)
        gid = res["id"]
        verb = "created"
    STATE.write_text(json.dumps({"id": gid, "public": public}))
    owner = res["owner"]["login"]
    url = "%s/gist/%s/%s/%s" % (COLAB, owner, gid, nb_path.name)
    print("gist %s (public=%s): %s" % (verb, public, res["html_url"]))
    print("\nOpen in Colab:\n    %s\n" % url)
    return url


def push_repo(token: str, nb_path: pathlib.Path, repo: str, branch: str) -> str:
    name = nb_path.name
    api_path = "/repos/%s/contents/%s" % (repo, name)
    try:
        old = api(api_path, None, token)
        sha = old.get("sha")
        msg = "chore(yt_auto): update %s" % name
    except SystemExit:
        sha, msg = None, "chore(yt_auto): add %s" % name
    data = {
        "message": msg,
        "content": __import__("base64").b64encode(
            nb_path.read_bytes()).decode(),
        "branch": branch,
    }
    if sha:
        data["sha"] = sha
    api(api_path, data, token)
    url = "%s/github/%s/blob/%s/%s" % (COLAB, repo, branch, name)
    print("repo push ok: %s\n\nOpen in Colab:\n    %s\n" % (repo, url))
    return url


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=pathlib.Path, default=DEFAULT_NB)
    ap.add_argument("--repo", help="owner/repo to push the notebook into")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--private", action="store_true", help="private gist")
    args = ap.parse_args()

    if not args.file.exists():
        raise SystemExit("notebook not found: %s\nrun build_colab_notebook.py first" % args.file)

    token = load_token()
    if not token:
        print(__doc__.split("Auth resolution")[0])
        print(
            "No GitHub token found.\n"
            "\nOption A (seconds): open the notebook in Colab directly\n"
            "    'colab.research.google.com' -> File -> Upload notebook -> "
            + str(args.file) + "\n"
            "\nOption B (one key): create a token and re-run\n"
            "    1. github.com -> Settings -> Developer settings ->\n"
            "       Personal access tokens -> Tokens (classic)\n"
            "    2. Scope: 'gist' (or 'repo' for --repo). Copy it.\n"
            "    3. echo TOKEN > ~/.github_token && chmod 600 ~/.github_token\n"
            "    4. python3 colab_push.py\n"
            "\nOption C: pip install --user gh and 'gh auth login', then re-run."
        )
        return 1

    if args.repo:
        push_repo(token, args.file, args.repo, args.branch)
    else:
        push_gist(token, args.file, not args.private)
    return 0


if __name__ == "__main__":
    sys.exit(main())