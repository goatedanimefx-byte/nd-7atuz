# REPOS_RESEARCH — Free Image/Video Stack for True Crime Shorts

**Author:** Research Engineer, agency `yt_auto`
**Date:** 2026-10-04
**Verification method:** every repo below was queried live against `api.github.com` for
stars, SPDX license, `pushed_at`, and `archived` state. Repos I could not confirm are in
the *Considered and rejected* section at the bottom, not in the main list.

---

## 0. The constraint that decides everything (measured, not assumed)

I profiled the actual target box before evaluating anything:

```
CPU     4 cores          (Intel iGPU only — no NVIDIA, no CUDA)
RAM     7.6 GiB total    2.3 GiB available at test time
DISK    4.1 GiB FREE     on /home  (49G total, 92% used)   <-- binding constraint
Swap    7.6 GiB (1.6 used)
Python  3.14.7           (newer than most AI tooling supports)
ffmpeg  8.1.2 present    ffprobe present   <-- good, already there
```

**Disk is the real limit, not RAM.** This box has 8 GB of RAM but only 4.1 GB of free
disk. A local Stable Diffusion install needs `torch` (~800 MB CPU wheel, ~2.5 GB with
CUDA libs) plus SD 1.5 weights (~4 GB) or SDXL (~7 GB). **That does not fit.** It does
not matter that it would fit in RAM.

Consequence for every recommendation below: anything requiring local model weights is
**out**. The viable architecture is *free remote inference for generation* + *light local
CPU processing for assembly*. This matches what `SOLUTIONS.md` in this same directory
already concluded about video (no local GPU → free HF token route).

**Live endpoint test I ran (pollinations, no key, no account):**

```
512x512   -> HTTP 200, 26 KB JPEG          OK
640x640   -> HTTP 200, 49 KB JPEG          OK
4x parallel @832x1472 -> HTTP 402 x4      FAIL (burst limit)
1024x1792 @25s spacing -> 200 once, 402 once   marginal
```

Successful 1024x1792 request actually returned **580x1015**, not the 1024x1792 asked for.
So anonymous Pollinations gives you a 9:16 image at roughly **580x1015**, with intermittent
`402 Payment Required` under burst. Treat as ~1 image per ~20-30 s, serialize + retry.

---

## A) Image Generation

### A1. pollinations/pollinations
- **URL:** https://github.com/pollinations/pollinations
- **Stars:** 5,170 · **License:** MIT · **Last push:** 2026-10-04 (today) · not archived
- **Does:** Open-source Gen-AI platform; the repo behind the free keyless
  `image.pollinations.ai` HTTP image API.
- **Install:** none — plain HTTP.
  ```bash
  pip install requests   # already satisfied here
  ```
- **RAM/CPU:** ~0 local. Generation happens remotely. Only cost is bandwidth.
- **Usage (EXECUTED LIVE, this is real output from this box):**
  ```bash
  curl -o scene.jpg "https://image.pollinations.ai/prompt/dark%20cinematic%20crime%20scene,\
  abandoned%20warehouse%20at%20night,%20moody%20low-key%20lighting,%20film%20grain\
  ?width=1024&height=1792&nologo=true&seed=42"
  ```
  Verified: HTTP 200, `image/jpeg`, ~5 s. Returns 580x1015 (see §0).
- **Pros:** zero cost, zero signup, zero key, MIT, actively committed today, no local
  footprint at all, `seed=` gives reproducible frames for motion continuity, prompt-only
  interface is trivially scriptable for batch.
- **Cons:** anonymous tier is a burst token bucket — `402` under any parallelism, and it
  silently downgrades resolution instead of erroring. No SLA, no support, ToS can change.
  Not enough throughput to be the *only* image source; fine as primary with a fallback.
- **Verdict: USE** — primary image source, wrapped in serialize + backoff + retry.

### A2. Comfy-Org/ComfyUI
- **URL:** https://github.com/Comfy-Org/ComfyUI · **Stars:** 136,042
- **License:** GPL-3.0 · **Last push:** 2026-10-04 · not archived
- **Note:** the repo moved from `comfyanonymous/ComfyUI`; the old path no longer resolves
  in search. Use `Comfy-Org`.
- **Does:** node-graph Stable Diffusion / Flux workflow engine. The de-facto base for
  nearly every open image+video generation ecosystem.
