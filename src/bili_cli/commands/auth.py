"""bili auth: two-step QR login designed for agents.

`login` only produces the QR code and returns immediately, because an agent
cannot scan it. A human scans it, then `status --wait` collects the result.
"""

from __future__ import annotations

import asyncio
import io
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import httpx
import qrcode
import typer
from bilibili_api import Credential, user
from bilibili_api.exceptions import CookiesRefreshException
from bilibili_api.utils.network import Api

from .. import config, output
from ..errors import BiliError
from ..runtime import WEB_HEADERS as HEADERS, http_client, run

app = typer.Typer(help="登录、登录状态与登出。", no_args_is_help=True)

QR_TTL_SECONDS = 180
POLL_INTERVAL_SECONDS = 2

# The web QR login is implemented here instead of via bilibili_api.login_v2:
# Bilibili now delivers the login cookies only as Set-Cookie headers, while
# bilibili-api still parses them from the redirect URL and ends up empty.
QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate?source=main-fe-header"
QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"

QR_NOT_SCANNED = 86101
QR_SCANNED = 86090
QR_EXPIRED = 86038

# Cookie refresh: bilibili-api stamps correspondPath with this machine's clock, and Bilibili
# answers 404 when the stamp is in its future, so a clock a few seconds fast broke every refresh.
# cookie/info returns Bilibili's own time, which is what the stamp should use.
COOKIE_INFO_URL = "https://passport.bilibili.com/x/passport-login/web/cookie/info"
CORRESPOND_KEY = """\
-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDLgd2OAkcGVtoE3ThUREbio0Eg
Uc/prcajMKXvkCKFCWhJYJcLkcM2DKKcSeFpD/j6Boy538YXnR6VhcuUJOhH2x71
nzPjfdTcqMz7djHum0qSZA0AyCBDABUqCrfNgCiJ00Ra7GmRj+YCK1NJEuewlb40
JNrRuoEUXpabUzGB8QIDAQAB
-----END PUBLIC KEY-----"""


async def _generate_qr() -> tuple[str, str]:
    async with http_client(headers=HEADERS, timeout=15) as client:
        resp = await client.get(QR_GENERATE_URL)
        resp.raise_for_status()
        body = resp.json()
    if body.get("code") != 0:
        raise BiliError("api_error", f"获取二维码失败：{body.get('message')}", detail={"bili_code": body.get("code")})
    return body["data"]["url"], body["data"]["qrcode_key"]


async def _poll_qr(key: str) -> tuple[int, Credential | None]:
    async with http_client(headers=HEADERS, timeout=15) as client:
        resp = await client.get(QR_POLL_URL, params={"qrcode_key": key})
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        cookies = {c.name: c.value for c in client.cookies.jar}
    code = data.get("code")
    if code != 0:
        return code, None
    # Older responses carried the cookies in the redirect URL; keep that as a fallback.
    query = {k: v[0] for k, v in parse_qs(urlparse(data.get("url") or "").query).items()}
    pick = lambda name: cookies.get(name) or query.get(name)  # noqa: E731
    cred = Credential(
        sessdata=pick("SESSDATA"),
        bili_jct=pick("bili_jct"),
        dedeuserid=pick("DedeUserID"),
        buvid3=cookies.get("buvid3"),
        buvid4=cookies.get("buvid4"),
        ac_time_value=data.get("refresh_token"),
    )
    if not (cred.sessdata and cred.bili_jct):
        missing = [n for n in ("SESSDATA", "bili_jct") if not pick(n)]
        raise BiliError(
            "api_error",
            "扫码成功，但没有从响应中拿到登录 Cookie，B 站登录接口可能又改了。",
            hint="请把这条输出提交到 issue，并重新运行 `bili auth login`。",
            detail={"missing": missing, "cookie_names": sorted(cookies), "url_params": sorted(query)},
        )
    return code, cred


async def _self_info(cred) -> dict:
    me = await user.get_self_info(cred)
    return {"mid": me.get("mid"), "name": me.get("name"), "level": me.get("level")}


def _correspond_path(timestamp_ms: int) -> str:
    from Cryptodome.Cipher import PKCS1_OAEP
    from Cryptodome.Hash import SHA256
    from Cryptodome.PublicKey import RSA

    return PKCS1_OAEP.new(RSA.importKey(CORRESPOND_KEY), SHA256).encrypt(f"refresh_{timestamp_ms}".encode()).hex()


async def _refresh_cookies(cred: Credential) -> bool:
    """Refresh the login cookies when Bilibili asks for it; returns whether they were refreshed."""
    from bilibili_api.utils import network

    info = await Api(url=COOKIE_INFO_URL, method="GET", credential=cred).result or {}
    if not info.get("refresh"):
        return False
    offset = info["timestamp"] / 1000 - time.time() if info.get("timestamp") else 0.0
    original = network._getCorrespondPath
    network._getCorrespondPath = lambda: _correspond_path(round((time.time() + offset) * 1000))
    try:
        await cred.refresh()
    finally:
        network._getCorrespondPath = original
    return True


