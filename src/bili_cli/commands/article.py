"""bili article: write a 专栏 (column article) — draft, upload images, publish, edit, delete, read.

The creative-center article API is not wrapped by `bilibili-api`, so these are the same requests the
web editor makes. Notes that cost us a few rounds of debugging:

* The category field is **`category`**, not `category_id` — the latter is silently stored as
  `template_id`, and the article then shows up with no category.
* `submit` validates field by field and rejects a bare `aid`: `title`, `content`, `category`,
  `reprint` must all be present, or you get 20022 / 20023 / 20027 / 20028 in that order. With no
  category it always fails with 20022, so `publish` refuses early instead of letting B站 report it.
* Uploading an image wants a **`data:` URI**, not raw base64 (`-400` 请求错误 otherwise).
* Editing a published article goes through `article/update`; `submit` would create a new one.
  `update` re-enters review, so the article is briefly `in_audit`: the **public** `x/article/view`
  answers 404 while that lasts, but the **author-side** `x/article/creative/article/view` keeps
  working — which is why every read here goes through the author-side endpoints.
* `x/article/creative/draft/view` only serves drafts; on a published article it answers
  `47030 该草稿已失效`, so the two reads are not interchangeable.
* `<pre>` / `<code>` are not in the editor's tag whitelist and get demoted to plain text, which
  destroys a code block's line breaks *and* its alignment (runs of spaces collapse). Render code as
  an image instead — see `ALLOWED_TAGS`.
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

import typer
from bilibili_api.utils.network import Api

from .. import config, guard, output
from ..errors import BiliError
from ..runtime import iso_time, run

app = typer.Typer(help="专栏：建草稿、传图、发布、编辑、删除、读取正文。", no_args_is_help=True)

BASE = "https://api.bilibili.com/x/article/creative"
CATEGORIES = "https://api.bilibili.com/x/article/categories"
# Preview links come in two shapes: `/read/preview/53767611` (no `c` after `preview`) and
# `/read/cv53767611`. Both branches are captured and whichever is set wins — reading only
# group(1) produced int(None) for the preview shape, and submit had already gone through by
# then, so the command reported an error for an article that was already published.
ARTICLE_ID_RE = re.compile(r"/read/(?:preview/)?cv?(\d+)|/(\d{6,})$")

# Content states. The public endpoint answers -404 while an article is in review; the
# author-side one keeps working.
STATES = {-2: "审核中", 0: "已发布", 1: "待审核", 2: "未通过"}

# Polling faster than this only hammers the endpoint; the audit state does not move that fast.
MIN_WATCH_INTERVAL = 10

# Tags the editor keeps as-is. Anything else is demoted to plain text, which also destroys a
# code block's line breaks and alignment — render code as an image instead.
#
# `strong` / `blockquote` / the list tags were verified on 2026-11-08: all of them survive a
# publish round-trip through `show --raw` unchanged. `hr` did **not** — a published `<hr>` is
# gone from the stored body, so it is deliberately not whitelisted and gets reported as dropped.
ALLOWED_TAGS = (
    "p", "span", "br", "h1", "figure", "img", "figcaption", "a",
    "strong", "blockquote", "ul", "ol", "li",
)

# The article editor has no AI-declaration field (video uploads do). This is a body-level
# stopgap: `--ai` appends one paragraph of notice, and re-running replaces or removes it
# rather than stacking copies. When the editor grows a real field, swap this for that field
# and callers do not need to change.
#
# A notice has to support attribution and reproduction, so it says something concrete: the
# runtime, the model and the agent each get a line. Those three can only come from the
# caller — the tool does not assume which runtime it runs in, and cannot know which model is
# behind it. A field with no value is omitted, never invented.
AI_NOTICE_STEM = "本内容由 AI 参与创作。"
# The notice separates its lines with <br>, so `[^<]*` cannot be used: it stops at the first
# <br> and never matches. <br> may appear anywhere; nothing else inside the paragraph is a tag.
AI_NOTICE_RE = re.compile(r"\s*<p>\s*本内容由 AI 参与创作(?:[^<]|<br\s*/?>)*</p>\s*$")



def _ai_notice_text(client: str = "", model: str = "", agent: str = "") -> str:
    """Join the notice items one per line. Empty items are skipped, never filled in.

    `feat.` follows the generator's name, so the model reads as a co-contributor rather
    than a tool the agent merely drove.
    """
    a = agent.strip().rstrip("。")
    m = model.strip().rstrip("。")
    c = client.strip().rstrip("。")

    lines = [AI_NOTICE_STEM]
    if a and m:
        lines.append(f"智能体：{a} feat. {m}")   # `feat.` needs a name in front of it
    elif a:
        lines.append(f"智能体：{a}")
    elif m:
        lines.append(f"模型：{m}")               # no name, no `feat.`
    if c:
        lines.append(f"运行时：{c}")
    return "<br>".join(lines)


TAG_RE = re.compile(r"<\s*([a-z0-9]+)", re.I)
BR_RE = re.compile(r"<br\s*/?>", re.I)
P_RE = re.compile(r"</p>", re.I)
ANY_TAG_RE = re.compile(r"<[^>]+>")


# ── HTTP ─────────────────────────────────────────────────────────────────────


def _api(path: str, method: str, cred) -> Api:
    """One creative-center call.

    `verify=True` requires the credential, and the library appends `csrf` to `data` by itself for
    non-GET calls (unless `no_csrf` is set). POST bodies therefore go through `update_data`, never
    `update_params` — the latter replaces the whole params dict and would drop csrf.
    """
    return Api(url=f"{BASE}/{path}", method=method, verify=True, credential=cred)


async def _get(path: str, cred, **params: Any) -> Any:
    return await _api(path, "GET", cred).update_params(**params).result


async def _post(path: str, cred, **data: Any) -> Any:
    return await _api(path, "POST", cred).update_data(**data).result


async def _post_article(path: str, cred, **data: Any) -> Any:
    """Save-style calls: translate Bilibili's rate-limit codes into actionable errors."""
    try:
        return await _post(path, cred, **data)
    except Exception as exc:
        code = getattr(exc, "code", None)
        if code == 20019:
            raise BiliError(
                "api_error",
                "刚发布 / 刚改过的文章有冷却，短时间不能再提交。",
                hint="等几分钟再试。这是 B 站侧的冷却，不是本地限流——等它自然过期。",
                detail={"bili_code": 20019},
            ) from exc
        if code == 37109:
            # The per-day cap comes from Bilibili and depends on the account level; the local
            # guard cannot predict it, so it is reported as-is instead of being modelled.
            raise BiliError(
                "rate_limited",
                "今天的专栏发布额度用完了（B 站按账号等级限制每天能发几篇，低等级额度更少）。",
                hint="明天再发。这个额度是 B 站侧的，和本地 guard 的每小时 10 次是两回事。",
                detail={"bili_code": 37109},
            ) from exc
        if code == 37409:
            raise BiliError(
                "api_error",
                "这篇专栏正在审核，暂时不能编辑。",
                hint="等审核结束（`bili article show <cvid>` 能读到最新状态）后再试。",
                detail={"bili_code": 37409},
            ) from exc
        raise


