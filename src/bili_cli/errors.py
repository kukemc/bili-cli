"""Error model shared by every command.

Every failure is turned into a BiliError with a stable string code, a process
exit code and a hint that tells an agent what to do next.
"""

from __future__ import annotations

from typing import Any

# code -> (exit code, default hint)
ERROR_TABLE: dict[str, tuple[int, str | None]] = {
    "internal_error": (1, "这是 bili-cli 的 bug，请把完整输出提交到 issue。"),
    "api_error": (1, None),
    "invalid_argument": (2, "运行 `bili <命令> --help` 或 `bili schema` 查看参数。"),
    "not_logged_in": (3, "运行 `bili auth login` 获取二维码，请人类扫码后运行 `bili auth status --wait 120`。"),
    "auth_expired": (3, "凭证已失效，运行 `bili auth login` 重新登录。"),
    "not_found": (4, "检查 BV 号 / 评论 ID 是否正确，资源可能已被删除。"),
    "risk_control": (5, "被 B 站风控拦截，请降低操作频率，稍后再试。"),
    "captcha_required": (6, "该操作需要人工完成验证码，请人类在浏览器中完成一次相同操作后再试。"),
    "rate_limited": (7, "操作过于频繁，请等待一段时间后重试。"),
    "network_error": (8, "网络错误，检查网络连接或代理后重试。"),
    "login_pending": (9, "二维码尚未被扫描确认，请人类扫码后再运行 `bili auth status --wait 120`。"),
    "login_expired": (10, "二维码已过期，运行 `bili auth login` 重新获取。"),
}

# Bilibili response codes -> our error codes
BILI_CODE_MAP: dict[int, str] = {
    -101: "not_logged_in",
    -111: "auth_expired",  # csrf 校验失败
    -400: "invalid_argument",
    -403: "risk_control",
    -404: "not_found",
    -352: "risk_control",
    -412: "risk_control",
    -509: "rate_limited",
    -799: "rate_limited",
    12002: "not_found",  # 评论区已关闭
    12006: "not_found",  # 没有该评论
    12014: "rate_limited",  # 已达到当前评论发送上限（按账号，B 站动态调整）
    12015: "captcha_required",
    12016: "invalid_argument",  # 评论含敏感内容
    12035: "invalid_argument",  # 被 UP 主拉黑，无法评论
    12051: "risk_control",  # 重复评论 / 被判定为刷屏，必须停下
    21007: "invalid_argument",  # 私信内容过长
    34002: "invalid_argument",  # UP 主不能给自己投币
    34005: "invalid_argument",  # 该视频投币已达上限
    12022: "not_found",  # 评论已被删除
    62002: "not_found",  # 稿件不可见
    62004: "not_found",  # 稿件审核中
    62012: "not_found",  # 仅 UP 主自己可见
    36703: "rate_limited",  # 发送弹幕的频率过快
    79046: "invalid_argument",  # 互动组件存在时间冲突
    79037: "invalid_argument",  # 关注弹幕坐标溢出
    79034: "invalid_argument",  # 视频时长过短，或发送时剩余时长过短
}

# hints for codes whose generic hint would send the agent the wrong way
BILI_CODE_HINTS: dict[int, str] = {
    12014: "账号的评论额度用完了（等级低、短时间发得多的账号额度更小）。至少停 1 小时再发，不要每隔几分钟试一次；之后放慢、均匀地发。",
}


class BiliError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        hint: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        if code not in ERROR_TABLE:
            code = "internal_error"
        self.code = code
        self.message = message
        self.hint = hint if hint is not None else ERROR_TABLE[code][1]
        self.detail = detail or {}

    @property
    def exit_code(self) -> int:
        return ERROR_TABLE[self.code][0]

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        if self.detail:
            d["detail"] = self.detail
        return d


def from_exception(exc: BaseException) -> BiliError:
    """Translate any exception raised by bilibili-api (or us) into a BiliError."""
    if isinstance(exc, BiliError):
        return exc

    from bilibili_api import exceptions as bex

    if isinstance(exc, bex.ResponseCodeException):
        code = BILI_CODE_MAP.get(exc.code, "api_error")
        return BiliError(code, f"B 站返回错误 {exc.code}：{exc.msg}", hint=BILI_CODE_HINTS.get(exc.code), detail={"bili_code": exc.code})
    if isinstance(
        exc,
        (
            bex.CredentialNoSessdataException,
            bex.CredentialNoBiliJctException,
            bex.CredentialNoDedeUserIDException,
        ),
    ):
        return BiliError("not_logged_in", "该操作需要登录。")
    if isinstance(exc, bex.ArgsException):
        return BiliError("invalid_argument", str(exc))
    if isinstance(exc, bex.NetworkException):
        status = getattr(exc, "status", None)
        if status == 412:
            return BiliError("risk_control", "请求被 B 站风控拦截（HTTP 412）。")
        return BiliError("network_error", str(exc))
    if isinstance(exc, bex.WbiRetryTimesExceedException):
        return BiliError("risk_control", "wbi 签名重试次数超限。")
    if isinstance(exc, bex.ResponseException):
        return BiliError("api_error", str(exc))

    import httpx

    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 412:
        return BiliError("risk_control", "请求被 B 站风控拦截（HTTP 412）。", detail={"url": str(exc.request.url.copy_with(query=None))})
    # curl_cffi (used by bilibili-api) raises its own Timeout / ConnectionError types
    if isinstance(exc, (httpx.HTTPError, ConnectionError, TimeoutError)) or type(exc).__module__.startswith("curl_cffi"):
        message = f"{type(exc).__name__}: {exc}"
        lowered = message.lower()
        if "certificate" in lowered or "curl: (60)" in lowered or "ssl" in lowered:
            return BiliError(
                "network_error",
                message,
                hint=(
                    "证书校验失败：这台机器上可能有杀软在扫描 HTTPS，或者这条线路只下发了叶子证书。"
                    "把缺的证书导出成 PEM，设 BILI_CLI_CA_BUNDLE 指向它（bili-cli 会和 certifi 合并，校验不会被关闭），"
                    "详见 README 的「常见问题」。不要用关闭校验的方式绕开。"
                ),
            )
        return BiliError("network_error", message)

    return BiliError("internal_error", f"{type(exc).__name__}: {exc}")