- **Install:**
  ```bash
  git clone https://github.com/Comfy-Org/ComfyUI.git && cd ComfyUI
  pip install -r requirements.txt      # pulls torch
  python main.py --cpu                 # CPU mode flag exists
  ```
- **RAM/CPU:** 8 GB RAM workable for SD 1.5 at 512px on `--cpu`; ~20-60 s/image on 4 cores.
  Disk: **does not fit here** (torch + weights > 4.1 GB free).
- **Usage (from docs, NOT executed — install does not fit on this box):**
  ```bash
  python main.py --cpu --lowvram --preview-method none
  ```
- **Pros:** the only real answer for local, offline, unlimited image generation. Nodes make
  batch and post-processing easy. Enormous custom-node ecosystem (incl. AnimateDiff, see B4).
- **Cons:** GPL-3.0 (copyleft — read before commercial use). Node-graph learning curve.
  Needs multi-GB disk + a real GPU to be pleasant. **Hard blocker here: 4.1 GB free disk.**
- **Verdict: SKIP on this box** (TEST FURTHER only after freeing 20+ GB and adding a GPU).

### A3. huggingface/diffusers
- **URL:** https://github.com/huggingface/diffusers · **Stars:** 34,648
- **License:** Apache-2.0 · **Last push:** 2026-10-02 · not archived
- **Does:** the official PyTorch library for diffusion pipelines (SD, SDXL, Flux, LTX, Wan).
- **Install:** `pip install diffusers transformers accelerate` (torch separate; torch 2.14.1
  does have a 3.14-compatible release, confirmed via `pip index versions torch`).
- **RAM/CPU:** 4 GB for SD 1.5 in fp16 on CPU; SDXL needs ~8 GB+ and is impractical.
- **Usage (from docs, NOT executed — disk):**
  ```python
  from diffusers import AutoPipelineForText2Image
  pipe = AutoPipelineForText2Image.from_pretrained("stabilityai/sd-turbo", torch_dtype=torch.float16)
  img = pipe("moody crime scene, low key", num_inference_steps=4).images[0]
  ```
- **Pros:** Apache-2.0 (cleanest license of the big three), scriptable, first-class CPU path,
  the standard interface every tutorial and model card uses. Best choice *if* disk is freed.
- **Cons:** it is a library, not a product — you write all the glue. Model downloads are
  multi-GB. No built-in batch UI, no built-in upscaling.
- **Verdict: TEST FURTHER** — correct long-term choice once disk is available.

### A4. AUTOMATIC1111/stable-diffusion-webui
- **URL:** https://github.com/AUTOMATIC1111/stable-diffusion-webui · **Stars:** 165,188
- **License:** AGPL-3.0 · **Last push:** 2026-03-02 · not archived
- **Does:** the original one-click Stable Diffusion GUI; built-in batch generation and
  built-in Real-ESRGAN upscaling in the same UI.
- **Install:**
  ```bash
  git clone https://github.com/AUTOMATIC1111/stable-diffusion-webui.git
  cd stable-diffusion-webui && ./webui.sh --skip-torch-cuda-test
  ```
- **RAM/CPU:** 4 GB for SD 1.5 CPU mode. Disk again the blocker (~5 GB+).
- **Usage (from docs, NOT executed):**
  ```bash
  ./webui.sh --medvram-sdp --no-half
  ```
- **Pros:** simplest UI of the three, native batch tab, integrated upscaler, huge community.
- **Cons:** **AGPL-3.0** is the strongest copyleft here — viral trigger if you modify and
  serve it as a network service. Last push 2026-03-02 is ~7 months, just outside your
  6-month maintenance bar, and the ecosystem has moved to Forge/ComfyUI.
- **Verdict: SKIP** — AGPL plus stale-plus-superseded; use ComfyUI or diffusers instead.

---

## B) Video Generation / Animation

### B1. Zulko/moviepy
- **URL:** https://github.com/Zulko/moviepy · **Stars:** 14,946
- **License:** MIT · **Last push:** 2026-08-26 · not archived
- **Does:** Python video editing library — clip assembly, Ken Burns (zoom/pan via animated
  `resize`+`crop`), text overlays, audio muxing, frame interpolation.
- **Install:** `pip install moviepy` (ffmpeg 8.1.2 already present on this box).
- **RAM/CPU:** very light — ~200-400 MB for a 60 s 1080x1920 clip. Pure CPU, no torch.
- **Usage (pattern used by this agency already in `render_pipeline*.py`):**
  ```python
  from moviepy import ImageClip, concatenate_videoclips
  clip = (ImageClip("scene.jpg")
          .with_duration(3)
          .resized(lambda t: 1 + 0.12 * t)   # slow zoom = Ken Burns
          .with_position(("center", "center")))
  concatenate_videoclips([clip, clip2, clip3]).write_videofile("short.mp4", fps=30)
  ```