async def _get_raw(path: str, cred, **params: Any) -> dict[str, Any]:
    """A GET that keeps the whole response envelope.

    `draft/list` and `article/list` put their payload at the top level (`artlist`) instead of
    under `data`, so the ordinary `_get` — which unwraps `data` — returns None for them.
    """
    raw = await _api(path, "GET", cred).update_params(**params).request(raw=True)
    return raw if isinstance(raw, dict) else {}


async def _read_article(cred, cvid: int) -> dict[str, Any]:
    """Author-side read; works during review, when the public x/article/view is a 404."""
    try:
        return await _get("article/view", cred, aid=cvid) or {}
    except Exception as exc:
        code = getattr(exc, "code", None)
        if code in (37104, -404, 404):
            # 37104 is "no permission over someone else's column", but the usual cause is that
            # the number is a draft id. A draft id can also come back as a bare -404. Either
            # way the answer is "this is not a published article", not "check your BV number".
            raise BiliError(
                "not_found",
                f"{cvid} 不是一篇已发布的文章。",
                hint="草稿请用 `bili article drafts` 找 id——`show` 只读已发布的文章，"
                     "草稿正文在本地文件里，或看 `draft --cvid <id> --dry-run` 的 chars。",
                detail={"bili_code": code},
            ) from exc
        raise


async def _read_draft(cred, cvid: int) -> dict[str, Any]:
    """Author-side draft read. Only `47030 该草稿已失效` means "there is no live draft".

    Everything else — not logged in, network error, rate limited — must surface as itself:
    folding those into not_found tells the caller to stop looking when the truth is "retry".
    """
    try:
        return await _get("draft/view", cred, aid=cvid) or {}
    except Exception as exc:
        if getattr(exc, "code", None) == 47030:
            raise BiliError(
                "not_found",
                f"读不到草稿 {cvid}。",
                hint="只对未发布的草稿有效；已发布的请用 `bili article show`。",
                detail={"bili_code": 47030},
            ) from exc
        raise


# ── helpers ──────────────────────────────────────────────────────────────────


def _plain(html: str) -> str:
    """Rough text length; the website counts differently, this is only a size hint."""
    return re.sub(r"\s+", "", ANY_TAG_RE.sub("", html))


