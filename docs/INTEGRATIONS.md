# Integrations — status and setup

Status was checked live on **2026-10-31** in a Claude Code Cloud session (`dgl status`; raw output
in `outputs/integration_status.json`). Each status means:

- **WORKING**: a real end-to-end call or operation succeeded in this environment.
- **BLOCKED**: a credential or network permission is missing. The exact item is named below.
- **NOT TESTED**: the check could not run, or running it would spend money.

Installing a package never counts as WORKING.

| Integration | Status | What was observed | What is needed |
|---|---|---|---|
| YouTube Data API v3 | **BLOCKED** | `www.googleapis.com` is reachable. An unauthenticated call returns `403 Method doesn't allow unregistered callers`. | `YOUTUBE_API_KEY` env var (Google Cloud → enable *YouTube Data API v3* → API key, restricted to that API). No network change is needed. |
| Apify (Instagram Reels metadata) | **BLOCKED** | The proxy refuses to open a tunnel to `api.apify.com` (policy denial). | Allow `api.apify.com` in the environment's network settings, and set `APIFY_TOKEN`. |
| Instagram direct | **BLOCKED** | `www.instagram.com` is denied by the network policy. | Not used for collection; we never scrape logged-in Instagram. Own-account insights use the Graph API (`graph.facebook.com` must be allowed, plus `INSTAGRAM_GRAPH_TOKEN`). |
| Watch skill (`bradautomates/claude-video`) | **BLOCKED** | README read from GitHub. It fetches media from platform URLs (yt-dlp), and `www.youtube.com` / `*.googlevideo.com` are denied. Its README also says it targets local Claude Code installs, not chat or Cowork. | Its local-file path is reproduced cloud-natively in `dgl.analysis.media` (frames, cuts, OCR, loudness, transcript). Platform media download also needs creator or platform permission. |
| FFmpeg 6.1 | **WORKING** | Used for probing, scene detection, frame extraction, EBU R128 loudness, black/freeze detection and audio synthesis. | — |
| Tesseract OCR (kor+eng) | **WORKING** | Read 6/6 sampled Korean caption frames in QC. | `apt-get install tesseract-ocr tesseract-ocr-kor` (in `scripts/setup.sh`). |
| Transcription (faster-whisper) | **BLOCKED** | Model weights are hosted on `huggingface.co` (denied); the OpenAI Whisper weights host is also unreachable. | Allow `huggingface.co` and install `.[transcribe]`, **or** set `OPENAI_API_KEY`/`GROQ_API_KEY` and allow that API host. Creator-supplied captions (SRT) already work. |
| TTS (espeak-ng, ko) | **WORKING** | Generated the narration of the demo video. | Placeholder quality only. Use a licensed natural Korean voice before publishing. |
| Remotion 4.0.539 | **WORKING** | Rendered the 24 s 1080×1920 demo with the image's bundled `chrome-headless-shell`. | `REMOTION_BROWSER_EXECUTABLE` (set by `scripts/setup.sh`). **Licence:** Remotion needs a paid company licence for for-profit companies with more than 3 people. Check DINARY's headcount. |
| Supabase | **BLOCKED** (pipeline) / connector available | The session has a Supabase connector, but its only project is the production app `dinary-app` (ap-northeast-2). The migration was **not applied** without owner approval. The REST host is denied by the network policy. | Choose: apply `supabase/migrations/0001_growth_lab_init.sql` to `dinary-app` (isolated `growth_lab` schema, RLS on, service-role only) **or** create a dedicated project. Then set `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`, expose the `growth_lab` schema in API settings, and allow `<ref>.supabase.co`. |
| Cloudflare R2 | **BLOCKED** | `*.r2.cloudflarestorage.com` is denied; no credentials. | Allow `<account>.r2.cloudflarestorage.com`; set `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`. |
| Runway | **BLOCKED** | `api.dev.runwayml.com` is denied; no secret. | Optional. Allow the host and set `RUNWAYML_API_SECRET`. Not needed for the current concepts. |
| Claude API (unattended concepts) | **BLOCKED** | `ANTHROPIC_API_KEY` is not set. | Optional: interactive sessions write concepts through the `generate-concepts` skill. For unattended runs set the key (code uses `claude-opus-5-5` with structured outputs). |
| Canva connector | not used | The MCP server failed to connect (proxy 403). | — |

## How each integration is used (cloud-first)

- **Direct HTTPS APIs only.** We use the YouTube Data API, the Apify REST API, Supabase PostgREST,
  the R2 S3 API and the Graph APIs. Calls go through `dgl.http.Client`, which provides retries,
  exponential backoff, a rate limit, and a distinction between network blocks and HTTP errors.
  No local CLI plugins are required.
- **Skills** in `.claude/skills/` drive the judgement steps (interpretation, labels, concepts). This
  works in any Claude Code session, including cloud and scheduled sessions.
- **Secrets** come only from environment variables (cloud environment settings, or GitHub Actions
  secrets). `.env` is git-ignored, and `.env.example` documents every variable.

## Cost controls

| Resource | Control |
|---|---|
| YouTube quota | `QuotaBudget` refuses a call *before* exceeding `YOUTUBE_DAILY_QUOTA` (search=100 units, others=1). A default discovery run uses ~6 searches + ~8 hydrations + creator baselines ≈ 700–900 units. A snapshot of 500 videos costs ≈ 10 units. |
| Apify | `maxTotalChargeUsd` is passed on every actor run (`APIFY_MAX_USD_PER_RUN`, default $2). |
| Claude API | One structured-output request per concept, with an explicit `effort`. |
| Rendering | Local only (Remotion + FFmpeg); no per-render API cost. |
