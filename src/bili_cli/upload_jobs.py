"""Background upload jobs: durable state on disk so a task survives MCP timeouts and restarts.

One job per running upload, written to `<state>/jobs/<id>.json` and refreshed as the upload moves
between lines. A pid lock keeps two uploads from racing, and an index of finished jobs blocks the
same file + title from being submitted twice.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from . import config

SUCCESS_DEDUPE_SECONDS = 24 * 3600
RUNNING_STALE_SECONDS = 6 * 3600


def jobs_dir() -> Path:
    path = config.state_home() / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_path(job_id: str) -> Path:
    return jobs_dir() / f"{job_id}.json"


def new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def write(job: dict[str, Any]) -> None:
    path = job_path(job["id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def read(job_id: str) -> dict[str, Any]:
    path = job_path(job_id)
    if not path.is_file():
        raise FileNotFoundError(job_id)
    return json.loads(path.read_text(encoding="utf-8"))


def all_jobs() -> list[dict[str, Any]]:
    jobs = []
    for path in sorted(jobs_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            jobs.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return jobs


def _lock_path() -> Path:
    return jobs_dir() / ".lock"


def pid_alive(pid: int) -> bool:
    """Is this process still running? On Windows os.kill(pid, 0) would terminate it, so use the API."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_lock(job_id: str) -> None:
    """Refuse to start when another upload is running; break locks whose owner died."""
    path = _lock_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = None
    if raw and pid_alive(int(raw.get("pid") or 0)) and raw.get("id") != job_id:
        raise RuntimeError(
            f"已有上传任务在跑：{raw.get('id')}（pid {raw.get('pid')}）。"
            f"用 `bili upload job {raw.get('id')}` 查看，确认要放弃再删掉 {path}。"
        )
    for job in all_jobs():
        if job.get("id") == job_id or job.get("status") not in {"queued", "running"}:
            continue
        if pid_alive(int(job.get("pid") or 0)):
            raise RuntimeError(
                f"已有上传任务在跑：{job.get('id')}（pid {job.get('pid')}）。"
                f"用 `bili upload job {job.get('id')}` 查看，确认要放弃再删掉 {job_path(job['id'])}。"
            )
    path.write_text(json.dumps({"id": job_id, "pid": os.getpid(), "at": time.time()}), encoding="utf-8")


def release_lock(job_id: str) -> None:
    path = _lock_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("id") == job_id:
            path.unlink()
    except (OSError, json.JSONDecodeError):
        pass


def fingerprint(paths: list[Path], title: str) -> str:
    parts = [str(p.resolve()).lower() for p in paths]
    sizes = [p.stat().st_size for p in paths if p.exists()]
    return "|".join([*parts, str(sizes), title.strip()])


def find_conflict(fingerprint_value: str, *, force: bool) -> dict[str, Any] | None:
    """A running job for the same input, or a success within the dedupe window."""
    if force:
        return None
    now = time.time()
    for job in all_jobs():
        if job.get("fingerprint") != fingerprint_value:
            continue
        if job.get("status") in {"running", "queued"} and pid_alive(int(job.get("pid") or 0)):
            return {"reason": "running", "job": job}
        if job.get("status") == "submitted" and now - float(job.get("finished_at") or 0) < SUCCESS_DEDUPE_SECONDS:
            return {"reason": "recent", "job": job}
    return None
