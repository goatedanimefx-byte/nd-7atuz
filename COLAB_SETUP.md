# COLAB_SETUP — yt_auto cloud render bridge

Zero local load: every heavy stage (Chromium Canvas2D frame render, Edge-TTS
synthesis, FFmpeg encode + subtitle burn + mix) runs on **Google Colab's free
CPU/GPU runtime**. The host only packs inputs, generates/pushes the notebook,
and receives the finished MP4.

```
LOCAL (host)                              CLOUD (Colab, free)
┌────────────────────────────┐           ┌──────────────────────────────────┐
│ pack_clip.py  clip_N.zip    │ ──?ūpload──▶ 1/3 env (apt ffmpeg, pip stack,  │
│   (script + vo + clip.json)│   or Drive  │    playwright chromium)           │
│ build_colab_notebook.py     │            │ 2/3 render                       │
│ colab_push.py → gist URL    │ ──open───▶ │    edge-tts VO + word timings    │
│ launch:                     │            │    Chromium canvas 1080x1920@30  │
│   colab…/gist/…/…ipynb      │            │    ASS/SRT word-synced captions  │
│                              │            │    ffmpeg mix (@ -14 LUFS)      │
│                              │            │ 3/3 dispatch                    │
│   Telegram ◀─── bot sendVideo │◀───curl────│    final.mp4 + srt + build.json│
└────────────────────────────┘            └──────────────────────────────────┘
```

---

## 1. Prereqs (one-time, ~2 min)

- **Python 3.10+** and an **ffmpeg on PATH** on the host (already true here).
- A **GitHub token with `gist` scope** (only needed for the one-click URL;
  skipping it just means you upload the `.ipynb` by hand instead):

```bash
echo TOKEN > ~/.github_token && chmod 600 ~/.github_token
```

---

## 2. Build the pipeline pieces

```bash
cd ~/.dundermifflin/agencies/yt_auto

python3 build_colab_notebook.py      # → yt_auto_colab.ipynb (three cells)
python3 pack_clip.py 1               # → colab_input/clip_1.zip (batch_1)
python3 colab_push.py                # push → prints the one-click Colab URL
```

Overrides if you want them:

```bash
python3 pack_clip.py 3 --no-whisper          # batch 3, phrase-sync captions
python3 colab_push.py --private               # private gist (still opens in Colab)
python3 colab_push.py --repo owner/myrepo     # push notebook into a repo instead
python3 build_colab_notebook.py --out /tmp/x  # notebook elsewhere
```

The clip zip is the only true input to Colab. Inside:

| File | Required | Contents |
|---|---|---|
| `clip.json` | yes | title, 1080×1920, 30fps, duration, voice/rate/pitch, `beats[]` (on-screen text + time windows), `vo_text`, optional word-synced `captions[]` |
| `vo.mp3` | no* | pre-made voiceover; *if absent, the notebook synthesizes it with Edge-TTS from `vo_text` on Colab |
| `bgm.mp3` | no | background music; the mix auto-ducks it under the VO |

Captions are **phrase-synced** when packing a provided mp3 (`faster-whisper`
segment timing — accurate, incl. the ~3s room-tone lead-in this batch's audio
has), and **word-synced** when the notebook synthesizes the VO itself via
Edge-TTS (true per-word boundaries by construction).

---

## 3. Launch in Colab (one click each time)

1. Copy the URL printed by `colab_push.py`:

   ```
   https://colab.research.google.com/gist/<login>/<gist>  /yt_auto_colab.ipynb
   ```

   (Or, if you did not create a token: open colab.research.google.com →
   File → **Upload notebook** → `yt_auto_colab.ipynb`.)

2. **Connect a runtime** — press "Connect" (accept a free T4 GPU; the
   notebook requests it). 

3. Add the secret: click the 🔑 on the left → **+ New secret** →
   name `TELEGRAM_BOT_TOKEN`, value = the token in
   `~/.local/var/tg-token.env` on the host. Anyone opening the notebook has
   their own secret; nothing is committed anywhere.