- **Pros:** the one dependency that does everything the brief asks of a "FFmpeg wrapper" —
  stitching, Ken Burns, parallax, subtitle burn-in, audio — with no GPU and ~zero disk.
  MIT. Actively maintained. Already proven on this box (5 finished Shorts in `production/`).
- **Cons:** slow for heavy filters (pure Python frame loop); not an AI generator, only an
  animator/compositor. `write_videofile` is CPU-bound, so use 1080x1920 not 4K.
- **Verdict: USE** — the motion layer for the whole pipeline.

### B2. Wan-Video/Wan2.2
- **URL:** https://github.com/Wan-Video/Wan2.2 · **Stars:** 17,716
- **License:** Apache-2.0 · **Last push:** 2026-09-21 · not archived
- **Does:** state-of-the-art open text-to-video and image-to-video (T2V/I2V), 14B MoE.
- **Install:** `git clone https://github.com/Wan-Video/Wan2.2.git` + ~20 GB weights.
- **RAM/CPU:** **needs 16 GB+ VRAM.** Not CPU-runnable at any RAM. CPU inference is not
  supported by the project.
- **Usage (via free HF Space + token, per `SOLUTIONS.md`):**
  ```bash
  python infer.py --task i2v-A14B --size 480P --ckpt_dir Wan2.2-I2V-A14B --image ref.jpg
  ```
- **Pros:** genuinely cinematic, high-detail motion — closest to real "horror scene"
  footage of anything open. Apache-2.0, actively developed.
- **Cons:** cannot run here at all. Only reachable through free ZeroGPU Spaces, whose
  anonymous quota `SOLUTIONS.md` measured as exhausted (0 of 24 Tor exits usable). Needs a
  free HF token, and even then it's a few clips/hour — nowhere near 10-20 shots per Short.
- **Verdict: SKIP as a primary; TEST FURTHER** as an opportunistic hero-shot enhancer once
  the free HF token exists. Never plan a Short around it.

### B3. Lightricks/LTX-Video
- **URL:** https://github.com/Lightricks/LTX-Video · **Stars:** 11,017
- **License:** Apache-2.0 · **Last push:** 2026-01-05 (~9 months) · not archived
- **Does:** lightweight video model (2B) with very fast inference, T2V + I2V + keyframe
  control; notably the cheapest open video model per-second.
- **Install:** `pip install ltx-video` or clone + `diffusers` pipeline.
- **RAM/CPU:** ~16 GB VRAM for practical use; not a CPU path.
- **Usage (from docs, NOT executed):**
  ```python
  from diffusers import LTXPipeline
  pipe = LTXPipeline.from_pretrained("Lightricks/LTX-Video", torch_dtype=torch.bfloat16)
  ```
- **Pros:** the most practical open video model if you ever get a GPU; Apache-2.0.
- **Cons:** **fails your 6-month maintenance bar** (last push 2026-01-05). No CPU path, no
  real local story on this box. 768p-ish ceiling on quality for cheap variants.
- **Verdict: SKIP** — stale by your own criteria and unusable on this hardware.

### B4. Kosinkadink/ComfyUI-AnimateDiff-Evolved
- **URL:** https://github.com/Kosinkadink/ComfyUI-AnimateDiff-Evolved · **Stars:** 3,552
- **License:** Apache-2.0 · **Last push:** 2026-07-28 · not archived
- **Note:** the current canonical AnimateDiff-for-ComfyUI node. `guoyww/AnimateDiff` (the
  original, 12,262★) last pushed 2024-07-31 — stale. `Gourieff/ComfyUI-AnimateDiff-Evolved`
  and `ZHO-ZHO-ZHO/...` no longer resolve; don't trust blog posts citing them.
- **Does:** turns still SD images into short animated clips (motion modules, camera
  motion, motion LoRAs) inside ComfyUI.
- **Install:** drop the folder into `ComfyUI/custom_nodes/`.
- **RAM/CPU:** inherits ComfyUI's — needs torch + SD + motion module weights.
- **Usage (ComfyUI graph):** `Load Checkpoint -> AnimateDiff (Evolved Model Loader) ->
  AnimateDiff Advanced -> KSampler -> Video Combine (frame batch)`
