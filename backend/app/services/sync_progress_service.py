from __future__ import annotations

import threading
from typing import Any

from backend.app.db.repositories.config import now_text


_SYNC_STATE_LOCK = threading.Lock()
_SYNC_STATE: dict[str, Any] = {
    "is_syncing": False,
    "cancel_requested": False,
    "progress": {
        "running": False,
        "started_at": None,
        "finished_at": None,
        "error": "",
        "summary": None,
        "items": {},
    },
}


def is_sync_running() -> bool:
    with _SYNC_STATE_LOCK:
        return bool(_SYNC_STATE["is_syncing"])


def set_sync_running(value: bool) -> None:
    with _SYNC_STATE_LOCK:
        _SYNC_STATE["is_syncing"] = value


def is_sync_cancel_requested() -> bool:
    with _SYNC_STATE_LOCK:
        return bool(_SYNC_STATE["cancel_requested"])


def request_sync_cancel() -> None:
    with _SYNC_STATE_LOCK:
        _SYNC_STATE["cancel_requested"] = True


def clear_sync_cancel_request() -> None:
    with _SYNC_STATE_LOCK:
        _SYNC_STATE["cancel_requested"] = False


def start_sync_progress(datasources: list[Any]) -> None:
    with _SYNC_STATE_LOCK:
        _SYNC_STATE["progress"] = {
            "running": True,
            "started_at": now_text(),
            "finished_at": None,
            "error": "",
            "summary": None,
            "items": {
                ds["source_key"]: {
                    "source_key": ds["source_key"],
                    "source_name": ds["source_name"],
                    "status": "pending",
                    "fetched": 0,
                    "total": None,
                    "page": 0,
                    "row_count": 0,
                    "message": "",
                    "quality_status": "",
                    "quality_summary": "",
                }
                for ds in datasources
            },
        }


def update_sync_progress_item(source_key: str, **kwargs: Any) -> None:
    with _SYNC_STATE_LOCK:
        item = _SYNC_STATE["progress"]["items"].setdefault(source_key, {"source_key": source_key})
        item.update(kwargs)


def finish_sync_progress(summary: dict[str, Any] | None = None, error: str = "") -> None:
    with _SYNC_STATE_LOCK:
        _SYNC_STATE["progress"]["running"] = False
        _SYNC_STATE["progress"]["finished_at"] = now_text()
        _SYNC_STATE["progress"]["summary"] = summary
        _SYNC_STATE["progress"]["error"] = error


def snapshot_sync_progress() -> dict[str, Any]:
    with _SYNC_STATE_LOCK:
        progress = _SYNC_STATE["progress"]
        items = list(progress["items"].values())
        result = None
        if not progress["running"]:
            result = {
                "summary": (progress["summary"] or {}).get("summary", {}),
                "error": progress["error"],
                "items": items,
            }
        return {
            "running": progress["running"],
            "started_at": progress["started_at"],
            "finished_at": progress["finished_at"],
            "error": progress["error"],
            "summary": progress["summary"],
            "items": items,
            "is_active": progress["running"],
            "result": result,
        }
