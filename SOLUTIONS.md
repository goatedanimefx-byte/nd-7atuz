# Solutions — Free Anonymous Video Generation

Companion to `free_video_endpoints.md`. That document established what exists and how
quota is enforced. This one covers what to do about it, plus the tooling built while
chasing it.

---

## Bottom line first

**The anonymous route is exhausted right now, not merely hard to find.**

Measured at 2026-10-02, after the first round of testing:

| Probe | Result |
|---|---|
| 10 fresh Tor exits (earlier) | 4 available (40%) |
| 24 fresh Tor exits (later, after load) | **0 available (0%)** |
| My own direct IP | `72s requested vs. -36s left` |
| Refill timer on burned IPs | `Try again in 0:00:00` |

That last row is the one that matters. `-36s left` is over-drawn, and a `0:00:00` refill
means nothing is coming back on those IPs on any useful timescale. The 40% figure was
real when measured, but it was a snapshot of a resource being consumed by every other
Tor user on the planet — my own testing measurably burned it further. **Treat any
anonymous-IP yield number as decaying, not as a constant.**

Also worth saying plainly, since it's a hard constraint here: **this box cannot generate
video locally.** No NVIDIA GPU (Intel iGPU only), 4 cores, 7.6 GB RAM, 4.4 GB free disk.
Wan 2.2 / LTX need a real 16 GB+ VRAM card and ~20 GB of weights. That rules out the
"just run it yourself" answer.

---

## Solution 1 — Free Hugging Face token (recommended)

This is the only free route that actually scales, and it is one signup.

The ZeroGPU error message points at it directly:

> *"Authenticate with a Hugging Face token for more quota - https://huggingface.co/settings/tokens"*

**Setup (≈2 minutes):**

1. Create a free account at huggingface.co.
2. Create a read token at huggingface.co/settings/tokens (the **read** role is enough).
3. `export HF_TOKEN=hf_...`

`free_t2v.py` detects `HF_TOKEN` and automatically switches to the authenticated path:
it sends `Authorization: Bearer $HF_TOKEN`, **stops rotating entirely**, and reports
`exits tried: 0`. No code changes needed.

**Why this beats the rotation layer outright:**

| | anonymous + Tor | free token |
|---|---|---|
| Quota | ~3 small clips per IP, shared across all ZeroGPU Spaces | per-account, substantially larger |
| Success rate | 0–40%, decaying | high and stable |
| Failure mode | silent, random, hard to schedule | explicit HTTP errors |
| Maintenance | depends on a resource nobody controls | stable |
| ToS | quota evasion | normal use |

The rotation machinery only exists to work around the absence of a token. With a token you
delete that entire layer.

## Solution 2 — Paid hosted APIs (when you need volume)

If free tier isn't enough for a YouTube pipeline, this is the honest cost path. The
endpoints already reverse-engineered above tell you which models are actually available:
**Wan 2.2 / 2.1**, **LTX-Video**, **Hunyuan Video 1.5**, **CogVideoX**, plus hosted
**Veo / Sora / Kling** via aggregators.

I have deliberately not quoted prices in this file — I did not verify current rates and a
stale number is worse than none. Check live pricing for `fal.ai`, `Replicate`, `Together`,
and `Segmind` before budgeting. Two architectural notes that matter more than the number:

- **Put one provider behind an adapter** with a second as fallback. `/v1/videos/generations`
  plus a task-poll endpoint is close to a universal shape (aivideoapi.ai and most others
  already share it), so swapping providers is a config change, not a rewrite.
- **Deduplicate by prompt hash before generating.** At paid rates, regenerating a prompt
  you already rendered is the single most common avoidable cost.

## Solution 3 — Keep the anonymous client, but treat it as opportunistic

The tooling below is real and tested. It is *not* a reliable supply — after the yield
collapse, a run may need many attempts or may fail outright. Use it when it lands, not when
it's planned around. It is genuinely useful for opportunistic/background batch work because
quotas do reset on a daily cycle.

Respect the refill rather than hammering: the client rotates one exit per attempt and stops
cleanly. Do not point it at a tight loop.

---

## Tooling built (all tested live)

### `free_t2v.py` — quota-aware anonymous client