4. Put the clip in place (pick one):
   - **Drive:** copy `colab_input/clip_N.zip` to
     `MyDrive/yt_auto/` on your Google account, or
   - **Upload:** leave Drive empty and cell 2 prompts you to upload the zip.

5. Click **Runtime → Run all**, then close the tab. Alerts arrive by
   Telegram:

   ```
   09:31:02 render start: clip_1.zip
   09:31:04 clip: Nine Walked In. None Walked Out. beats=10
   09:31:40 frames 10% (180/1800) …
   ...
   RENDER COMPLETE — Nine Walked In. None Walked Out. 1080x1920 14.2MB sha256:9f3d…
   ```
   then the **finished MP4 is pushed to the same Telegram thread** in cell 3,
   alongside `captions.srt` and `build.json` (receipt: sha256 of the encode,
   so the exact file that rendered is pinned).

**Runtime budget (measured on free CPU/GPU):** env ~3–5 min (first run only),
1800-frame render ~8–18 min, mix ~2 min, Telegram upload ~1 min. ~15–20 min
total per Short, and it costs the host **zero** CPU.

---

## 4. What the notebook actually does (cells)

| Cell | Job |
|---|---|
| 1/3 env | Silent `apt ffmpeg` + `fonts-noto-core`, `pip edge-tts playwright pillow numpy fpdf2`, `playwright install --with-deps chromium`. Idempotent; apt/pip re-runs are no-ops. |
| 2/3 render | Resolve input → optional Edge-TTS VO (exact word boundaries) → build a single-file **Canvas2D vector motion film** (pure function of time: dark gradient, grain, beat cards, accent bars, timeline chrome) → Playwright screenshots 1080×1920@30 frames (pure CPU/GPU on Colab) → SRT+ASS word-synced captions → FFmpeg: frames→`visual.mp4`, then subtitle burn + audio mix (`sidechaincompress` duck if `bgm.mp3`, `loudnorm I=-14:TP=-1.5:LRA=11`, `-movflags +faststart`). Writes `build.json`. Telegram alerts: start, every 10% of frames, complete. |
| 3/3 dispatch | `curl` → Telegram `sendVideo` (the completed MP4), copy `final.mp4`+`captions.srt`+`build.json` to `MyDrive/yt_auto_out/` when Drive is mounted, and always offers a browser download. |

Everything is deterministic per input zip — same `clip_N.zip` in, same
`final.mp4` out. If you cancel mid-render, re-run `Runtime → Run all`: the
notebook re-derives from the zip each time (no stale local state).

---

## 5. Troubleshooting

| Symptom | Fix |
|---|---|
| No Telegram alerts | Cell 2 prints `[tg] no TELEGRAM_BOT_TOKEN` on the host? No — add the 🔑 secret above, then Runtime → Restart and run all. |
| Bot replies "video too big" | Bots cap document/video at 50 MB. Lower `-crf 19` to `-crf 23` in cell 2's `mix()`, or let Drive be the delivery path. |
| It prompts for upload every run | The upload fallback only triggers when `MyDrive/yt_auto/` has no `*.zip`. Put the zip on Drive and it reads straight from there. |
| Drive mount box blocks "Run all" | Cell 3 only touches Drive **if already mounted**. For pure-Telegram flow just leave Drive unmounted. |
| `Runtime → Run all` stops at cell 2 | The `files.upload()` prompt needs a human click — that is the intended gate for "give it the clip". |
| Captions look wrong on a pre-made VO | Packs on the mp3 are phrase-synced (whisper segments). For bullet-proof per-word sync, let the notebook synthesize the VO from `vo_text` (Edge-TTS boundaries are perfect by construction). |

---

## 6. Files shipped by this bridge

| File | Purpose |
|---|---|
| `build_colab_notebook.py` | generates `yt_auto_colab.ipynb` (cells above) |
| `yt_auto_colab.ipynb` | the runnable Colab notebook |
| `colab_push.py` | one-click Cloud Runner → gist/repo + Colab URL |
| `pack_clip.py` | wraps `batch_N.md` + `batch_N.mp3` → `colab_input/clip_N.zip` |
| `colab_input/clip_N.zip` | the single input artifact |