def _plain_keep_lines(html: str) -> str:
    s = BR_RE.sub("\n", html)
    s = P_RE.sub("\n", s)
    s = ANY_TAG_RE.sub("", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def _apply_ai_notice(html: str, enabled: bool, client: str = "", model: str = "",
                     agent: str = "", reuse: str = "") -> str:
    """Idempotent: strip any existing notice first, then add one only when asked.

    `reuse` is the "keep what was there" channel: when the caller does not re-supply the
    three fields, the old notice goes back in verbatim — keeping only the fact that *some*
    notice existed would downgrade an attributable one into an empty sentence.
    """
    html = AI_NOTICE_RE.sub("", html).rstrip()
    if not enabled:
        return html
    if reuse:
        return html + "\n<p>" + reuse + "</p>"
    return html + f"\n<p>{_ai_notice_text(client, model, agent)}</p>"


def _resolve_ai_notice(old_html: str, ai: bool, no_ai: bool,
                       client: str = "", model: str = "", agent: str = "") -> tuple[bool, str]:
    """Decide notice handling when the caller gave no explicit `--ai` / `--no-ai`.

    Returns `(enabled, reuse)`. Shared by `draft` and `update` so both answer the same
    question the same way: replacing the body must not silently drop the old notice.
    """
    if ai and no_ai:
        raise BiliError("invalid_argument", "`--ai` 和 `--no-ai` 只能给一个。")
    if no_ai:
        return False, ""
    if ai:
        return True, ""
    # Without `--ai` the three fields must change nothing — not even when the body already
    # carries a notice. Letting that case "rewrite with what was given" is how a caller who
    # passed only `--ai-model` silently lost the client and agent lines.
    if client or model or agent:
        raise BiliError(
            "invalid_argument",
            "只给 --ai-client / --ai-model / --ai-agent 而不加 `--ai`，这三项不会生效。",
            hint="要改 AI 声明请一起给 `--ai`；不给 `--ai` 时声明整段保持原样。",
        )
    found = AI_NOTICE_RE.search(old_html or "")
    if not found:
        return False, ""
    return True, found.group(0).strip()[len("<p>"):-len("</p>")].strip()


def _check_tags(html: str) -> list[str]:
    """Tags the editor will not keep as-is."""
    return sorted({m.group(1).lower() for m in TAG_RE.finditer(html)} - set(ALLOWED_TAGS))


def _read_body(file: Optional[Path], text: Optional[str]) -> str:
    """Read the body from exactly one source.

    The check is `is None`, not truthiness: `--content ""` is a *given* (and empty) body, not
    an absent one. Testing truthiness made it look like "no source", so the error said
    "give only one" for a body the caller had just supplied.
    """
    if file is not None and text is not None:
        raise BiliError("invalid_argument", "正文只能给一个：`--file` 或 `--content`。")
    if file is None and text is None:
        raise BiliError("invalid_argument", "正文要写一点内容：用 `--file` 或 `--content`。")
    if file is not None:
        if str(file) == "-":
            return sys.stdin.read()
        if not file.is_file():
            raise BiliError("invalid_argument", f"正文文件不存在：{file}")
        return file.read_text(encoding="utf-8")
    return text or ""


def _data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    if not mime.startswith("image/"):
        raise BiliError("invalid_argument", f"不是图片：{path}")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


async def _upload_image(cred, path: Path) -> str:
    """Upload one article image and return its https URL.

    Shared by `image` and `embed` so both use the same rate-limit bucket and the same
    response-key fallback (`url` vs `image_url`).
    """
    guard.check("article_image")   # one article needs many images, so its own bucket
    data = await _post("article/upcover", cred, cover=_data_uri(path))
    url = str((data or {}).get("url") or (data or {}).get("image_url") or "")
    if not url:
        raise BiliError("api_error", "上传成功但没有拿到图片地址。", detail=data)
    return url.replace("http://", "https://")


# ── categories ───────────────────────────────────────────────────────────────


@app.command()
def categories(
    parent: Optional[int] = typer.Option(None, "--parent", help="只看某个一级分类的子分类。"),
) -> None:
    """列出专栏分类 ID（发布时必须给一个，否则 20022）。"""

    async def body():
        data = await Api(url=CATEGORIES, method="GET").result
        rows = []
        for top in data or []:
            if parent is not None and top.get("id") != parent:
                continue
            rows.append({
                "id": top.get("id"),
                "name": top.get("name"),
                "children": [{"id": c.get("id"), "name": c.get("name")} for c in (top.get("children") or [])],
            })
        return {
            "count": len(rows),
            "categories": rows,
            "hint": "发草稿时用二级分类：`bili article draft --category 34`",
        }

    run(body)


# ── image ────────────────────────────────────────────────────────────────────


def _figure(url: str, caption: str) -> str:
    """The figure markup the editor actually saves. A bare <img> renders, but without the
    figcaption styling and spacing."""
    cap = caption.replace("<", "&lt;").replace(">", "&gt;")
    return (
        f'<figure class="img-box" contenteditable="false">'
        f'<img src="{url}">'
        f'<figcaption class="caption" contenteditable="">{cap}</figcaption>'
        f"</figure>"
    )


@app.command()
def image(
    path: Path = typer.Argument(..., help="本地图片路径（png / jpg）。"),
    caption: str = typer.Option("", "--caption", "-c", help="图注（显示在图下方）。"),
) -> None:
    """上传一张正文配图，返回可粘进正文的 <figure> 片段和图片 URL。

    URL 可以直接喂给 `bili article draft --cover`。
    """

    async def body():
        if not path.is_file():
            raise BiliError("invalid_argument", f"图片不存在：{path}")
        params = {"file": str(path), "size": path.stat().st_size, "caption": caption}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "article_image", "params": params}
        cred = config.require_credential()
        url = await _upload_image(cred, path)
        guard.record("article_image", params, {"url": url})
        return {
            "url": url,
            "html": _figure(url, caption),
            "usage": "把 html 粘到正文里想放图的位置，和别的块同级（别套进 <p> 里）。"
                     "想按段落定位就用 `bili article embed --after`，那个会真的插进去。",
        }

    run(body)


# ── draft ────────────────────────────────────────────────────────────────────