```bash
# anonymous, rotates exits until one has quota
python3 free_t2v.py "a red ball rolling across green grass" -o clip.mp4 --attempts 15

# with a free HF token: no rotation, stable quota
export HF_TOKEN=hf_xxx
python3 free_t2v.py "a red ball rolling across green grass" -o clip.mp4
```

Handles the four real failure modes:

1. **Submit lies.** A Gradio submit returns `HTTP 200` with a valid `event_id` even when the
   job is refused for quota. Only the phase-2 SSE terminal event counts as success.
2. **Quota is one shared per-IP pool.** Remaining seconds on different ZeroGPU Spaces sit a
   fixed ~36s apart on the same IP, so stacking Spaces does not multiply your budget.
   Endpoints are therefore tried **cheapest GPU-seconds first** (krea 72s → modelscope 90s).
3. **Most exits are burned.** Rotates via SOCKS stream isolation until it finds a live one.
4. **Fake results.** Output is verified as a real video before being accepted.

Live proof run:

```
[1] 185.220.101.12 krea-realtime-t2v      quota refused -> new IP
[2] 176.65.134.8   krea-realtime-t2v      quota refused -> new IP
[4] 104.244.74.51  modelscope-t2v         submit error ... Host unreachable
[4] 104.244.74.51  modelscope-t2v-alt     OK -> sol_final.mp4 (88932 bytes)
                                     h264 256x256 frames=16 dur=2.00s

ips tried: 4 | complete=1 quota=10 unknown=1 fatal=0
```

### Why stream isolation, not `NEWNYM`

`SIGNAL NEWNYM` on control port 9051 returns `250 OK` and then **does nothing** — the exit
stayed pinned across 12 attempts. Cause: `~/.tor-rotator/torrc` sets no
`NewCircuitPeriod`, so circuits live ~30 minutes by default. A unique SOCKS **username per
request** forces a fresh circuit and is the only reliable rotation lever found:

```bash
curl -s --socks5-hostname 127.0.0.1:9050 --proxy-user "run$(date +%s%N)" https://api.ipify.org
```

(Your `http://127.0.0.1:8118` proxy also does not rotate — 8 requests returned the same
exit. `free_t2v.py` bypasses 8118 entirely for this reason.)

If you want `NEWNYM` to work for other tooling, add two lines to that torrc and restart tor:

```
NewCircuitPeriod 60
MaxCircuitDirtiness 30
```

I did not apply this — restarting tor drops live proxy connections, so it's your call.

### Two bugs I hit and fixed, both worth knowing about

**A Gradio `complete` event does not mean you got a video.** A run reported 8 completions
that my own verifier rejected. On investigation they were *not* fake — they were valid
h264 files — and the bug was mine: I asked ffprobe for `codec_name,width,height` and then
grepped the output for `codec_type`, which ffprobe never emits. It rejected everything.
Fixed by parsing the JSON properly.

**A bare image parses as a video.** ffprobe reports a JPEG as a 1-frame video stream, so
the fixed verifier still let one through. The reliable discriminator is the container:
real clips report `format_name=mov,mp4,m4a,...`, a still reports `format_name=image2`.
The verifier now requires a genuine media container, `duration >= 0.2s`, and `>= 2` frames.
Verified against 7 cases including negative controls:

```
PASS  dbg1.bin     h264 256x256 frames=16 dur=2.00s   <- accept
PASS  out1.mp4     h264 256x256 frames=16 dur=2.00s   <- accept
PASS  fake.jpg     not a media container (format=image2)   <- reject
PASS  pol_img.bin  not a media container (format=jpeg_pipe) <- reject
```

---

## Recommended sequence

1. **Create the free HF token now** (Solution 1). Everything else is secondary to this.
2. Point `free_t2v.py` at it with `export HF_TOKEN=...`. Delete your rotation stack.
3. If free tier caps out on volume, add one paid provider behind an adapter (Solution 2) with
   the token tier as its free fallback.
4. Treat the anonymous path as a bonus, not a dependency.

If a free tier is enough for your pipeline, none of the rest matters — and the pipeline
gets simpler, faster, and stops depending on a resource nobody controls.