async def _wait_pending(pending: dict, wait: int) -> dict:
    deadline = time.monotonic() + max(wait, 0)
    while True:
        if time.time() > pending["created_at"] + QR_TTL_SECONDS:
            config.clear_pending_login()
            raise BiliError("login_expired", "二维码已过期。")
        try:
            code, cred = await _poll_qr(pending["qrcode_key"])
        except BiliError:
            config.clear_pending_login()
            raise
        if cred is not None:
            config.save_credential(cred)
            config.clear_pending_login()
            return {"logged_in": True, "user": await _self_info(cred)}
        if code == QR_EXPIRED:
            config.clear_pending_login()
            raise BiliError("login_expired", "二维码已过期。")
        if code not in (QR_NOT_SCANNED, QR_SCANNED):
            raise BiliError("api_error", f"未知的扫码状态 {code}。", detail={"bili_code": code})
        if time.monotonic() >= deadline:
            scanned = code == QR_SCANNED
            raise BiliError(
                "login_pending",
                "已扫码，等待在手机上确认登录。" if scanned else "二维码尚未被扫描。",
                detail={"scanned": scanned, "qr_url": pending["url"], "qr_image": pending["qr_image"]},
            )
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def _wait_deadline(wait: int) -> float | None:
    """--wait blocks on purpose; the overall limit must leave room for it."""
    from ..runtime import default_deadline

    limit = default_deadline()
    return None if limit is None else max(limit, wait + 120)


@app.command()
def login(
    qr_out: Optional[Path] = typer.Option(
        None, "--qr-out", help="二维码 PNG 保存路径，默认保存在配置目录。"
    ),
    wait: int = typer.Option(
        0, "--wait", help="生成二维码后阻塞等待扫码的秒数；0 表示立即返回（适合 Agent）。"
    ),
) -> None:
    """生成登录二维码。Agent 应把二维码图片或链接交给人类扫码，再运行 `bili auth status --wait 120`。"""

    async def body():
        url, key = await _generate_qr()

        path = (qr_out or config.home() / "login_qrcode.png").resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        qrcode.make(url).save(path)

        pending = {"qrcode_key": key, "url": url, "qr_image": str(path), "created_at": int(time.time())}
        config.save_pending_login(pending)

        if sys.stderr.isatty():
            qr = qrcode.QRCode(border=1)
            qr.add_data(url)
            art = io.StringIO()
            qr.print_ascii(out=art, invert=True)
            output.info(art.getvalue())
            output.info(f"请用哔哩哔哩 App 扫码登录（{QR_TTL_SECONDS} 秒内有效）。二维码图片：{path}")

        if wait > 0:
            return await _wait_pending(pending, wait)
        return {
            "logged_in": False,
            "status": "pending",
            "qr_url": url,
            "qr_image": str(path),
            "expires_in": QR_TTL_SECONDS,
            "next": "请人类用哔哩哔哩 App 扫描二维码，然后运行 `bili auth status --wait 120`。",
        }

    run(body, deadline=_wait_deadline(wait))


@app.command()
def status(
    wait: int = typer.Option(
        0, "--wait", help="存在待完成的扫码登录时，最多等待的秒数。"
    ),
) -> None:
    """查看登录状态；如果有进行中的扫码登录，则检查（并可等待）扫码结果。"""

    async def body():
        pending = config.load_pending_login()
        if pending:
            return await _wait_pending(pending, wait)

        cred = config.load_credential()
        if cred is None:
            raise BiliError("not_logged_in", "尚未登录。")
        if not await cred.check_valid():
            raise BiliError("auth_expired", "登录凭证已失效。")
        refreshed, refresh_error = False, None
        if cred.has_ac_time_value():
            try:
                refreshed = await _refresh_cookies(cred)
            except CookiesRefreshException as exc:
                # the login was just checked and still works; only renewing it failed
                refresh_error = str(exc)
        if refreshed:
            config.save_credential(cred)
        result = {"logged_in": True, "user": await _self_info(cred), "refreshed": refreshed}
        if refresh_error:
            result["refresh_error"] = refresh_error
            result["hint"] = "登录目前仍有效，只是这次没能续期。稍后再运行 `bili auth status`；一直失败的话，等登录过期后用 `bili auth login` 重新扫码。"
        return result

    run(body, deadline=_wait_deadline(wait))


@app.command()
def logout() -> None:
    """删除本地保存的登录凭证（不会使服务端会话失效）。"""

    async def body():
        config.clear_pending_login()
        return {"logged_in": False, "removed": config.delete_credential()}

    run(body)