- **Pros:** the cheapest way to get *real* generated motion (not just a zoom) from stills,
  and it pairs perfectly with A1 output. Apache-2.0, actively maintained.
- **Cons:** inherits every ComfyUI blocker — torch, weights, disk, GPU. ~2-4 s/frame on
  30-frame clips even on good hardware, far worse on 4 CPU cores.
- **Verdict: TEST FURTHER** — the upgrade path from Ken Burns to real motion, gated behind
  freeing disk and ideally adding a GPU.

---

## C) Supporting Tools

### C1. ggml-org/whisper.cpp
- **URL:** https://github.com/ggml-org/whisper.cpp · **Stars:** 54,121
- **License:** MIT · **Last push:** 2026-10-02 · not archived
- **Does:** Whisper in C/C++ with no Python/torch dependency; word-level timestamps.
- **Install:**
  ```bash
  git clone https://github.com/ggml-org/whisper.cpp && cd whisper.cpp
  cmake -B build && cmake --build build -j4 --config Release
  ./build/bin/whisper-cli -m models/ggml-base.en.bin -f audio.wav -owts
  ```
- **RAM/CPU:** **~150-300 MB, pure CPU.** base.en on 4 cores transcribes 60 s audio in
  roughly real-time-ish. This is the single best RAM-per-quality ratio in the whole report.
- **Usage:** see install line; `-owts` emits `.wts` word-timestamp JSON for karaoke captions.
- **Pros:** MIT, tiny, no torch (crucial — torch is what blows the disk budget), actively
  maintained, word-level timestamps are exactly what karaoke-caption Shorts need, binary
  ships as a single file. Survives Python 3.14 because it doesn't care about Python.
- **Cons:** no diarization, no forced-alignment refinement (that's whisperX, which needs a
  GPU), C++ build step, slower than faster-whisper on CPU.
- **Verdict: USE** — caption/verification layer. Best RAM behaviour of anything listed.

### C2. SYSTRAN/faster-whisper
- **URL:** https://github.com/SYSTRAN/faster-whisper · **Stars:** 25,693
- **License:** MIT · **Last push:** 2026-10-01 · not archived
- **Does:** reimplementation of Whisper on CTranslate2 — ~4x faster, 4x less memory,
  Python-native, built-in word timestamps and VAD.
- **Install:** `pip install faster-whisper`
- **RAM/CPU:** ~1-2 GB for `small`/`medium` int8 on CPU.
- **Usage (from docs; note the install risk below):**
  ```python
  from faster_whisper import WhisperModel
  m = WhisperModel("small", device="cpu", compute_type="int8")
  segs, _ = m.transcribe("vo.mp3", word_timestamps=True, vad_filter=True)
  ```
- **Pros:** drop-in Python, VAD filtering removes silence artefacts (good for TTS output),
  int8 quantization makes `medium` feasible on CPU. MIT and current.
- **Cons:** depends on `ctranslate2`, which is a compiled dep — my `pip download` for a
  cp314 wheel **timed out** on this box, and CTranslate2 historically lags new Python
  releases. More fragile on Python 3.14 than whisper.cpp. Higher RAM than whisper.cpp.
- **Verdict: TEST FURTHER** — better quality than whisper.cpp if the wheel installs; fall
  back to C1 if not. Try it first, keep C1 as the guaranteed path.

### C3. jgm/pandoc
- **URL:** https://github.com/jgm/pandoc · **Stars:** 46,545
- **License:** GPL-2.0 · **Last push:** 2026-10-04 · not archived
- **Does:** the universal document converter; Markdown → PDF (via LaTeX), DOCX, HTML, EPUB.
- **Install:** `dnf install pandoc texlive-latex` or `pip install pypandoc_binary`
  (the `pypandoc_binary` wheel bundles the binary — no LaTeX needed for the common path).
- **RAM/CPU:** ~100 MB. PDF via LaTeX needs `texlive-latex` (~1-2 GB disk) — avoid on this
  box; use `--pdf-engine=weasyprint` or the HTML path instead.
- **Usage (from docs):**
  ```bash
  pandoc lead_magnet.md -o lead_magnet.pdf --pdf-engine=weasyprint --toc
  ```
- **Pros:** bulletproof, actively maintained today, one tool for every lead-magnet format,
  huge filter ecosystem. The `--pdf-engine=weasyprint` route avoids the LaTeX disk cost.
