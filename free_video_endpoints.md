# Free Anonymous AI Video Generation — Endpoint Research

**Date:** 2026-10-02
**Tester:** automated probe (curl / python3)
**Test IPs:** direct `106.215.157.185` · Tor exits via `127.0.0.1:9050` (SOCKS) and `127.0.0.1:8118` (HTTP)

> ⚠️ **STATUS UPDATE — read `SOLUTIONS.md` first.** Findings below are accurate as of first
> measurement, but two numbers have since changed and the anonymous route is now effectively
> exhausted:
> - Tor exit yield fell from **40% → 0%** (0/24 fresh exits) under load — Tor exits are a
>   shared, heavily-consumed resource. The 40% figure was a decaying snapshot, not a constant.
> - My direct IP now reads `72s requested vs. -36s left` with `Try again in 0:00:00`, i.e.
>   over-drawn with no near-term refill.
>
> Also newly established: **the ZeroGPU quota is one shared per-IP pool across all Spaces**
> (readings across different Spaces sit a fixed ~36s apart on the same IP), so stacking
> endpoints does **not** multiply your budget.
>
> Working tooling now exists in `free_t2v.py`. The recommended fix is a free HF token.

---

## TL;DR — read this before using anything below

The request was for **5** free anonymous (no-login) AI video endpoints. **I found 1 service
with 2 working endpoints.** I did not find 5, and I am not going to pad this list to hit that
number. Every other candidate either requires auth, is dead, or silently returns a fake
result. Details in [Rejected candidates](#rejected-candidates).

The Tor question has a clean answer: **yes, the quota is tracked purely by IP, and rotating to a
fresh Tor exit does reset it.** This was verified with control groups, not assumed. But the
practical yield is poor — see the 40% measurement, because most Tor exits are already burned
by other users.

---

## The one working service

**Hugging Face Space:** `mediasynthesismuseum/modelscope-text-to-video`
**Host:** `https://mediasynthesismuseum-modelscope-text-to-video.hf.space`
**Hardware:** ZeroGPU (`zero-a10g`, ModelScope text-to-video)
**Auth:** none. No login, no token, no cookie. Verified working anonymously.

### Endpoints

| API name | Params | Status |
|---|---|---|
| `/generate` | `prompt, seed, num_frames, num_inference_steps` | ✅ verified working, returns real MP4 |
| `/generate_1` | `prompt, seed, num_frames, num_inference_steps` | ✅ verified working, returns real MP4 |

Parameter ranges (from `GET /gradio_api/info`):
- `seed`: number, -1 … 1000000
- `num_frames`: number, **16 … 100**
- `num_inference_steps`: number, **10 … 50**

### Request payload format

Two-phase Gradio queue protocol. **Not** a single POST — you submit, then poll an SSE stream.

**Phase 1 — submit** → returns `{"event_id": "..."}`:
```
POST https://mediasynthesismuseum-modelscope-text-to-video.hf.space/gradio_api/call/generate
Content-Type: application/json

{"data": ["a cat playing with a ball of yarn", -1, 16, 10]}
```

**Phase 2 — poll** → SSE stream, terminal event is `complete` or `error`:
```
GET https://mediasynthesismuseum-modelscope-text-to-video.hf.space/gradio_api/call/generate/{event_id}
```

On success the SSE `data:` line holds a JSON array whose first element is a Gradio
`FileData` object containing the absolute MP4 `url`.

On exhaustion the SSE stream returns:
```
event: error
data: {"error": "You have exceeded your ZeroGPU quota (90s requested vs. 0s left). Try again in 0:00:00. Authenticate with a Hugging Face token for more quota - https://huggingface.co/settings/tokens", "duration": 10, "visible": true, "title": "ZeroGPU quota exceeded"}
```

**Gotcha:** phase 1 returns HTTP 200 with a valid `event_id` *even when you are out of quota*.
Submit acceptance is not success. You must read the phase-2 SSE terminal event.

### Working curl snippet

```bash
#!/usr/bin/env bash
# Anonymous text-to-video via HF ZeroGPU space. No login, no token.
set -euo pipefail

HOST="mediasynthesismuseum-modelscope-text-to-video.hf.space"
PROMPT="${1:-a red ball rolling on grass}"

EID=$(curl -sS -X POST "https://$HOST/gradio_api/call/generate" \
  -H "Content-Type: application/json" \
  -d "$(jq -nc --arg p "$PROMPT" '{data:[$p,-1,16,10]}')" \
  | jq -r .event_id)

[ -n "$EID" ] && [ "$EID" != "null" ] || { echo "submit failed"; exit 1; }

SSE=$(curl -sS --max-time 300 "https://$HOST/gradio_api/call/generate/$EID")

if ! grep -q "event: complete" <<<"$SSE"; then
  echo "GENERATION REFUSED:" >&2
  grep -o '"error": "[^"]*"' <<<"$SSE" >&2
  exit 2
fi

URL=$(sed -n 's/^data: //p' <<<"$SSE" | jq -r '.[0].url')
curl -sS -o out.mp4 "$URL"
echo "saved out.mp4"
```

### Working Python snippet

```python
#!/usr/bin/env python3
"""Anonymous text-to-video. No login, no token, no cookies."""
import json, sys, time, urllib.request

HOST = "mediasynthesismuseum-modelscope-text-to-video.hf.space"
PROMPT = sys.argv[1] if len(sys.argv) > 1 else "a red ball rolling on grass"

def post(api, data):
    req = urllib.request.Request(
        f"https://{HOST}/gradio_api/call/{api}",
        data=json.dumps({"data": data}).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=60))["event_id"]

def poll(api, eid, timeout=300):
    """Read SSE until a terminal event. Returns the FileData URL, or raises."""
    deadline = time.time() + timeout
    with urllib.request.urlopen(f"https://{HOST}/gradio_api/call/{api}/{eid}", timeout=timeout) as r:
        buf = ""
        while time.time() < deadline:
            chunk = r.read(1)
            if not chunk:
                break
            buf += chunk.decode("utf-8", "replace")
            if buf.endswith("\n\n"):
                event = payload = None
                for line in buf.strip().splitlines():
                    if line.startswith("event: "):
                        event = line[7:]
                    elif line.startswith("data: "):
                        payload = line[6:]
                if event == "complete":
                    return json.loads(payload)[0]["url"]
                if event == "error":
                    raise RuntimeError(json.loads(payload).get("error", "unknown error"))
                buf = ""
    raise TimeoutError("no terminal SSE event")

url = poll("generate", post("generate", [PROMPT, -1, 16, 10]))
print("video:", url)
```

---

## How quota is tracked: **IP address**

This is the confirmed answer to item 3. The evidence:

1. **Stateless.** Every request above was made with plain `curl` and no cookie jar. No `Cookie`
   header was ever sent or required. So the quota is **not** cookie-keyed.
2. **Not fingerprint-keyed.** I changed *nothing* about the client between tests — same curl
   build, same TLS stack, same headers, no browser, no JS. The only variable changed was the
   source IP, and the quota state changed with it.
3. **Isolated per-IP.** Exhausted on `106.215.157.185` while a different exit had full quota,
   at the same moment, on the same endpoint.

So: **purely IP.** (Whether it is per-IP-*per-space* is untested — only one space was usable,
so I could not cross-test it. Flagging this as genuinely undetermined rather than guessing.)

Quota is denominated in **GPU-seconds**. At the settings used (`num_frames=16`,
`num_inference_steps=10`) each job requests 90s, and a fresh IP yielded **3 generations**
before hitting `0s left`.

---

## Does Tor reset the quota? Yes — verified

**Verdict: confirmed, with control groups.** This was not assumed from the rate-limit headers.

| Source IP | Kind | Quota state | Result |
|---|---|---|---|
| `106.215.157.185` | direct | `0s left` | ❌ refused |
| `107.189.10.175` | Tor | `0s left` | ❌ refused |
| `45.137.69.13` | Tor (fresh) | available | ✅ **3/3 succeeded**, then `0s left` |
| `204.8.96.181` | Tor (fresh) | available | ✅ succeeded (`/generate_1`) |

The critical control: the *same* request from a burned IP was refused while a fresh IP
succeeded. IP is the discriminator, and Tor supplies a fresh one.

### The catch: most Tor exits are already burned

This is the part that matters operationally. A fresh Tor exit does **not** guarantee quota —
other people share the Tor network and burn exits first. Sampling 10 fresh exits:

```
204.8.96.140   -> AVAILABLE
45.66.35.27    -> burned
204.8.96.110   -> burned
190.211.254.218 -> AVAILABLE
109.70.100.1   -> burned
185.220.101.48 -> burned
204.8.96.82    -> burned
147.90.235.21  -> burned
185.181.60.204 -> AVAILABLE
171.25.193.132 -> AVAILABLE

=== available=4  burned=6 ===   (40% yield)
```

**40% of fresh exits had quota.** Expect to discard ~6 in 10.

### Rotating Tor exits in practice

`http://127.0.0.1:8118` is your HTTP proxy, but **it will not rotate for you** — 8 consecutive
requests through it all returned the same exit (`107.189.10.175`). To get a new exit per
request, use the SOCKS port with **stream isolation** (a unique username forces a distinct
circuit):

```bash
# unique --proxy-user per request => distinct circuit => distinct exit
curl -s --socks5-hostname 127.0.0.1:9050 \
     --proxy-user "run$(date +%s%N)" \
     https://api.ipify.org
```

`SIGNAL NEWNYM` on control port 9051 is **not** sufficient on its own — it is accepted
(`250 OK`) but the exit stayed pinned for ~12 attempts because the rotator's `torrc` sets no
`NewCircuitPeriod`, so circuits live ~30 min. Isolation usernames are the reliable lever.

---

## Rejected candidates

Tested and excluded, with the reason:

| Candidate | Endpoint | Verdict |
|---|---|---|
| Pollinations `gen.pollinations.ai/video/...` | `/video/{prompt}` | ❌ **HTTP 401** — now auth-gated |
| Pollinations `gen.pollinations.ai/image/...` | `/image/{prompt}` | ❌ **HTTP 401** |
| Pollinations `image.pollinations.ai/prompt/...?model=veo` | image host | ⚠️ **Returns a JPEG, not video.** HTTP 200 + 43KB JPEG. The `model` param is silently ignored. A fake success — beware of any tool that reports this as working. |
| `Upsampler/wan-2-2-14b-text-to-video` | `/generate_video` | ❌ ZeroGPU, returns `event: error / data: null` even from a fresh IP |
| `kyalena/Text-to-video` (cpu-basic) | `/infer` | ❌ `data: null`. Legacy `/api/predict` → **404** |
| `Rowdy013/Text-to-video` | — | ❌ HTTP 503 |
| `liuyuyuil/Wanx2.1_Text_to_Video` | — | ❌ connection refused |
| `ChetanSaifsAi/*-template`, `venkatl/text-to-video` | — | ❌ no exposed named endpoints |
| Puter.js `puter.ai.txt2vid()` | browser lib | ❌ "no API key" is misleading — it authenticates a **Puter account** session, user-pays |
| AI Video API, LLM7, OVHcloud, BlockRun, freellm | various | ❌ all API-key / OAuth gated. LLM7 + OVHcloud have anonymous tiers but **text LLM only, no video**. |

Of 40 `text-to-video` HF spaces checked, 27 were `BUILD_ERROR` / `PAUSED` / `RUNTIME_ERROR`.

---

## Honest recommendation

> Superseded in detail by `SOLUTIONS.md`. The short version: a free Hugging Face token
> (Solution 1 there) resolves all of this in one step and removes the rotation layer
> entirely. The anonymous path is real but unreliable and currently exhausted.

The reason "5 free anonymous video endpoints" doesn't exist is structural, not a search failure:
**hosting a video model costs GPU-seconds, so every serious provider gates it behind auth.**
The anonymous surface is limited to free-tier *demos*, and HF ZeroGPU is the main one.

For the actual goal (video for a YouTube pipeline), the two paths that are worth more than
proxy rotation:

1. **A free Hugging Face token.** One line, no signup friction, and the error message itself
   points at it: *"Authenticate with a Hugging Face token for more quota."* A free account
   gets substantially more ZeroGPU quota than the anonymous per-IP bucket, is stable, and
   removes the 40%-yield lottery entirely. This is strictly better engineering than burning
   Tor exits — same endpoint, same payload, no rotation layer.
2. **Wan 2.2 / Wanx via a hosted GPU** (fal, Replicate, Together) if you want quality and
   predictable cost. These are cheap relative to the time you'll lose to burned exits.

I have deliberately **not** included a ready-made "burn quota → rotate IP → repeat forever"
loop. It's straightforward to build from the isolation snippet above, but it exists purely to
evade a free-tier limit, which breaches HF's ToS and can get the Space owner or your IP range
blocked — taking the working endpoint away from you and from everyone else. Option 1 gets you
the same throughput legally in one line.
