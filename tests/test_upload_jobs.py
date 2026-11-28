"""Background job store: round-trip, single-upload lock, duplicate protection."""

import json
import os
import time

import pytest

from bili_cli import config, upload_jobs


@pytest.fixture()
def state(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "state_home", lambda: tmp_path)
    return tmp_path


def _job(**overrides):
    job = {
        "id": upload_jobs.new_id(),
        "status": "running",
        "title": "测试",
        "fingerprint": "fp-1",
        "pid": os.getpid(),
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    job.update(overrides)
    return job


def test_job_roundtrip(state):
    job = _job()
    upload_jobs.write(job)
    assert upload_jobs.read(job["id"])["title"] == "测试"
    assert [j["id"] for j in upload_jobs.all_jobs()] == [job["id"]]


def test_read_missing_job(state):
    with pytest.raises(FileNotFoundError):
        upload_jobs.read("nope")


def test_fingerprint_changes_with_title_or_file(state, tmp_path):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x" * 10)
    first = upload_jobs.fingerprint([video], "标题 A")
    assert first == upload_jobs.fingerprint([video], "标题 A")
    assert first != upload_jobs.fingerprint([video], "标题 B")


def test_conflict_on_running_job(state):
    job = _job(status="running", pid=os.getpid())
    upload_jobs.write(job)
    found = upload_jobs.find_conflict("fp-1", force=False)
    assert found and found["reason"] == "running"


def test_conflict_on_recent_success(state):
    job = _job(status="submitted", pid=1, finished_at=time.time() - 60)
    upload_jobs.write(job)
    found = upload_jobs.find_conflict("fp-1", force=False)
    assert found and found["reason"] == "recent"


def test_old_success_does_not_block(state):
    job = _job(status="submitted", pid=1, finished_at=time.time() - upload_jobs.SUCCESS_DEDUPE_SECONDS - 10)
    upload_jobs.write(job)
    assert upload_jobs.find_conflict("fp-1", force=False) is None


def test_force_bypasses_everything(state):
    upload_jobs.write(_job(status="running"))
    assert upload_jobs.find_conflict("fp-1", force=True) is None


def test_lock_refuses_a_live_owner_and_releases(state):
    other = _job(status="running", pid=os.getpid())
    upload_jobs.write(other)

    with pytest.raises(RuntimeError) as err:
        upload_jobs.acquire_lock("someone-else")
    assert "已有上传任务在跑" in str(err.value)
    assert not (state / "jobs" / ".lock").exists()      # 被拒绝时不留锁

    # 同一个任务重新拿锁是允许的（子进程重启的场景）
    upload_jobs.acquire_lock(other["id"])
    assert json.loads((state / "jobs" / ".lock").read_text(encoding="utf-8"))["id"] == other["id"]

    upload_jobs.release_lock("someone-else")            # id 不匹配，不解别人的锁
    assert (state / "jobs" / ".lock").exists()

    upload_jobs.release_lock(other["id"])
    assert not (state / "jobs" / ".lock").exists()
