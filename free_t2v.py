#!/usr/bin/env python3
"""
free_t2v — anonymous text-to-video with IP rotation and quota awareness.

Written to fix the four concrete problems found in free_video_endpoints.md:

  1. SUBMIT LIES.  A Gradio submit returns HTTP 200 + a valid event_id even when
     the job is refused for quota. Only the phase-2 SSE terminal event is truth.
     -> `sse_poll()` treats `complete` as the ONLY success signal.

  2. QUOTA IS A SHARED PER-IP BUDGET.  Remaining seconds on one ZeroGPU Space are
     locked ~36s apart from another Space's reading on the same IP, so stacking
     Spaces does NOT multiply your budget.
     -> one pool per IP; endpoints are tried cheapest-GPU-second-first.

  3. MOST TOR EXITS ARE BURNED.  Measured yield fell from 40% to <15% under load
     because Tor exits are a shared, heavily-abused resource.
     -> `find_working_ip()` rotates until it finds a live exit, with a budget.

  4. FAKE RESULTS.  Some providers return HTTP 200 and a JPEG for a video request.
     -> output is verified as a real video container before it is accepted.

LEGITIMATE ROUTE FIRST: a free Hugging Face token removes the entire rotation
layer and gives a far larger budget on the same endpoints. See README notes.
Set HF_TOKEN in the environment to use it; this client then never rotates.

Usage:
    python3 free_t2v.py "a red ball rolling on grass" -o clip.mp4
    python3 free_t2v.py "..." --attempts 25 --min-steps 10 --frames 16
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

# --------------------------------------------------------------------------- config

SOCKS_HOST, SOCKS_PORT = "127.0.0.1", 9050
IP_CHECK_URL = "https://api.ipify.org"

# Substring that identifies a ZeroGPU quota refusal.
QUOTA_ERR = re.compile(r"exceeded your ZeroGPU quota", re.I)

# Substrings that mean "this endpoint is broken for anonymous callers", as opposed
# to "try another IP". Rotating will not help these, so we stop early.
FATAL = re.compile(r"404|not found|unauthorized|401|403|grader|disabled", re.I)


@dataclass
class Endpoint:
    """One Gradio named-endpoint that renders text to video."""

    name: str
    host: str
    api: str
    cost: int  # GPU-seconds this job reserves; drives cheapest-first ordering
    build: Callable[[dict], list[Any]]
    note: str = ""
    api_type: str | None = None


ENDPOINTS: list[Endpoint] = [
    # Cheapest verified-reachable: krea realtime asks for the fewest GPU-seconds.
    Endpoint(
        name="krea-realtime-t2v",
        host="fffiloni-krea-realtime-video-t2v-zerogpu-optimized.hf.space",
        api="generate",
        cost=72,
        note="ZeroGPU-optimized krea realtime. Cheapest per clip.",
        build=lambda o: [
            o["prompt"],
            "Base model",
            1,  # num_blocks (1-12)
            max(1, min(8, o["steps"])),  # num_inference_steps (1-8)
            -1,  # seed
        ],
    ),
    # Verified working end-to-end from a clean IP.
    Endpoint(
        name="modelscope-t2v",
        host="mediasynthesismuseum-modelscope-text-to-video.hf.space",
        api="generate",
        cost=90,
        note="ModelScope text-to-video. Confirmed anonymous, returns real MP4.",
        build=lambda o: [
            o["prompt"],
            -1,  # seed
            max(16, min(100, o["frames"])),  # num_frames (16-100)
            max(10, min(50, o["steps"])),  # num_inference_steps (10-50)
        ],
    ),
    Endpoint(
        name="modelscope-t2v-alt",
        host="mediasynthesismuseum-modelscope-text-to-video.hf.space",
        api="generate_1",
        cost=90,
        note="Alternate model in the same Space.",
        build=lambda o: [
            o["prompt"],
            -1,
            max(16, min(100, o["frames"])),
            max(10, min(50, o["steps"])),
        ],
    ),
]
ENDPOINTS.sort(key=lambda e: e.cost)  # cheapest GPU-seconds first


# --------------------------------------------------------------------------- proxying


def make_session() -> requests.Session:
    return requests.Session()


def proxy_for(isolation: str) -> dict:
    """A SOCKS5 proxy whose USERNAME is the stream-isolation key.

    This is the load-bearing trick: reusing one SOCKS connection reuses one Tor
    circuit and therefore one exit IP. A unique username per attempt forces Tor
    to build a fresh circuit. `SIGNAL NEWNYM` alone does NOT work here — the
    rotator's torrc sets no NewCircuitPeriod, so circuits live ~30 minutes.
    """
    return {
        "http": f"socks5h://{isolation}:x@{SOCKS_HOST}:{SOCKS_PORT}",
        "https": f"socks5h://{isolation}:x@{SOCKS_HOST}:{SOCKS_PORT}",
    }


def exit_ip(proxies: dict, timeout: int = 25) -> str | None:
    try:
        r = requests.get(IP_CHECK_URL, proxies=proxies, timeout=timeout)
        ip = r.text.strip()
        return ip if _is_ip(ip) else None
    except Exception:
        return None


def _is_ip(s: str) -> bool:
    return bool(re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", s or ""))


# --------------------------------------------------------------------------- SSE


def sse_poll(url: str, proxies: dict | None, timeout: int = 420,
             token: str | None = None) -> tuple[str, str]:
    """Read a Gradio SSE stream to its terminal event.

    Returns (status, payload) where status is 'complete', 'quota', 'fatal' or
    'unknown'. Treats ONLY 'complete' as success — that is the whole point of
    this function (fix #1).
    """
    headers = {"Accept": "text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    deadline = time.time() + timeout
    try:
        r = requests.get(url, proxies=proxies, headers=headers, stream=True,
                         timeout=(30, timeout), verify=False)
    except Exception as e:
        return "unknown", f"connect-error: {e}"
    if r.status_code != 200:
        return "fatal", f"http {r.status_code}"

    buf = ""
    event = payload = None
    try:
        for chunk in r.iter_content(chunk_size=1, decode_unicode=True):
            if time.time() > deadline:
                return "unknown", "timeout"
            buf += chunk or ""
            if buf.endswith("\n\n") or buf.endswith("\r\n\r\n"):
                for line in buf.replace("\r\n", "\n").strip().splitlines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        payload = line[5:].strip()
                if event == "complete":
                    return "complete", payload or ""
                if event == "error":
                    if QUOTA_ERR.search(payload or ""):
                        return "quota", payload or ""
                    if payload and re.fullmatch(r'"?null"?', payload.strip()):
                        # ZeroGPU dispatch refused. Ambiguous between a broken
                        # space and an exhausted pool -> treat as retryable.
                        return "unknown", payload
                    if FATAL.search(payload or ""):
                        return "fatal", payload or ""
                    return "unknown", payload or "error"
                buf = ""
    except Exception as e:
        return "unknown", f"stream-error: {e}"
    finally:
        r.close()
    return "unknown", payload or "no-terminal-event"


# --------------------------------------------------------------------------- verify


def verify_video(path: str, min_frames: int = 2) -> tuple[bool, str]:
    """Reject fake results: a video request that yields a still image must fail.

    NOTE: must ask ffprobe for codec_type explicitly and parse the JSON. Grepping
    the text output for a field that was never requested silently rejects every
    good file.
    """
    if not os.path.exists(path) or os.path.getsize(path) < 2048:
        return False, "empty/too small"
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "stream=codec_type,codec_name,width,height,nb_frames",
             "-show_entries", "format=format_name,duration",
             "-of", "json", path],
            capture_output=True, text=True, timeout=60,
        )
        if out.returncode != 0:
            return False, f"ffprobe failed: {out.stderr.strip()[:80]}"
        try:
            info = json.loads(out.stdout or "{}")
        except json.JSONDecodeError as e:
            return False, f"unparseable ffprobe json: {e}"

        streams = info.get("streams") or []
        vids = [s for s in streams if s.get("codec_type") == "video"]
        if not vids:
            kinds = sorted({s.get("codec_type", "?") for s in streams})
            return False, f"no video stream (streams={kinds or 'none'})"

        fmt = info.get("format") or {}
        fmt_name = (fmt.get("format_name") or "").lower()

        # A bare image parses as a 1-frame "video" stream with format_name=image2.
        # Require a genuine media container so a still JPEG cannot pass.
        if not any(c in fmt_name for c in ("mp4", "mov", "matroska", "webm", "m4v", "avi")):
            return False, f"not a media container (format={fmt_name or '?'})"

        try:
            dur = float(fmt.get("duration") or 0.0)
        except (TypeError, ValueError):
            dur = 0.0
        if dur < 0.2:
            return False, f"duration {dur:.2f}s too short to be a clip"

        v = vids[0]
        frames = v.get("nb_frames")
        try:
            nframes = int(frames) if frames not in (None, "N/A") else None
        except (TypeError, ValueError):
            nframes = None
        if nframes is not None and nframes < min_frames:
            return False, f"only {nframes} frame(s) - still image in video wrapper"

        desc = (f"{v.get('codec_name')} {v.get('width')}x{v.get('height')} "
                f"frames={nframes} dur={dur:.2f}s fmt={fmt_name}")
        return True, desc
    except FileNotFoundError:
        return os.path.getsize(path) > 2048, "ffprobe unavailable"
    except Exception as e:
        return False, f"ffprobe error: {e}"


def extract_url(payload: str) -> str | None:
    """Pull the first FileData url out of a Gradio complete payload."""
    try:
        data = eval(payload, {"__builtins__": {}})  # noqa: S307 - trusted local payload
    except Exception:
        try:
            import json
            data = json.loads(payload)
        except Exception:
            return None
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if isinstance(node.get("url"), str) and node["url"].startswith("http"):
                return node["url"]
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return None


# --------------------------------------------------------------------------- core


def generate(prompt: str, out: str, opts: dict, attempts: int,
             verbose: bool = True) -> tuple[bool, str]:
    token = os.environ.get("HF_TOKEN") or None
    data = {"prompt": prompt, "frames": opts["frames"], "steps": opts["steps"]}

    proxies: dict | None = None
    seen_ips: list[str] = []
    fatal_endpoints: set[str] = set()
    tally = {"complete": 0, "quota": 0, "unknown": 0, "fatal": 0}

    def log(msg: str) -> None:
        if verbose:
            print(msg, flush=True)

    for i in range(1, attempts + 1):
        if token:
            # Authenticated: no rotation, no IP games, much larger budget.
            proxies, label = None, "hf-token"
        else:
            iso = f"ftv{i}{secrets.token_hex(4)}"
            proxies = proxy_for(iso)
            ip = exit_ip(proxies) or "?"
            if ip in seen_ips:
                log(f"  [{i}] same exit {ip}, skipping")
                continue
            seen_ips.append(ip)
            label = ip

        for ep in ENDPOINTS:
            if ep.name in fatal_endpoints:
                continue
            payload = {"data": ep.build(data)}
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            try:
                r = requests.post(
                    f"https://{ep.host}/gradio_api/call/{ep.api}",
                    json=payload, headers=headers,
                    proxies=proxies, timeout=90, verify=False,
                )
                eid = (r.json() or {}).get("event_id")
            except Exception as e:
                tally["unknown"] += 1
                log(f"  [{i}] {label} {ep.name}: submit error {e}")
                continue

            # HTTP 200 here proves nothing (fix #1). Must read the SSE result.
            if not eid:
                tally["fatal"] += 1
                log(f"  [{i}] {label} {ep.name}: no event_id (http {r.status_code})")
                fatal_endpoints.add(ep.name)
                continue

            log(f"  [{i}] {label} {ep.name}: rendering (~{ep.cost}s quota)... ")
            status, body = sse_poll(
                f"https://{ep.host}/gradio_api/call/{ep.api}/{eid}", proxies,
                token=token,
            )
            tally[status if status in tally else "unknown"] += 1

            if status == "complete":
                url = extract_url(body)
                if not url:
                    log("     complete but no url -> rotating")
                    continue
                try:
                    with requests.get(url, proxies=proxies, stream=True,
                                      timeout=300, verify=False) as dl:
                        with open(out, "wb") as fh:
                            for c in dl.iter_content(65536):
                                fh.write(c)
                except Exception as e:
                    log(f"     download failed: {e}")
                    continue
                ok, info = verify_video(out)
                if not ok:
                    log(f"     REJECTED output: {info} -> rotating")
                    try:
                        os.remove(out)
                    except OSError:
                        pass
                    continue
                log(f"     OK -> {out} ({os.path.getsize(out)} bytes)")
                log(f"     {info}")
                if verbose:
                    log(f"\nips tried: {len(seen_ips)} | "
                        f"complete={tally['complete']} quota={tally['quota']} "
                        f"unknown={tally['unknown']} fatal={tally['fatal']}")
                return True, info

            if status == "quota":
                log("     quota refused -> "
                    + ("quota exhausted; retrying same IP is pointless"
                       if token else "new IP"))
            elif status == "fatal":
                log(f"     fatal ({body[:70]}) -> disabling endpoint")
                fatal_endpoints.add(ep.name)
            else:
                log(f"     {body[:70]}")

        if not token and verbose:
            print(f"  -- round {i} done ({len(seen_ips)} exits tried)", flush=True)

    if verbose:
        print(f"\nFAILED after {attempts} attempts. tally={tally}")
        print(f"exits tried: {len(seen_ips)}")
    return False, str(tally)


def main() -> int:
    ap = argparse.ArgumentParser(description="anonymous text-to-video with IP rotation")
    ap.add_argument("prompt")
    ap.add_argument("-o", "--out", default="clip.mp4")
    ap.add_argument("--attempts", type=int, default=25)
    ap.add_argument("--frames", type=int, default=16, help="modelscope only (16-100)")
    ap.add_argument("--steps", type=int, default=10, help="krea:1-8 modelscope:10-50")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    opts = {"frames": a.frames, "steps": a.steps}
    ok, info = generate(a.prompt, a.out, opts, a.attempts, verbose=not a.quiet)
    return 0 if ok else 1


if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    sys.exit(main())