- **Cons:** GPL-2.0 (invoking a CLI binary is fine; shipping it inside a closed product is
  not). Canonical PDF path wants LaTeX, which is a disk problem here.
- **Verdict: USE** for lead magnets (with weasyprint), keeping disk cost low.

### C4. elliottblackburn/mdpdf
- **URL:** https://github.com/elliotblackburn/mdpdf · **Stars:** 803
- **License:** Apache-2.0 · **Last push:** 2026-07-31 · not archived
- **Does:** one-command Markdown → PDF with custom CSS stylesheet support. Chrome
  headless under the hood.
- **Install:** `pip install mdpdf` (pulls `mdpdf-bin`).
- **RAM/CPU:** ~250 MB while headless Chrome runs. Disk trivial.
- **Usage (from docs):**
  ```bash
  mdpdf -o out.pdf --stylesheet style.css lead_magnet.md
  ```
- **Pros:** the smallest useful option — CSS control is what makes a lead magnet look
  branded rather than like a default LaTeX dump. Apache-2.0 (cleanest license in this
  category), actively maintained.
- **Cons:** far fewer stars (803) so a thinner community. Headless Chrome on a 4-core box
  with 2.3 GB free RAM can OOM if Chrome is also running. Fedora needs `chromium`/
  `google-chrome` present — **not installed on this box.**
- **Verdict: USE** (after `dnf install chromium`) — best looking PDFs per line of config,
  and CSS lets the "lead magnet" carry your brand.

### C5. xinntao/Real-ESRGAN
- **URL:** https://github.com/xinntao/Real-ESRGAN · **Stars:** 36,974
- **License:** BSD-3-Clause · **Last push:** 2024-08-06 · **FAILS your 6-month rule**
- **Does:** practical image/video super-resolution; the reference open upscaler.
- **Install:** `pip install basicsr realesrgan` (or clone and use `inference_realesrgan.py`).
- **RAM/CPU:** runs on CPU, but **~1-2 GB RAM and 1-5 min per 1080x1920 frame** on 4 cores.
- **Usage:**
  ```bash
  python inference_realesrgan.py -n realesr-general-x4v3 -i scene.jpg -o scene_4x.png
  ```
- **Pros:** permissive BSD-3, the de-facto standard, weights freely downloadable, and it
  genuinely fixes the A1 problem: 580x1015 → 4x overshoots, so use `x2` and crop to
  1080x1920, or use `realesrgan-x2plus` at exactly 2x.
- **Cons:** **unmaintained since Aug 2024** — `basicsr` also has a known torchvision
  version conflict that bites on new Python. Slow on CPU. You'd run it in batch overnight,
  not inline.
- **Verdict: TEST FURTHER** — only because nothing better exists for CPU upscaling. Two
  caveats: it's stale, and the sibling `xinntao/Real-ESRGAN-ncnn-vulkan` (2,247★) needs
  Vulkan and is *also* stale (2024-05-10) — skip that one. Prefer the `ncnn`/ONNX route or
  an ONNX community release on Python 3.14.

---

## D) Full Pipeline / All-in-One

### D1. harry0703/MoneyPrinterTurbo
- **URL:** https://github.com/harry0703/MoneyPrinterTurbo · **Stars:** 128,322
- **License:** MIT · **Last push:** 2026-10-04 (today) · not archived
- **Does:** keyword → finished HD short video. Script → LLM → TTS → stock/AI visuals →
  captions → music → stitched MP4, plus one-click publish to YouTube Shorts / TikTok / IG.
- **Its own hardware table (quoted from its README, verified live):**
  | | minimum | recommended | ideal |
  |---|---|---|---|
  | CPU | 4 cores | 6-8 cores | 8+ cores |
  | RAM | 4 GB | 8 GB | 16 GB+ |
  | GPU | not required | 4 GB VRAM | 8 GB VRAM |
- **Install** (Linux path is `uv`; it wants Python 3.11, and this box has 3.14):
  ```bash
  git clone https://github.com/harry0703/MoneyPrinterTurbo.git && cd MoneyPrinterTurbo
  uv python install 3.11 && uv sync --frozen
  cp config.example.toml config.toml
  ./run.sh                                  # WebUI on 127.0.0.1:8501
  ```
  Docker alternative: `docker compose up` (WebUI 8501, API docs 8080/docs).
