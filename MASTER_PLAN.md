# MASTER EXECUTION PLAN: True Crime / Horror YouTube Shorts Monetization (yt_auto)

**Agency:** `yt_auto`  
**Root:** `~/.dundermifflin/agencies/yt_auto`  
**Date:** 2026-10-04  
**Hardware:** 4 cores, 7.6 GB RAM, 4.1 GB free on /home (primary), 174 GB free on /mnt/mobiledrive (secondary), ffmpeg 8.1.2 installed  
**Assets ready:** 5 Shorts rendered (720x1280), scripts, voiceovers, case files (278.45s total)

---

## 1. FULL PIPELINE ARCHITECTURE

### Flow
```
Script (Markdown) → Audio (Edge-TTS, existing) → Scene prompts → Pollinations images → Ken Burns motion (MoviePy) → Subtitles (Whisper.cpp) → Final MP4 stitch (FFmpeg)
```

### Component mapping
| Stage | Tool/Repo | Notes |
|---|---|---|
| Script | Existing `production/scripts/*.md` | Hook + beat sheet + VO block + A/B hooks. Keep fact-corrected version. |
| Audio | Edge-TTS (existing via `tts_build.py`) | `en-US-ChristopherNeural`/`en-US-GuyNeural`, rate +5%, pitch -4Hz (edge-tts accepts Hz only). MP3s already generated. |
| Prompts | Local Python generator | Derive scene prompts per beat from script. Include: mood, lighting, camera angle, no gore closeups, text-free. Seed per scene for motion continuity. |
| Images | [pollinations/pollinations](https://github.com/pollinations/pollinations) (HTTP) | Keyless `image.pollinations.ai`. Observed: returns ~580x1015 despite 1024x1792 request. Must serialize (no parallel fan-out). Add retry/backoff on 402. Seeded. |
| Motion | [Zulko/moviepy](https://github.com/Zulko/moviepy) | CPU-only, MIT. Ken Burns (slow zoom/pan), imageclip per beat, concatenate, mux audio. Proven on box (existing renders). |
| Subtitles | [ggml-org/whisper.cpp](https://github.com/ggml-org/whisper.cpp) | MIT, single binary, word-level timestamps. CPU-only (~150-300MB). Build on /mnt/mobiledrive or /home if space allows. `whisper-cli -owts` → burn via MoviePy or ffmpeg subtitles filter. Fallback: faster-whisper if ctranslate2 wheel works on Python 3.14. |
| Stitch/encode | ffmpeg 8.1.2 (system) | H.264, 720x1280 (current), 30fps. Keep bitrate modest (2-3MB per 50-60s). |

### Where secondary disk is mounted/used
- **Primary:** `/home` (49G total, 43G used, 4.1G free) — avoid storing model weights/torch here. Keep only code, configs, prompts, small assets.
- **Secondary:** `/mnt/mobiledrive` (232G total, 59G used, 174G free) — mount target for models/weights, render cache, intermediate frames, whisper.cpp models (`ggml-base.en.bin` ~150MB, larger if needed), Real-ESRGAN weights, ComfyUI/AnimateDiff weights if ever adopted. Also use for batch render outputs.
- **Workflow:** read scripts/audio from `/home/prince/.dundermifflin/agencies/yt_auto/`, write images/frames/cache to `/mnt/mobiledrive/yt_auto_cache/`, final MP4 to `/home/.../production/final_packs/` or `/mnt/mobiledrive/yt_auto_renders/`.

### Expected time per Short (realistic)
Given measured Pollinations behavior (4/12 succeeded, ~25-30s between requests; burst gave 402), with CPU assembly:
| Stage | Time | Notes |
|---|---|---|
| Prompt gen + scene planning | 1-2 min | From script beat sheet. |
| Image fetch (10-20 scenes) | 8-15 min | Serialized, retry/backoff. With resume/checkpoints. |
| Ken Burns + assembly (MoviePy) | 5-10 min | CPU-bound on 4 cores for 720x1280@30fps. |
| Whisper.cpp subtitle extraction + burn-in | 2-5 min | `base.en` runs near real-time for 50-60s audio on 4 cores. |
| FFmpeg finalize | <1 min | Mux, trim, metadata. |
| **Total per Short** | **15-25 min** | Realistic with serialization. If parallel attempted, expect failures/quota stalls. |

---

## 2. CONTENT STRATEGY (True Crime Niche)

### Sub-niches ranked by CPM + competition fit
| Sub-niche | Shorts CPM range (typical) | Competition | Retention potential | Notes |
|---|---|---|---|---|
| Unsolved Mysteries | $2-$6 | Low-Mid | High (hook-driven) | Fits text-only visuals + narration. Less graphic, ad-safe. Best fit for this pipeline. |
| Cold Cases | $2-$5 | Low | High | Often decades old, public domain facts, less likely to trigger takedowns. Strong fit. |
| Historical Crimes | $1-$4 | Low | Mid-High | Educational angle, broader audience, easier to stay factual. |
| Serial Killers | $3-$8 | Very High | Very High | Highest CPM but saturated; riskier for ad-safety/restrictions. Avoid glorification. |
| Paranormal Crime | $1-$3 | Mid | Mid | Can blur fact/fiction; harder to keep strictly documentary. |

**Recommendation:** Focus primarily on **Unsolved Mysteries** + **Cold Cases** (highest CPM per effort, lowest saturation for AI-only visuals, easiest to enforce handling rules).

### Recommended posting cadence
- **Month 1:** 1 Short/day (minimum) or batch 5-7 twice/week and drip. With 15-25 min each, daily is feasible.
- **Month 2:** 1-2 Shorts/day if pipeline stable (after automation hardening).
- **Sustainable:** **1 per day** for solo operator running offline wholesale business too (under 20 min active time with checkpoints; generation runs in background).
- **Batching:** Create 7 Shorts over weekend (3-5 hours active) and schedule/post daily Mon-Sun.

### Hook formulas (drive 3-second retention)
| Formula | Example | Why |
|---|---|---|
| "X walked in. None walked out." | Dyatlov-style | Instant tension, creates question. |
| Contrasting normalcy | "He stayed for dinner." (Hinterkaifeck) | Subverts expectation. |
| Behavioral horror | "The killer ate their ice cream." (Setagaya) | Unsettling, fact-based, avoids gore. |
| Anonymous/erasure | "Someone wanted him to stay anonymous forever." (Somerton) | Mystery drives clicks/retention. |
| Empty space | "The ship came back with nobody on it." (Mary Celeste) | Visual negative space powerful. |
| "But..." pivot | "It looked routine. But..." | Forces viewer to stay. |
| Time anchor | "74 years later, it’s still unsolved." | Creates curiosity gap. |
| Concrete detail | "Every label had been cut off his clothes." | Specificity beats hyperbole. |

**Rule:** Put hook in first 3 seconds. Avoid graphic descriptions. Behavioral details outperform violent ones.

---

## 3. MONETIZATION STACK (All Revenue Streams)

| Stream | Timeline | Requirements | Realistic range |
|---|---|---|---|
| **Primary: YouTube Shorts ad revenue** | 3-6 months (typical) | 1K subs + 10M public Shorts views in last 90 days (current YPP threshold for Shorts). | $0-$3 per 1000 views (RPM/CPM varies by geography). Long tail. |
| **Secondary: Linkvertise CPM on free lead magnet PDF** | Day 1 | Free lead magnet (PDF of case file) hosted via Linkvertise/alternative. Put link in description + pinned comment (not on-screen). | $1-$4 CPM on outbound clicks; $0.01-$0.10 per click typical. Can earn before YPP. |
| **Tertiary: Paid upsell on Cosmofeed (₹299 INR) + Gumroad ($3.99 USD)** | Month 1-2 | Upsell expanded case files (deeper timeline, evidence, sources), or "Unsolved Cases Vol.1" pack. Need basic landing page. | ₹50-₹150 per sale (Cosmofeed net varies), $1.50-$3 per Gumroad sale after fees. Conversion 0.1%-1% of magnet clicks realistic. |
| **Quaternary: Affiliate links** | Day 1 | Amazon Associates (true crime books), VPN, Nord/Surfshark style (if relevant), Audible. Comply with YT affiliate disclosure. | 1%-4% commission. Low volume initially; compounds later. |

### Realistic revenue breakdown
| Monthly Views | YT Shorts Ad Revenue | Linkvertise (magnet clicks) | Paid upsell (0.3% of viewers click magnet, 1% convert) | Affiliates | **Total (realistic)** |
|---|---|---|---|---|---|
| **10K** | $2-$20 | $2-$15 | $0-$10 | $0-$2 | **$5-$30/month** |
| **50K** | $15-$70 | $8-$40 | $5-$30 | $2-$10 | **$30-$120/month** |
| **500K** | $150-$800 | $40-$150 | $40-$200 | $15-$40 | **$250-$1,000/month** |

*Assumptions:* Global audience mix, factual/educational content, conservative CTR (1-2% magnet click-through). Avoid inflated numbers. Focus on CTR optimization first.

---

## 4. 30-DAY LAUNCH ROADMAP

### Week 1: Pipeline wiring + first 7 Shorts batched
| Day | Task | Time | Notes |
|---|---|---|---|
| Mon | Verify disk layout. Create `/mnt/mobiledrive/yt_auto_cache/` and `/mnt/mobiledrive/yt_auto_renders/`. | 5 min | Use secondary disk for all heavy I/O. |
| Tue | Build whisper.cpp (`ggml-base.en.bin`). Test transcription on existing MP3. | 10-15 min | Build from source on mobiledrive or home. Keep binary + model on mobiledrive. |
| Wed | Wire Pollinations + MoviePy prototype: take 1 script → 3-5 scenes → Ken Burns MP4. Add serialize+retry+backoff on 402. | 10-15 min | Prove pipeline with existing assets. Don't parallelize. |
| Thu | Caption burn-in via whisper.cpp word timestamps + ffmpeg/MoviePy. Test on short_1. | 10-15 min | Verify timing matches narration. |
| Fri | Batch prompts for 7 Shorts (use existing scripts). Generate images in background (serialized). | 5-10 min (active) | Can run overnight in chunks with checkpoints. |
| Sat | Render 3-4 Shorts (MoviePy). Quality check: motion smoothness, text legibility, no hallucinations. | 5-10 min (active) | Background render. |
| Sun | Render remaining 3 Shorts. Prep case files as PDFs (mdpdf + chromium). | 5-10 min (active) | Ready for week 2 posting. |

**Week 1 goal:** Pipeline hardened, 7 Shorts ready.

### Week 2: Daily posting + cross-posts
| Day | Task | Time | Notes |
|---|---|---|---|
| Mon | Post Short 1. Fill {{LINK}} with Linkvertise magnet URL. Add pinned comment. | <5 min | Use ready-to-paste description. |
| Tue | Post Short 2. Reply to comments, enforce handling rules. | <5 min | Moderate for Setagaya/Somerton rule violations. |
| Wed | Post Short 3. Track early CTR to link (description clicks). | <5 min | Check Linkvertise dashboard. |
| Thu | Post Short 4. Reddit cross-post: find r/UnresolvedMysteries, r/UnsolvedMysteries (follow subreddit rules; avoid linking in body if forbidden, link in profile/comments only per rules). | <10 min | Comply with each sub’s rules. Don’t spam. Focus on discussion. |
| Fri | Post Short 5. X/Twitter thread: 3-5 tweets expanding case, link in last tweet or profile per rules. | <5 min | Keep factual, cite public record. |
| Sat | Post Short 6. Analyze top comments, answer factually. | <5 min | Build rapport without speculation. |
| Sun | Post Short 7. Weekly review: views, CTR, saves/shares. | <10 min | Log to KPI sheet. |

**Week 2 goal:** 7 Shorts live, first cross-traffic.

### Week 3: Optimize CTR + first Linkvertise earnings
| Day | Task | Time | Notes |
|---|---|---|---|
| Mon | A/B test pinned comment: stronger value prop vs curiosity. | <5 min | Change pinned comment text on best performer. |
| Tue | Tighten description: move link earlier context, keep compliant. | <5 min | Don’t put link on-screen. |
| Wed | Generate 3 more Shorts (backfill). | <10 min (active) | Keep pipeline fed. |
| Thu | Cross-post to X/Reddit for 1-2 best performers. | <5 min | Focus on winners. |
| Fri | Check Linkvertise earnings. Tweak magnet title/cover if CTR <1.5%. | <5 min | Iterate on hook-to-magnet alignment. |
| Sat | Batch images for next 5 Shorts. | <5 min (active) | Serialized in background. |
| Sun | Review top 3 by saves (algorithm signal). Plan next batch. | <5 min | Double down on winners. |

**Week 3 goal:** First Linkvertise earnings visible, CTR improved.

### Week 4: Launch paid product + outreach
| Day | Task | Time | Notes |
|---|---|---|---|
| Mon | Create upsell: “Unsolved Cases Vol.1” (expanded 5 case files + timeline extras) on Gumroad ($3.99) and/or Cosmofeed (₹299). | 10-15 min | Reuse existing case files, add TOC/index. |
| Tue | Update magnet to mention upsell context subtly (in PDF footer/last page), don’t hard sell in YT. | <5 min | Keep UX clean. |
| Wed | Post Short 8. Track conversions. | <5 min | Gumroad/Cosmofeed dashboards. |
| Thu | Outreach to small true crime creators (ethical: comment, DM only if allowed). Focus on collabs, not spam. | <10 min | Build network, don’t cross-post full videos. |
| Fri | Batch 5 more Shorts. | <5 min (active) | Maintain cadence. |
| Sat | Analyze week 4: paid conversions, subs growth. | <5 min | Log KPIs. |
| Sun | 30-day review: what worked, kill losers, double down. | <10 min | Plan Month 2. |

**Week 4 goal:** Paid product live, first sale (if traction).

---

## 5. SCALING PATH (After Month 1)

| Milestone | Trigger | Action |
|---|---|---|
| **Second channel** | First channel hits ~5-10K monthly views consistently for 30 days with positive ROI (earnings > time cost). | Split: Horror vs True Crime. Reuse pipeline, new branding. Don’t cannibalize; target different sub-niches. |
| **Instagram Reels + TikTok cross-posting** | First channel stable, 10-20 Shorts live, Linkvertise converting. | Post vertical 9:16 to IG Reels, TikTok (if allowed). Adapt captions (hooks). Respect platform rules on true crime. Don’t upload same link aggressively; use link-in-bio or profile. |
| **Hire human editor** | MRR reaches **$150-$300/month** consistently for 2-3 months. | Outsource: thumbnail/visual polish, fact-checking, comment moderation. Start part-time ($20-$80/month) not full-time. |
| **Upgrade to paid Pollinations/Flux API** | Image fetch becomes bottleneck: >20 min per Short regularly, quota hits daily. | Upgrade Pollinations (if tier available) or use Flux via paid API to remove 402/quota. Only upgrade when time saved > cost. Current free tier is fine for 1/day. |

**Rule:** Scale channels first (free), upgrade infra only when bottleneck proven.

---

## 6. KPI DASHBOARD

### Daily metrics (5-10 min max)
| Metric | Target | Source |
|---|---|---|
| **Views** | +10-15% vs previous day (or >=500 if new) | YouTube Studio |
| **Retention (3-sec)** | >=70% | YouTube Studio (Shorts) |
| **CTR to Linkvertise** | >=1% (min), >=2% (good) | Linkvertise dashboard |
| **PDF downloads/magnet clicks** | Track trend | Linkvertise |
| **Paid conversions** | >=1% of magnet clicks | Gumroad/Cosmofeed |
| **Comments moderated** | 0 rule violations | Manual scan |
| **Saves/Shares** | Track (algorithm signal) | YouTube Studio |

### Weekly review checklist
- [ ] All 7 days posted (or target met)
- [ ] Top 3 performers identified by views+saves
- [ ] CTR >= target; if <1% after 20 Shorts → pivot (see red flags)
- [ ] Linkvertise earnings logged
- [ ] Paid sales logged
- [ ] Moderation clean (no Setagaya names)
- [ ] Next 7 Shorts planned/prompts ready
- [ ] Disk usage: /home free >=2GB, /mnt/mobiledrive free >=50GB

### Red flags (pivot needed)
| Flag | Threshold | Action |
|---|---|---|
| **CTR below 1%** | After 20 Shorts, average CTR <1% | Test new hook formula, rewrite description, try different sub-niche (Cold Cases over Serial Killers). |
| **3-sec retention <60%** | Consistent across 10+ Shorts | Shorten hook, front-load mystery, cut fluff. Test A/B hooks. |
| **Pollinations quota blocking daily cadence** | >2 failed attempts/Short, serialize >25 min/image fetch | Add stricter backoff, implement resume checkpoints, consider upgrading only if blocks 1/day. |
| **Linkvertise account issues** | Warnings/bans triggered | Switch to alternative (e.g., Linkvertise alternatives) immediately. Review ToS compliance. |
| **Ad-safe strikes** | Any copyright/claim | Audit visuals: ensure 100% AI-generated (Pollinations), no stock footage, no crime scene photos. Review description. |

---

## 7. RISK MITIGATION

| Risk | Mitigation |
|---|---|
| **YouTube copyright strikes on crime footage** | **Only use Pollinations-generated AI stills.** No movie clips, no news footage, no crime scene photos. Keep visuals abstract/moody (rooms, landscapes, objects) not identifiable victims. Don’t overlay copyrighted images. Existing pipeline uses AI-only; maintain this. |
| **Linkvertise account ban triggers** | Don’t misrepresent content. Link to free educational PDF, not pirated content. Don’t use deceptive redirects. Keep link in description/pinned comment (not on-screen buttons). Avoid spammy phrasing. Comply with Linkvertise ToS. Have backup redirect service ready. |
| **Pollinations endpoint down** | Implement fallback image source: (1) HuggingFace Inference API (free tier) with key, (2) Civitai free endpoints if available, (3) fal.ai free credits if obtained. Keep prompts cached so can retry later. Also consider local diffusers only if disk freed significantly. |
| **Backup upsell if Cosmofeed/Gumroad have payout issues** | Gumroad primary (reliable), Cosmofeed secondary. Backup: LemonSqueezy (if available), Stan Store, Thrivecart alternatives. Also Stripe-based simple checkout as last resort. Keep products exportable. |
| **Factual errors (reputation/legal)** | Enforce [DOCUMENTED]/[RECONSTRUCTED] labeling. Follow handling rules (esp. Setagaya no-names, Somerton attribution). Use public domain facts. Provenance notice in case files. Corrected 10 errors already logged. |
| **Hardware constraints** | Keep /home <90% (currently 92% — free 1GB more if possible). Force all heavy writes to /mnt/mobiledrive. Serialize Pollinations (no parallel). Use whisper.cpp (no torch). Avoid ComfyUI until disk freed. |

---

## 8. ZERO-BUDGET GROWTH HACKS

| Hack | Tactic | Ethics/compliance |
|---|---|---|
| **Reddit traffic siphoning** | Target: `r/UnresolvedMysteries`, `r/UnsolvedMysteries`, `r/truecrimemysteries` (check rules). Post case discussion (not link spam). Answer questions, link in profile/comments only if allowed. Focus on value. | Read subreddit rules first. Many forbid direct Short links in posts. Comment-based linking safer. Don’t brigade. |
| **Twitter/X thread format** | Create 3-5 tweet thread per case: hook tweet → facts → unanswered questions → link to magnet (last tweet). Use relevant hashtags (#truecrime #unsolved). | Avoid speculation. Cite facts. Don’t dox. Comply with X ToS. |
| **YouTube comment hijacking (ethical)** | Find big true crime channels with unsolved cases you covered. Comment factually: add detail, link to magnet only in profile if relevant, never spam. Focus on helping viewers. | Don’t post links in comments (often against rules). Use profile link. Be additive, not promotional. Avoid "watch my video" spam. |
| **Telegram public channel** | Create free Telegram channel "Unsolved Files" (or similar). Post case summaries, link to Shorts/magnet. Cross-promote from Shorts description via Linkvertise or profile. | No piracy, no graphic images. Keep factual. Comply with Telegram ToS. Focus on discussion. |

---

## 9. FINAL ANSWER: WHAT TO BUILD FIRST

Ranked by ROI (highest return, lowest effort). Do these in order.

| Rank | Step | Time | Tools needed | Expected outcome | Priority |
|---|---|---|---|---|---|
| **1** | **Disk layout + whisper.cpp build** | 15 min | bash, cmake, wget/curl, /mnt/mobiledrive | Free up I/O on primary disk. Get subtitle layer working (CPU-only). Enables all captioning. | **CRITICAL** |
| **2** | **Pollinations + MoviePy pipeline (serialize+retry)** | 15-20 min | Python, requests, moviepy, ffmpeg | End-to-end: script → images → Ken Burns MP4. Prove with existing assets. Core revenue engine. | **CRITICAL** |
| **3** | **Caption burn-in + QA** | 10 min | whisper.cpp, moviepy/ffmpeg | Add timed captions. Improves 3-sec retention. Test on short_1. | **HIGH** |
| **4** | **Magnet setup + Linkvertise** | 10-15 min | mdpdf + chromium, Linkvertise account | Day-1 monetization. Convert traffic before YPP. | **HIGH** |
| **5** | **Batch 7 Shorts + post daily (Week 2 start)** | 5-10 min active (batch runs bg) | Pipeline from 1-3 | Get traction. Generate first views, clicks. Validate CTR. | **HIGH** |

### Next 48 hours (concrete)
1. **Day 1 AM (15m):** Create `/mnt/mobiledrive/yt_auto_cache/`, `/mnt/mobiledrive/yt_auto_whisper/`. Build whisper.cpp base.en.
2. **Day 1 PM (20m):** Script `yt_auto_render.py` wrapper: loads script, calls Pollinations (serialize, retry on 402 with exponential backoff), MoviePy Ken Burns, whisper.cpp captions, ffmpeg mux.
3. **Day 2 AM (10m):** Render short_1 with captions, compare to existing.
4. **Day 2 PM (15m):** Create lead magnet PDF from case_1.md, set up Linkvertise, post first Short with real link.

**Do not build ComfyUI/local SD now.** Disk (4.1GB free) cannot fit torch+weights. Revisit only after freeing >=20GB or moving models entirely to /mnt/mobiledrive with aggressive pruning. The Pollinations + MoviePy + whisper.cpp stack fits current hardware perfectly.