@app.command()
def draft(
    title: Optional[str] = typer.Option(None, "--title", "-t", help="标题，40 字以内。"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="正文 HTML 文件；`-` 为 stdin。"),
    content: Optional[str] = typer.Option(None, "--content", help="正文 HTML 文本（与 --file 二选一）。"),
    category: Optional[int] = typer.Option(None, "--category", help="二级分类 ID（`bili article categories`）。"),
    cover: Optional[str] = typer.Option(None, "--cover", help="封面图 URL（`bili article image`）。"),
    # Same three-state problem as `--ai`: typer's double flag always returns a bool, so
    # "not given" collapsed into "original=True" and updating a 转载 draft silently turned it
    # back into 原创. Two separate flags keep "not given" distinguishable.
    # 80 列终端会截断，所以把结论放在前半句。
    original: bool = typer.Option(False, "--original", help="原创。"),
    reprint: bool = typer.Option(False, "--reprint", help="转载。"),
    # `--ai/--no-ai` cannot be one flag pair: typer's double flag always returns a bool
    # (default False), so Optional[bool] never sees None and the three states collapse into
    # two. Two separate flags keep "not given" distinguishable.
    ai: bool = typer.Option(False, "--ai", help="AI 创作声明（专栏无官方字段）。"),
    no_ai: bool = typer.Option(False, "--no-ai", help="移除已有声明。"),
    ai_client: str = typer.Option("", "--ai-client", help="客户端名，如 <客户端>。"),
    ai_model: str = typer.Option("", "--ai-model", help="模型名，如 <模型>。"),
    ai_agent: str = typer.Option("", "--ai-agent", help="智能体名，如 <智能体名>。"),
    cvid: Optional[int] = typer.Option(None, "--cvid", help="更新这个草稿；不给则新建。"),
) -> None:
    """新建一个草稿，或更新已有草稿（--cvid）。

    更新时，以下几处不给就沿用草稿现有的：
    标题、正文、分类、封面、原创/转载、AI 声明。
    新建时默认原创、无 AI 声明。
    三项 --ai-* 必须配合 `--ai` 使用。
    发布用 `bili article publish`。
    """

    async def body():
        cred = config.require_credential()
        cat_id = category
        existing: dict[str, Any] = {}
        if cvid:
            try:
                existing = await _read_draft(cred, cvid) or {}
            except BiliError as exc:
                if exc.code != "not_found":
                    raise
                # 明确指定了要更新的草稿却读不到：报错，别让 `--cvid` 悄悄退化成别的路径。
                raise BiliError(
                    "not_found",
                    f"找不到草稿 {cvid}。",
                    hint="cvid 可能记错了，用 `bili article drafts` 列一下；"
                         "已经发布过的请用 `bili article update`。",
                ) from exc
        if file is None and content is None:
            # Changing only the title / category / notice must not require re-sending the
            # body: read it back from the draft. `update` (published) has always worked this
            # way; `draft` failing with "give some body" made no sense to a caller that only
            # wanted a new title.
            raw = (existing.get("content") or "") if cvid else ""
            if not raw:
                raise BiliError("invalid_argument", "正文要写一点内容：用 `--file` 或 `--content`。")
        else:
            raw = _read_body(file, content)
        if not raw.strip():
            raise BiliError("invalid_argument", "正文不能为空。")
        old_html = existing.get("content") or ""
        if cat_id is None:
            current_cat = existing.get("category")
            cat_id = current_cat.get("id") if isinstance(current_cat, dict) else None

        draft_title = title if title is not None else (existing.get("title") or "")
        if not draft_title.strip():
            raise BiliError("invalid_argument", "标题不能为空。")
        if len(draft_title) > 40:
            raise BiliError("invalid_argument", "标题不能超过 40 字。")

        if original and reprint:
            raise BiliError("invalid_argument", "`--original` 和 `--reprint` 只能给一个。")
        if original:
            is_original = True
        elif reprint:
            is_original = False
        elif cvid:
            is_original = bool(existing.get("original", True))
        else:
            is_original = True    # new drafts are original by default

        enabled, reuse = _resolve_ai_notice(old_html, ai, no_ai, ai_client, ai_model, ai_agent)
        html = _apply_ai_notice(raw, enabled, ai_client, ai_model, ai_agent, reuse)
        dropped = _check_tags(html)
        # Report what actually landed, not what was typed. With `--no-ai` (or no `--ai` at
        # all) the three fields never reach the body, so echoing them back would be a lie.
        declares: dict[str, Any] = {"client": None, "model": None, "agent": None, "reused": False}
        if enabled and reuse:
            declares["reused"] = True          # carried over verbatim from the old body
        elif enabled:
            declares.update(client=ai_client or None, model=ai_model or None, agent=ai_agent or None)
        params: dict[str, Any] = {
            "cvid": cvid, "title": draft_title, "category": cat_id, "cover": bool(cover),
            "original": is_original, "ai": enabled, "chars": len(_plain(html)),
            "ai_declares": declares,
        }
        # Warn early, and in the dry-run too: a clean rehearsal followed by a bad publish is worse.
        # `--ai-client` 也是声明的一项，漏掉它等于 warning 自己没做到它要求别人做的事。
        missing = [f for f, v in (("--ai-client", ai_client), ("--ai-model", ai_model),
                                  ("--ai-agent", ai_agent)) if not v.strip()]
        warning = None
        # `reuse` means the old notice was carried over whole — its model and agent are right
        # there, so calling it "an empty sentence" was the warning being wrong, not the notice.
        if enabled and not reuse and missing:
            warning = (f"AI 声明缺 {' 和 '.join(missing)}，只剩一句空话。"
                       "声明要能追责和复现：补上客户端、模型、智能体三项再建一次。")
        # Warnings are collected, not overwritten: the AI-notice warning and the
        # missing-category warning used to share one key, so the second hid the first.
        warnings = []
        if warning:
            warnings.append(warning)
        if cat_id is None:
            warnings.append("没有分类，publish 会报 20022。用 `bili article categories` 查一个再补。")
        # 帮助里用的就是 `<客户端>` 这种占位符；照字面传进来不会有任何校验拦它，
        # 于是正文里会真的落下一行「客户端：<客户端>」。
        placeholders = [f for f, v in (("--ai-client", ai_client), ("--ai-model", ai_model),
                                       ("--ai-agent", ai_agent))
                        if v.strip().startswith("<") and v.strip().endswith(">")]
        if placeholders:
            warnings.append(f"{' / '.join(placeholders)} 看起来是帮助里的占位符，不像真实值。")

        if output.settings.dry_run:
            out = {"dry_run": True, "action": "article.draft", "params": params, "dropped_tags": dropped}
            if warnings:
                out["warning"] = warnings
            return out
        # Saving a draft is not a public action, so it gets its own bucket rather than
        # sharing the publish one — otherwise the documented `draft` → `publish` flow
        # trips the 30-second gap on its second step.
        guard.check("article_draft")
        payload: dict[str, Any] = {"title": draft_title, "content": html, "original": 1 if is_original else 0}
        if cvid:
            payload["aid"] = cvid
        if cat_id is not None:
            # The field is `category`, not `category_id`; the latter is stored as template_id
            # and the article then shows up with no category at all.
            payload["category"] = cat_id
        if cover:
            payload["banner_url"] = cover
        data = await _post("draft/addupdate", cred, **payload)
        aid = int((data or {}).get("aid") or cvid or 0)
        guard.record("article_draft", params, {"cvid": aid})
        out: dict[str, Any] = {
            "cvid": aid,
            "title": draft_title,
            "category": cat_id,
            # `--reprint` 有没有生效，光看 ok:true 是看不出来的——回一下实际落下来的值。
            "original": is_original,
            "ai": enabled,
            "ai_declares": declares,
            # 更新时也想确认正文有没有真的换掉；不给的话只能再跑一次 dry-run。
            "chars": len(_plain(html)),
            "next": f"发布：`bili article publish {aid}`",
        }
        if warnings:
            out["warning"] = warnings
        if dropped:
            out["dropped_tags"] = dropped
            out["note"] = "这些标签专栏编辑器不会保留，会被降级成普通文本。代码块请改用图片。"
        return out

    run(body)