- **RAM/CPU:** matches this box's 4-core/8 GB tier exactly, **provided** you stay on cloud
  LLM + cloud/Edge TTS + online assets — its README says so explicitly: with cloud LLM,
  cloud TTS and online material, CPU/RAM matter more than GPU.
- **Zero-cost paths it actually supports (verified in README):**
  - **Edge TTS — free, no API key required.**
  - Pollinations listed as an LLM/gateway provider.
  - Batch generation of multiple finished videos, with task history.
  - Local Whisper for editable transcripts.
  - Optional Ollama for fully local LLM.
- **Pros:** by far the most active project in this report (128k★, committed today, MIT).
  Explicitly GPU-optional (its table marks 4 cores / 8 GB RAM as supported). Auto-publish
  closes the loop to YouTube. Local assembly means no cloud cost for the render step.
- **Cons:** **rejected by the boss on 2026-10-04 — do not install.** Two independent
  reasons: (1) its visual source is stock footage (Pexels), which is the wrong aesthetic for
  true crime and needs a free Pexels key anyway; (2) **we already have a working voice
  agent**, so its TTS layer duplicates infrastructure we own and trust. Adopting it would
  mean rewiring its image stage to A1 *and* throwing away our narration pipeline — at which
  point it is only orchestrating, which moviepy (B1) already does with less code.
  Also: paid sponsors (OfoxAI) in the README pushing per-second video APIs, `uv sync`/Docker
  fights the 4.1 GB free disk, and it wants Python 3.11 vs our 3.14.
- **Verdict: SKIP (rejected by boss).** Keep the row in this report as a documented
  negative — it is the most-starred project here and the rejection is deliberate, not an
  oversight. Revisit only if we ever lose our own voice pipeline.

### D2. leamsigc/ShortsGenerator
- **URL:** https://github.com/leamsigc/ShortsGenerator · **Stars:** 354
- **License:** MIT · **Last push:** 2026-09-30 · not archived
- **Does:** fully-local Shorts factory — script gen, stock video search, TTS, subtitles,
  background music, social scheduling, Flask + Node frontend.
- **Install:**
  ```bash
  git clone https://github.com/leamsigc/ShortsGenerator.git && cd ShortsGenerator
  pip install -r requirements.lock     # author ships a pre-resolved lock; plain requirements.txt stalls
  python app.py
  ```
- **RAM/CPU:** local-first; auto-detects GPU and degrades gracefully without one.
- **Zero-cost paths (verified in README):** script gen via **`g4f` (free, no key)**;
  **local TTS** via Supertonic (10 voices), Qwen3-TTS (9 timbres + voice clone), KittenTTS;
  `GOOGLE_API_KEY` and `OPENAI_API_KEY` are both listed as **optional**.
- **Pros:** the only genuinely **zero-key** all-in-one found — g4f for scripting plus local
  TTS means the whole thing can run with no paid account at all, which is stricter than
  MoneyPrinterTurbo's cloud-LLM-first default. Ships a `requirements.lock` (good practice;
  most of these projects don't). Local-first and Linux-native. MIT.
- **Cons:** only 354★ — young, thin community, expect breakage. Its `torch/CUDA + qwen-tts`
  dependency graph is heavy and will strain 4.1 GB free disk. Stock-video-centric rather
  image-centric, so it would need the same A1 rewiring. No evidence of true-crime/
  dark-cinematic prompting.
- **Verdict: TEST FURTHER** — the best *fully-local, zero-key* candidate. Try it before
  committing to moviepy if the disk holds up.

---

## Considered and rejected (not verified — do not cite these)

- **`kkroening/ffmpeg-python`** (11,011★, Apache-2.0) — the FFmpeg wrapper everyone
  recommends. **Last push 2024-08-04: abandoned.** Use moviepy (B1) instead.
- **`jianfch/stable-ts`** (2,281★, MIT) — **archived.** Superseded by faster-whisper.
- **`guoyww/AnimateDiff`** (12,262★) — original, last push 2024-07-31, stale.
- **`xinntao/Real-ESRGAN-ncnn-vulkan`** (2,247★) — last push 2024-05-10, needs Vulkan.
- **`black-forest-labs/flux`** (26,005★, Apache-2.0) — last push 2025-07-31, stale by 14
  months, and the weights need far more RAM/disk than this box has.
- **`Djdefrag/QualityScaler`** (3,202★, MIT) — best-maintained upscaler found
  (2026-08-27), but its README states *"Windows app"* and *"Any DirectX12 compatible GPU
  with ≥4GB VRAM."* **Windows-only. SKIP on Fedora.**
