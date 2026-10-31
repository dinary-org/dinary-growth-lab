"""Job runner: logging, retries, idempotent job records, cost accounting, durable sync.

Every scheduled step runs through `run_job`, which records start/finish,
attempt count, cost counters and the error (if any) in `job_runs`, and drains
the sync queue to Supabase at the end when credentials exist — so a session
that dies mid-run loses nothing that was already acknowledged.
"""
from __future__ import annotations

import json
import logging
import time
import traceback
from typing import Any, Callable

from ..config import Settings
from ..store import Store, new_id, now_iso

log = logging.getLogger("dgl.jobs")


class BudgetExceeded(RuntimeError):
    pass


def run_job(store: Store, s: Settings, name: str, fn: Callable[[], dict[str, Any]], retries: int = 2,
            retry_on: tuple[type[BaseException], ...] = (ConnectionError, TimeoutError)) -> dict[str, Any]:
    job_id = new_id(f"job_{name}")
    started = now_iso()
    attempt, result, status, err = 0, None, "failed", None
    while attempt <= retries:
        attempt += 1
        try:
            result = fn()
            status = "ok"
            break
        except retry_on as e:
            err = f"{type(e).__name__}: {e}"
            log.warning("%s attempt %d failed: %s", name, attempt, err)
            time.sleep(min(2 ** attempt, 30))
        except Exception as e:  # noqa: BLE001 - non-retryable: record and stop
            err = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=4)}"
            break
    synced = None
    if s.supabase_url and s.supabase_key:
        try:
            from ..sinks import SupabaseSink
            synced = SupabaseSink(s).drain(store)
        except Exception as e:  # noqa: BLE001 - keep queue for the next run
            err = (err or "") + f"\nsync failed (rows kept queued): {e}"
    cost = (result or {}).get("cost") or {k: (result or {}).get(k) for k in ("quota_used",) if (result or {}).get(k)}
    store.upsert("job_runs", {"job_id": job_id, "job": name, "started_at": started, "finished_at": now_iso(),
                              "status": status, "attempts": attempt, "cost": cost,
                              "log": json.dumps({"error": err, "synced_rows": synced}, ensure_ascii=False)})
    return {"job_id": job_id, "status": status, "attempts": attempt, "error": err, "result": result, "synced": synced}