# ── publish ──────────────────────────────────────────────────────────────────


@app.command()
def publish(
    cvid: int = typer.Argument(..., help="草稿的 cvid。"),
    category: Optional[int] = typer.Option(None, "--category", help="分类 ID；草稿里没存就必须给。"),
) -> None:
    """把草稿提交发布（会进审核）。"""

    async def body():
        cred = config.require_credential()
        d = await _read_draft(cred, cvid)
        html = d.get("content") or ""
        if not html.strip():
            raise BiliError("not_found", f"草稿 {cvid} 没有正文，或它不属于你。")
        cat = category if category is not None else ((d.get("category") or {}).get("id") or None)
        if not cat:
            # Without a category, submit always fails with 20022; refusing here is more
            # useful than relaying a server error.
            raise BiliError(
                "invalid_argument",
                "这篇草稿没有分类，发布必然报 20022。",
                hint="用 `bili article categories` 查一个，然后 `bili article publish <cvid> --category 34`。",
            )
        params = {"cvid": cvid, "title": d.get("title"), "category": cat}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "article.publish", "params": params}
        payload: dict[str, Any] = {
            "aid": cvid,
            "title": d.get("title") or "",
            "content": html,
            "category": cat,
            # `submit` requires this field, but forcing 0 would reset a reprint (转载) article
            # that the author set on the web; carry the draft's own value back instead.
            "reprint": int(d.get("reprint") or 0),
            "original": 1 if d.get("original", True) else 0,
        }
        if d.get("banner_url"):
            payload["banner_url"] = d["banner_url"]
        guard.check("article")
        data = await _post_article("article/submit", cred, **payload)
        guard.record("article", params, {"cvid": cvid, "state": (data or {}).get("state")})
        # Publishing assigns a new cvid (the draft id is not the article id); the real value
        # only appears inside the preview link.
        view_url = str((data or {}).get("view_url") or "")
        m = ARTICLE_ID_RE.search(view_url)
        real_id = (m.group(1) or m.group(2)) if m else None
        out: dict[str, Any] = {
            "draft_id": cvid,
            "cvid": int(real_id) if real_id else None,
            "state": STATES.get((data or {}).get("state"), (data or {}).get("state")),
            "preview": view_url or None,
            "next": "发布后 cvid 会变（草稿 ID 不是文章 ID）。审核期间公开链接返回 404，属正常。",
        }
        if not real_id:
            out["warning"] = "没能从预览链接里解析出文章 cvid，请到创作中心确认。"
        return out

    run(body)


# ── update ───────────────────────────────────────────────────────────────────