- **`SamurAIGPT/AI-Faceless-Video-Generator`** (509★, MIT) — README requirements state
  *"OpenAI API key"*. Not zero-cost.
- **`Anil-matcha/AI-Youtube-Shorts-Generator`** (5,233★, MIT) — wrong direction: it chops
  *existing* long videos into Shorts (an OpusClip alternative). We need topic → video.
- **`mifi/lossless-cut`** (44,259★, GPL-2.0), **`remotion-dev/remotion`** (61,757★, pushed
  2026-10-03 but SPDX `NOASSERTION` — custom/proprietary-ish licence, needs a licence
  review before commercial use), **`m-bain/whisperX`** (24,357★, needs GPU),
  **`SubtitleEdit/subtitleedit`** (14,420★ — Windows-only), **`AUTOMATIC1111/stable-diffusion-webui-forge`**
  (does not resolve — wrong owner path), **`Stability-AI/sd-turbo`** / **`sdxl-turbo`** /
  **`nota-ai/bk-sdm-tiny`** / **`THUDM/CogVideo`** (none resolve at those paths).
- **Zero-result searches** (so no repo exists matching these descriptions under those
  terms): `youtube shorts automation AI video pipeline stars:>400`,
  `ken burns effect image video python OR parallax OR panzoom stars:>30`,
  `stable diffusion CPU low memory stars:>100 pushed:>2026-01-01`. I did not manufacture
  entries to fill these categories — B1 covers Ken Burns.

---

## FINAL RECOMMENDATION — top 3

> **Revised 2026-10-04 after boss review.** MoneyPrinterTurbo is **rejected** (stock-clip
> visuals + it duplicates our existing voice agent). moviepy (B1) is promoted from
> "support cast" into the core pipeline as the video layer. Pollinations output has been
> QA'd — see §A1-QA below.

### The pipeline

```
topic / case file
   │
   ├─(1) pollinations/pollinations ......... free keyless AI stills, 9:16, seeded
   │        └─ 580x1015 JPEG, SERIALIZED + backoff on 402
   │
   ├─(2) Zulko/moviepy ...................... Ken Burns / parallax motion, stitching,
   │        └─ 1080x1920 @ 30fps, CPU only, ~0 extra disk
   │
   ├─(3) ggml-org/whisper.cpp .............. word-timestamp transcript from OUR
            existing voice agent's output → karaoke caption burn-in
            (also catches mispronounced case names, which matters in true crime)

  pre-step: our own voice agent produces the narration MP3 (already exists)
```

Support cast, added only when needed: **mdpdf** + `dnf install chromium` for lead magnets;
**faster-whisper** instead of C1 *if* its ctranslate2 wheel installs on Python 3.14;
**Real-ESRGAN x2** in an overnight batch if 580x1015 is too soft (see the QA caveat).

### Why these 3 beat the rest

**1. They are the only three that fit the box as it actually is.** I measured 4 cores,
7.6 GB RAM — and, decisively, **4.1 GB free disk**. Every serious local generator needs
`torch` plus 2-7 GB of weights, which does not fit. ComfyUI (A2), diffusers (A3) and A1111
(A4) are therefore *not* slower or lower-quality choices — they are uninstallable. C1 is the
only listed tool that is a single self-contained binary with no Python/torch dependency.

