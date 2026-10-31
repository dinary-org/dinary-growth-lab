"""Live integration checks. Each check performs a real call and classifies the result:

WORKING      — an authenticated end-to-end call succeeded in this environment
BLOCKED      — a credential or network permission is missing (detail says which)
NOT TESTED   — the check could not run for reasons outside the integration
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable

from .config import Settings
from .http import Client, HttpError, NetworkBlocked
from .store import Store, new_id, now_iso


def _probe_host(url: str) -> tuple[bool, str]:
    try:
        r = Client("probe", max_retries=0, timeout=12).session.get(url, timeout=12)
        return True, f"reachable (HTTP {r.status_code})"
    except Exception as e:  # noqa: BLE001
        return False, f"unreachable: {type(e).__name__}"


def check_youtube(s: Settings) -> tuple[str, str]:
    if not s.youtube_api_key:
        ok, why = _probe_host("https://www.googleapis.com/youtube/v3/videos?part=id&id=x")
        return "BLOCKED", f"YOUTUBE_API_KEY not set; googleapis.com {why}"
    try:
        r = Client("youtube").get_json("https://www.googleapis.com/youtube/v3/videos", params={
            "part": "id", "chart": "mostPopular", "regionCode": s.region, "maxResults": 1, "key": s.youtube_api_key})
        return "WORKING", f"mostPopular returned {len(r.get('items', []))} item(s)"
    except HttpError as e:
        return "BLOCKED", f"API error {e.status}: {e.body[:160]}"
    except NetworkBlocked as e:
        return "BLOCKED", str(e)


def check_apify(s: Settings) -> tuple[str, str]:
    ok, why = _probe_host("https://api.apify.com/v2/acts")
    if not ok:
        return "BLOCKED", f"api.apify.com {why} (network policy); token {'set' if s.apify_token else 'not set'}"
    if not s.apify_token:
        return "BLOCKED", "APIFY_TOKEN not set (host reachable)"
    try:
        me = Client("apify").get_json("https://api.apify.com/v2/users/me", params={"token": s.apify_token})
        return "WORKING", f"authenticated as {me['data'].get('username')}"
    except HttpError as e:
        return "BLOCKED", f"API error {e.status}"


def check_supabase(s: Settings) -> tuple[str, str]:
    if not (s.supabase_url and s.supabase_key):
        return "BLOCKED", "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set (schema can still be applied via MCP)"
    try:
        Client("supabase", max_retries=1).request("GET", f"{s.supabase_url}/rest/v1/integration_checks", params={"limit": 1},
                                                   headers={"apikey": s.supabase_key, "Authorization": f"Bearer {s.supabase_key}",
                                                            "Accept-Profile": s.supabase_schema})
        return "WORKING", f"PostgREST read on {s.supabase_schema}.integration_checks ok"
    except (HttpError, NetworkBlocked) as e:
        return "BLOCKED", str(e)[:200]


def check_r2(s: Settings) -> tuple[str, str]:
    if not s.r2_account_id:
        ok, why = _probe_host("https://r2.cloudflarestorage.com")
        return "BLOCKED", f"R2 credentials not set; r2.cloudflarestorage.com {why}"
    try:
        from .sinks import R2Sink
        R2Sink(s).s3.head_bucket(Bucket=s.r2_bucket)
        return "WORKING", f"bucket {s.r2_bucket} reachable"
    except Exception as e:  # noqa: BLE001
        return "BLOCKED", f"{type(e).__name__}: {str(e)[:160]}"


def check_runway(s: Settings) -> tuple[str, str]:
    ok, why = _probe_host("https://api.dev.runwayml.com/v1/organization")
    if not s.runway_secret:
        return "BLOCKED", f"RUNWAYML_API_SECRET not set; api.dev.runwayml.com {why}"
    return ("NOT TESTED", "secret set; generation not exercised to avoid spend") if ok else ("BLOCKED", why)


def check_anthropic(s: Settings) -> tuple[str, str]:
    if not s.anthropic_api_key:
        return "BLOCKED", "ANTHROPIC_API_KEY not set — concepts are written in-session via the generate-concepts skill"
    return "NOT TESTED", "key set; run `dgl concepts --llm` to exercise"


def check_ffmpeg(_: Settings) -> tuple[str, str]:
    if not shutil.which("ffmpeg"):
        return "BLOCKED", "ffmpeg not installed"
    v = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.split("\n")[0]
    return "WORKING", v


def check_tesseract(_: Settings) -> tuple[str, str]:
    if not shutil.which("tesseract"):
        return "BLOCKED", "tesseract not installed (apt-get install tesseract-ocr tesseract-ocr-kor)"
    langs = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout
    return ("WORKING", "kor+eng available") if "kor" in langs else ("BLOCKED", "Korean traineddata missing")


def check_transcription(_: Settings) -> tuple[str, str]:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        ok, why = _probe_host("https://huggingface.co")
        return "BLOCKED", f"faster-whisper not installed; model host huggingface.co {why}. Sidecar SRT path works."
    return "NOT TESTED", "faster-whisper installed; model download not attempted"


def check_tts(_: Settings) -> tuple[str, str]:
    if not shutil.which("espeak-ng"):
        return "BLOCKED", "espeak-ng not installed"
    return "WORKING", "espeak-ng Korean voice (placeholder quality)"


def check_remotion(s: Settings) -> tuple[str, str]:
    root = Path(__file__).resolve().parents[2] / "render"
    if not (root / "node_modules" / "remotion").exists():
        return "BLOCKED", "run `npm ci` in render/"
    if s.remotion_browser and not Path(s.remotion_browser).exists():
        return "BLOCKED", f"browser not found at {s.remotion_browser}"
    return "NOT TESTED", "installed; run `dgl render` for the end-to-end test"


def check_watch_skill(_: Settings) -> tuple[str, str]:
    ok, why = _probe_host("https://www.youtube.com")
    return ("BLOCKED", f"bradautomates/claude-video fetches media from platform URLs via yt-dlp; www.youtube.com {why}. "
            "Local-file analysis is covered by dgl.analysis.media.") if not ok else (
            "NOT TESTED", "platform host reachable; plugin install not attempted (plugins are not loaded in cloud sessions)")


CHECKS: dict[str, Callable[[Settings], tuple[str, str]]] = {
    "youtube_data_api_v3": check_youtube, "apify_instagram": check_apify, "supabase_rest": check_supabase,
    "cloudflare_r2": check_r2, "runway": check_runway, "claude_api": check_anthropic, "ffmpeg": check_ffmpeg,
    "tesseract_ocr": check_tesseract, "transcription": check_transcription, "tts_espeak": check_tts,
    "remotion": check_remotion, "watch_skill": check_watch_skill,
}


def run_checks(s: Settings, store: Store | None = None) -> dict[str, dict[str, str]]:
    out = {}
    for name, fn in CHECKS.items():
        try:
            status, detail = fn(s)
        except Exception as e:  # noqa: BLE001
            status, detail = "NOT TESTED", f"check crashed: {type(e).__name__}: {e}"
        out[name] = {"status": status, "detail": detail, "checked_at": now_iso()}
        if store:
            store.upsert("integration_checks", {"check_id": new_id("chk"), "integration": name, "status": status,
                                                "checked_at": out[name]["checked_at"], "detail": detail})
    return out