@app.command()
def update(
    cvid: int = typer.Argument(..., help="已发布文章的 cvid。"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="正文 HTML 文件；`-` 为 stdin。"),
    content: Optional[str] = typer.Option(None, "--content", help="正文 HTML 文本。"),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="新标题。"),
    cover: Optional[str] = typer.Option(None, "--cover", help="新封面 URL。"),
    ai: bool = typer.Option(False, "--ai", help="追加 AI 创作声明。"),
    no_ai: bool = typer.Option(False, "--no-ai", help="移除已有声明。"),
    ai_client: str = typer.Option("", "--ai-client", help="声明里的客户端。"),
    ai_model: str = typer.Option("", "--ai-model", help="声明里的模型。"),
    ai_agent: str = typer.Option("", "--ai-agent", help="声明里的智能体名。"),
) -> None:
    """原地编辑一篇已发布的文章。会重新进审核，cvid 与动态 ID 不变。

    标题、正文、封面、AI 声明不给就沿用现有的；三项 --ai-* 必须配合 `--ai` 使用。
    分类与原创/转载改不了——那两项只能在草稿阶段（`bili article draft`）定。
    """

    async def body():
        cred = config.require_credential()
        current = await _read_article(cred, cvid)   # author side: readable during review too
        old_html = current.get("content") or ""
        gave_body = file is not None or content is not None
        new_html = _read_body(file, content) if gave_body else old_html
        # A replacement body has no notice of its own, so editing without an explicit flag
        # would drop the one the old body carried. Same resolver as `draft`.
        # 总是先解析：`--ai-*` 给了却不给 `--ai` 时要报错，不能因为「这次没换正文」就跳过
        # 检查——那样 `update --ai-model x` 会假成功（返回 ok:true，但什么都没写）。
        enabled, reuse = _resolve_ai_notice(old_html, ai, no_ai, ai_client, ai_model, ai_agent)
        if ai or no_ai or gave_body:
            new_html = _apply_ai_notice(new_html, enabled, ai_client, ai_model, ai_agent, reuse)
        if not new_html.strip():
            raise BiliError("invalid_argument", "正文不能为空。")
        if title is not None:
            if not title.strip():
                raise BiliError("invalid_argument", "标题不能为空。")
            new_title = title
        else:
            new_title = current.get("title") or ""
        if len(new_title) > 40:
            raise BiliError("invalid_argument", "标题不能超过 40 字。")
        cat = (current.get("category") or {}).get("id") or None
        dropped = _check_tags(new_html)
        params = {"cvid": cvid, "title": new_title, "category": cat, "chars": len(_plain(new_html))}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "article.update", "params": params, "dropped_tags": dropped}
        payload: dict[str, Any] = {
            "aid": cvid,
            "title": new_title,
            "content": new_html,
            # Editing must not silently reset the author's reprint (转载) setting; carry it back.
            "reprint": int(current.get("reprint") or 0),
            "original": 1 if current.get("original", True) else 0,
        }
        if cat:
            payload["category"] = cat
        if cover or current.get("banner_url"):
            payload["banner_url"] = cover or current["banner_url"]
        guard.check("article")
        data = await _post_article("article/update", cred, **payload)
        guard.record("article", params, {"cvid": cvid})
        out: dict[str, Any] = {
            "cvid": cvid,
            "title": new_title,
            # 和建草稿那边对齐：只看 ok:true 看不出分类 / 原创 / 声明有没有被动过。
            "category": payload.get("category"),
            "original": bool(payload.get("original")),
            "ai": enabled,
            "state": STATES.get((data or {}).get("state"), (data or {}).get("state")),
            # 回一下正文长度：不然调用方只能再跑一次 dry-run 才知道内容有没有真的换掉。
            "chars": len(_plain(new_html)),
            "next": "编辑会重新进审核；审核期间 `bili article show` 仍可读（走作者侧接口）。",
        }
        if dropped:
            out["dropped_tags"] = dropped
            out["note"] = "这些标签已被降级成普通文本，代码块请改用图片。"
        return out

    run(body)


# ── delete ───────────────────────────────────────────────────────────────────


@app.command()
def delete(
    cvid: int = typer.Argument(..., help="要删除的 cvid（草稿或已发布都行）。"),
) -> None:
    """删除一篇自己的专栏。不可撤销。"""

    async def body():
        cred = config.require_credential()
        # Confirm it is ours first: articles and drafts sit behind two different reads, and
        # one of them failing does not mean it is not ours. Remember which kind it is, too —
        # deleting needs the matching endpoint, and mixing them fails with 37104.
        found = None
        kind = None
        for reader, k in ((_read_article, "article"), (_read_draft, "draft")):
            try:
                found = await reader(cred, cvid)
            except Exception as exc:
                # Only "there is nothing here" means keep looking. A login / risk-control /
                # network failure must surface — reporting it as "not in your account" tells
                # the caller to stop when the truth is "retry".
                #
                # The two readers answer "not yours" differently: `_read_article` maps 37104
                # to not_found, but a draft id can also come back as a bare -404 straight
                # from the API. Narrowing this to `except BiliError` missed the second one
                # and made `delete` fail on real drafts while still reporting not_found.
                code = getattr(exc, "code", None)
                if isinstance(exc, BiliError):
                    if exc.code != "not_found":
                        raise
                elif code not in (404, -404, 37104, 47030):
                    raise
                found = None
            if found:
                kind = k
                break
        if not found:
            raise BiliError(
                "not_found",
                f"你的账号里找不到专栏 {cvid}（文章和草稿都试过了），不删。",
                hint="cvid 可能记错了。文章用 `bili article show` 确认；草稿 ID 在建草稿时返回过。",
            )
        params = {"cvid": cvid, "kind": kind, "title": found.get("title")}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "article.delete", "params": params}
        guard.check("article")
        # Drafts and published articles have separate delete endpoints; the wrong one fails
        # with 37104.
        await _post("draft/delete" if kind == "draft" else "article/delete", cred, aid=cvid)
        guard.record("article", params, "ok")
        return {"cvid": cvid, "deleted": True, "kind": kind, "title": found.get("title")}

    run(body)