**2. They have genuinely zero cost, verified rather than assumed.** A1 needs no key and no
account — I pulled real JPEGs from it on this box (QA'd in §A1-QA). C1 is MIT software on
our own CPU. moviepy is MIT, pure-CPU, and needs only the ffmpeg 8.1.2 already installed.
Critically, **none of the three duplicates the voice agent we already own** — that was the
specific reason D1 was cut.

**3. They cover the three jobs with the least overlap and the least glue code.** A1 makes
frames, moviepy makes them move and stitches the timeline, C1 supplies timed captions for
the narration we already produce. C1 is the only listed tool giving **word-level timestamps**
without a GPU (`whisperX` is the GPU-only alternative), and karaoke captions are the biggest
retention lever on Shorts — that gap is what decides the third slot. moviepy also replaces
D1 entirely: it already does Ken Burns + stitching + caption burn-in in ~30 lines, which is
why dropping MPT cost us nothing.

**4. All three are actively maintained.** A1 was committed **2026-10-04** (the day of this
research), C1 on 2026-10-02, moviepy 2026-08-26. Contrast with what else looks attractive
on paper: `ffmpeg-python` abandoned 2024-08, `Real-ESRGAN` 2024-08, `AnimateDiff` 2024-07,
`flux` 2025-07, `stable-ts` **archived**.

---

## A1-QA — Pollinations output, measured (not eyeballed)

I cannot view images (this model has no image input), so I measured them numerically
instead: mean luma `Ymean`, 5th/95th percentile luma, RMS contrast, mean HSV saturation,
mean absolute gradient as a `detail` proxy, and unique-colour count. Five surviving renders
are saved in `production/pollinations_samples/`.

| file | WxH | Ymean | Y5 | Y95 | RMS | sat | detail | colours |
|---|---|---|---|---|---|---|---|---|
| poly_test.jpg | 580x1015 | 25.8 | 1.0 | 106.5 | 47.8 | 139.6 | 1.83 | 12,787 |
| hi_2.jpg | 580x1015 | 26.9 | 2.1 | **64.2** | **35.3** | 140.3 | 2.45 | 14,050 |
| tc_1.jpg | 580x1015 | 35.8 | 1.1 | **169.8** | 47.8 | 132.9 | 2.11 | 11,692 |
| tc_2.jpg | 580x1015 | 68.6 | 16.1 | 207.3 | 53.9 | 93.2 | 2.75 | 21,650 |
| ref_test.jpg | 512x512 | 63.8 | 9.8 | 241.0 | 70.4 | 83.3 | **10.87** | 27,508 |

Reading it against a dark cinematic target (`Ymean` ~25-60, `Y5` near 0 for crushed blacks,
`Y95` 120-220 for a real highlight, RMS 40-70 for punch):

- **Darkness: reliable.** `Ymean` 25.8-35.8 and `Y5` ~1.0 on the three best — genuinely
  low-key with crushed blacks. This is not accidental; the dark prompts work.
- **Cinematic highlight: mostly works, and I can now predict failure.** `tc_1` has
  `Y95`=169.8 (a real light source in frame) = proper noir. `hi_2` failed at `Y95`=64.2 with
  `RMS`=35.3 — **no highlight means no contrast means mud**, and that is measurable in
  advance. Screen candidates on `Y95`/`RMS` and regenerate the rejects instead of hunting
  through thumbnails.
- **Sharpness: the real weakness.** `detail` is only 1.8-2.75 across all four 9:16 renders.
  Compare `ref_test` at 10.87 — so this is partly content variance, but 580x1015 is both
  small *and* soft.
- **Verdict:** usable as **moody backgrounds under caption text and Ken Burns motion**, where
  softness is masked by movement and overlays. **Not** usable as a sharp hero frame.
  If sharpness matters, run Real-ESRGAN x2 overnight → 1160x2030, then crop to 1080x1920.
- Quota during the session: **4 successes / 12 attempts.** Serialized at 25-30 s spacing;
  parallel fan-out returned `402` every time.

---

### Honest risks in this plan

- **A1's throughput is the weak link.** Anonymous Pollinations returned `402` on 4 of 4
  parallel requests and intermittently at 25 s spacing, and silently returned 580x1015
  instead of the 1024x1792 requested. A 50-60 s Short needs 10-20 images, so this source
  alone will stall a batch run. It must run **serialized with retry/backoff and resume
  checkpoints**, and ideally gets a free account for a higher ceiling. This is the same
  exhaustion pattern `SOLUTIONS.md` already documented for video — create the free HF
  token.
- **Softness is the top visual risk.** A1 output is 580x1015 *and* soft (`detail` 1.8-2.75).
  Budget a Real-ESRGAN x2 overnight pass before you call a frame final.
- **Quota will stall batch runs.** 4 successes / 12 attempts this session. A 60 s Short needs
  10-20 images, so serialization + resume checkpoints are mandatory, not nice-to-have.
- **Python version.** faster-whisper's ctranslate2 wheel is unproven on 3.14; whisper.cpp
  sidesteps Python entirely. Install C1 first.

### First three actions

1. `dnf install chromium` (for C4 lead magnets) + free disk space — unblocks the
   Real-ESRGAN pass and any future local ComfyUI.
2. `export HF_TOKEN=...` (free, read-only, ~2 min) — restores quota for any free Space.
3. Stand up A1 + B1 first as a standalone "topic → 10 dark stills → Ken Burns MP4" test,
   *before* anything else. A1 + B1 is the whole pipeline minus captions, needs no install,
   and proves the aesthetic on cheap parts.