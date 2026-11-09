"""Local state: credential and pending QR login, stored under the config dir.

The directory can be overridden with the BILI_CLI_HOME environment variable,
which is also how tests and multiple isolated profiles are supported.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from bilibili_api import Credential

from .errors import BiliError

CREDENTIAL_FIELDS = ("sessdata", "bili_jct", "buvid3", "buvid4", "dedeuserid", "ac_time_value")


def home() -> Path:
    env = os.environ.get("BILI_CLI_HOME")
    if env:
        path = Path(env)
    elif sys.platform == "win32":
        path = Path(os.environ.get("APPDATA", Path.home())) / "bili-cli"
    else:
        path = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "bili-cli"
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_home() -> Path:
    """Where small state files live: rate limits, the audit log and the seen list.

    Defaults to home(). BILI_CLI_STATE_DIR can point it at a directory the
    process may actually write to (a workspace inside a sandbox, for example)
    while credentials stay in home().
    """
    env = os.environ.get("BILI_CLI_STATE_DIR")
    return Path(env) if env else home()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_private(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if os.name == "posix":
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def credential_path() -> Path:
    return home() / "credential.json"


def pending_login_path() -> Path:
    return home() / "pending_login.json"


def save_credential(cred: Credential) -> None:
    if not (cred.sessdata and cred.bili_jct):
        raise BiliError("internal_error", "拒绝保存缺少 SESSDATA / bili_jct 的凭证。")
    data = {f: getattr(cred, f, None) for f in CREDENTIAL_FIELDS}
    _write_private(credential_path(), data)


def load_credential() -> Credential | None:
    data = _read_json(credential_path())
    if not data or not data.get("sessdata"):
        return None
    return Credential(**{f: data.get(f) for f in CREDENTIAL_FIELDS})


def require_credential() -> Credential:
    cred = load_credential()
    if cred is None:
        raise BiliError("not_logged_in", "尚未登录。")
    return cred


def my_mid(cred: Credential) -> int:
    """The logged-in UID. Credential files written by hand sometimes leave DedeUserID out."""
    if not cred.dedeuserid:
        raise BiliError(
            "auth_expired",
            "登录凭证里缺少 DedeUserID（你的 UID）。",
            hint="运行 `bili auth login` 重新扫码登录；手动导入凭证时要带上 dedeuserid。",
        )
    return int(cred.dedeuserid)


def optional_credential() -> Credential:
    """Credential if logged in, otherwise an anonymous one (more data is visible when logged in)."""
    return load_credential() or Credential()


def delete_credential() -> bool:
    path = credential_path()
    if path.exists():
        path.unlink()
        return True
    return False


def owner_path() -> Path:
    return home() / "owner.json"


def load_owner() -> dict[str, Any] | None:
    """The human this agent works for: who `--at owner` and `dm send owner` mean."""
    return _read_json(owner_path())


def save_owner(data: dict[str, Any]) -> None:
    _write_private(owner_path(), data)


def device_id() -> str:
    """A stable per-install device ID (B 站's web client generates one per browser)."""
    import uuid

    path = home() / "device.json"
    data = _read_json(path) or {}
    if not data.get("dev_id"):
        data["dev_id"] = str(uuid.uuid4()).upper()
        _write_private(path, data)
    return data["dev_id"]


def save_pending_login(data: dict[str, Any]) -> None:
    _write_private(pending_login_path(), data)


def load_pending_login() -> dict[str, Any] | None:
    return _read_json(pending_login_path())


def clear_pending_login() -> None:
    pending_login_path().unlink(missing_ok=True)