# ── show ─────────────────────────────────────────────────────────────────────


@app.command()
def show(
    cvid: int = typer.Argument(..., help="已发布文章的 cvid（草稿会返回 not_found）。"),
    text: bool = typer.Option(False, "--text", help="输出纯文本正文，而不是摘要。"),
) -> None:
    """读一篇专栏：标题、字数、图片数、封面；`--text` 给出正文。

    只读已发布的文章；草稿会返回 not_found，草稿正文请在本地文件里看。
    """

    async def body():
        cred = config.require_credential()
        d = await _read_article(cred, cvid)
        # The global `--raw` promises the unsummarised payload; without this branch the flag
        # was silently ignored here, which also made `reprint` / 原创 impossible to observe.
        if output.settings.raw:
            return d
        html = d.get("content") or ""
        if text:
            return {"cvid": cvid, "title": d.get("title"), "text": _plain_keep_lines(html)}
        return {
            "cvid": cvid,
            "title": d.get("title"),
            "category": (d.get("category") or {}).get("id") or None,
            "cover": d.get("banner_url") or None,
            "chars": len(_plain(html)),
            "images": html.count("<img"),
            "dropped_tags": _check_tags(html),
            "published": iso_time(d.get("publish_time")),
            "url": f"https://www.bilibili.com/read/cv{cvid}",
        }

    run(body)


# ── lists ────────────────────────────────────────────────────────────────────


@app.command()
def drafts(
    page: int = typer.Option(1, "--page", help="页码，从 1 开始。"),
    limit: int = typer.Option(20, "--limit", help="每页条数。"),
) -> None:
    """列出草稿箱里的草稿。

    草稿 id 不是文章 id：发布之后它会变成另一个数字，这个 id 就作废了。
    建了草稿又忘了 cvid 时，先用这里把它找回来。
    """

    async def body():
        cred = config.require_credential()
        raw = await _get_raw("draft/list", cred, pn=page, ps=limit)
        art = raw.get("artlist") or {}
        rows = art.get("drafts") or []
        items = [{
            "cvid": d.get("id"),
            "title": d.get("title"),
            # The draft box answers with the **top-level** category (17 科技), not the
            # second-level id that `draft --category` expects — feeding this back would
            # silently pick a different section.
            "top_category": (d.get("category") or {}).get("id"),
            "modified": iso_time(d.get("mtime")),
            "edit_url": d.get("edit_url") or None,
        } for d in rows]
        return {
            "count": len(items),
            "total": (art.get("page") or {}).get("total") or 0,
            "page": page,
            "drafts": items,
            "next": "草稿 id 只在发布前有效；发布后请用 publish 返回的 cvid。",
            "note": "top_category 是 B 站回的一级分类；新建草稿请用 `bili article categories` 查二级 ID。",
        }

    run(body)


@app.command()
def articles(
    page: int = typer.Option(1, "--page", help="页码，从 1 开始。"),
    limit: int = typer.Option(20, "--limit", help="每页条数。"),
) -> None:
    """列出已发布的专栏（按创作中心返回的顺序）。"""

    async def body():
        cred = config.require_credential()
        raw = await _get_raw("article/list", cred, pn=page, ps=limit)
        art = raw.get("artlist") or {}
        rows = art.get("articles") or []
        items = []
        # `article/list` ignores `ps` (verified 2026-11-08: --limit 1/2/3/20 all came back with
        # every row), so `--limit` has to be honoured here. Silently returning more than asked
        # would be exactly the kind of failure that looks like success.
        for a in rows[:limit]:
            code = a.get("state")
            items.append({
                "cvid": a.get("id"),
                "title": a.get("title"),
                # `STATES.get(code, code)` leaked the raw int for unmapped states, so one row
                # carried "已发布" and the next `7` — same key, two types, string filters broke.
                "state": STATES.get(code, f"未知({code})"),
                "state_code": code,
                "published": iso_time(a.get("publish_time")),
                "url": f"https://www.bilibili.com/read/cv{a.get('id')}",
            })
        return {
            "count": len(items),
            "total": (art.get("page") or {}).get("total") or 0,
            "page": page,
            "articles": items,
            "note": "`--limit` 是客户端截断：article/list 不认 ps，翻页请用 --page。",
        }

    run(body)


# ── watch ────────────────────────────────────────────────────────────────────


@app.command()
def watch(
    cvid: int = typer.Argument(..., help="要盯的专栏 cvid。"),
    interval: int = typer.Option(30, "--interval", help="轮询间隔（秒），不能小于 10。"),
    timeout: int = typer.Option(600, "--timeout", help="最长等待（秒），0 表示一直等。"),
) -> None:
    """等一篇专栏过审：轮询作者侧状态，通过/驳回/超时再返回。

    状态码来自 x/article/creative/article/view 的 state：
      -2 审核中    0 已发布    2 未通过
    只有 0 与 2 算终点；其余（含 1 待审核）继续等，不会误报成过审。
    超时和请求失败都返回 ok:false，不会伪装成通过。
    """

    async def body():
        cred = config.require_credential()
        if interval < MIN_WATCH_INTERVAL:
            # A sub-10s interval just hammers the endpoint; the state does not move that fast.
            raise BiliError(
                "invalid_argument",
                f"--interval 不能小于 {MIN_WATCH_INTERVAL} 秒（给了 {interval}）。",
                hint="审核状态不会在几秒内变化，间隔太短只会触发风控。",
            )

        started = time.monotonic()
        polls = 0
        seen: list[str] = []
        while True:
            d = await _read_article(cred, cvid)
            state = d.get("state")
            polls += 1
            label = STATES.get(state, str(state))
            if not seen or seen[-1] != label:
                seen.append(label)
                output.info(f"[{polls}] state={state} ({label})")
            # Only 0 (published) and 2 (rejected) are terminal. `1` is pending review (待审核)
            # and must keep waiting: treating every non-`-2` state as done reported review as
            # approval.
            if state in (0, 2):
                out = {
                    "cvid": cvid,
                    "state": label,
                    "polls": polls,
                    "waited": round(time.monotonic() - started),
                    "title": d.get("title"),
                    "url": f"https://www.bilibili.com/read/cv{cvid}",
                }
                if state == 2:
                    out["rejected"] = True
                    out["reason"] = d.get("reason") or "(接口没给原因)"
                    out["hint"] = "改完再 `bili article update` 会重新进审核。"
                else:
                    out["hint"] = "已过审，公开链接现在可以访问了。"
                return out
            if timeout and (time.monotonic() - started) >= timeout:
                raise BiliError(
                    "api_error",
                    f"等了 {timeout} 秒还是{label}（state={state}）。",
                    hint=f"稍后再跑 `bili article watch {cvid}`，或用 `bili article show {cvid}` 看一次。",
                    detail={"cvid": cvid, "polls": polls, "state": state},
                )
            await asyncio.sleep(interval)

    # --timeout owns the waiting budget; the 3-minute global deadline would abort this first.
    run(body, deadline=None)

# ── embed ────────────────────────────────────────────────────────────────────

BLOCK_END_RE = re.compile(r"</(?:p|h1|figure)>", re.I)


def _find_block_end(html: str, anchor: str) -> int:
    """Return the offset just past the block that contains `anchor`.

    Raises when the anchor is missing or appears more than once — guessing a position is
    worse than failing, because the wrong position still looks like success.
    """
    hits = [m.start() for m in re.finditer(re.escape(anchor), html)]
    if not hits:
        raise BiliError(
            "invalid_argument",
            f"正文里找不到锚点：{anchor}",
            hint="锚点要能和正文原样对上（区分全半角）。锚点是从你手上的正文 HTML 里找的，"
                 "直接对照那个文件即可——不用先 show（草稿阶段 show 读不到）。",
        )
    if len(hits) > 1:
        raise BiliError(
            "invalid_argument",
            f"锚点不唯一（出现 {len(hits)} 次）：{anchor}",
            hint="换一段更长、只出现一次的片段。",
        )
    after = hits[0] + len(anchor)
    m = BLOCK_END_RE.search(html, after)
    if not m:
        raise BiliError("invalid_argument", f"锚点 {anchor} 之后找不到块的结束标签（</p> / </h1> / </figure>）。")
    return m.end()


@app.command()
def embed(
    file: Path = typer.Argument(..., help="正文 HTML 文件。"),
    image: Path = typer.Option(..., "--image", "-i", help="要插入的图片。"),
    after: str = typer.Option(..., "--after", "-a", help="插在这段文字所在块之后（须全文唯一）。"),
    caption: str = typer.Option("", "--caption", "-c", help="图注。"),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="写到这个文件；默认打印到 stdout。"),
) -> None:
    """上传一张图，并插到正文里指定段落之后，输出新的正文。

    锚点由 `--after` 决定：一段正文里独一无二、只出现一次的文字。
    工具找到它所在的块（p / h1 / figure），把图片插在那个块之后。
    锚点找不到或不唯一都会报错，不会退而求其次塞到末尾。
    """

    async def body():
        if not file.is_file():
            raise BiliError("invalid_argument", f"正文文件不存在：{file}")
        if not image.is_file():
            raise BiliError("invalid_argument", f"图片不存在：{image}")
        html = file.read_text(encoding="utf-8")
        pos = _find_block_end(html, after)   # locate first: a bad anchor must not cost an upload
        params = {"file": str(file), "image": str(image), "after": after}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "article.embed", "params": params,
                    "insert_at": pos, "preview": html[:pos] + "\n<!-- 图插在这里 -->\n" + html[pos:]}
        cred = config.require_credential()
        url = await _upload_image(cred, image)
        fig = _figure(url, caption)
        merged = html[:pos] + "\n" + fig + "\n" + html[pos:]
        guard.record("article_image", params, {"url": url})
        result: dict[str, Any] = {
            "url": url,
            "inserted_after": after,
            "at": pos,
            "html": merged,
        }
        if out:
            out.write_text(merged, encoding="utf-8")
            result.pop("html")
            result["written"] = str(out)
            result["next"] = f"用 `bili article draft --file {out} ...` 或 `bili article update <cvid> --file {out}`"
        else:
            result["next"] = "把 data.html 写到文件，再传给 `--file`"
        return result

    run(body)
