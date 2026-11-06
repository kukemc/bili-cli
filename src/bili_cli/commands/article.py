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
import os
import mimetypes
import re
import sys
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
# 预览链接有两种形态：`/read/preview/53767611`（preview 后没有 c）和 `/read/cv53767611`。
# 两条分支各自捕获，取的时候哪个有值用哪个——之前只看 group(1)，碰到 preview 形态
# 就是 int(None)，而那时 submit 已经执行完了，于是「报错但已发布」。
ARTICLE_ID_RE = re.compile(r"/read/(?:preview/)?cv?(\d+)|/(\d{6,})$")

# 内容状态。审核期间公开接口返回 -404，但作者侧仍能读。
STATES = {-2: "审核中", 0: "已发布", 1: "待审核", 2: "未通过"}

# 专栏编辑器的标签白名单（实测）。不在名单里的标签会被降级成普通文本，
# 代码块的换行与对齐都会丢失——代码请渲染成图片。
ALLOWED_TAGS = ("p", "span", "br", "h1", "figure", "img", "figcaption", "a")

# 专栏编辑器没有 AI 创作声明字段（投稿有，专栏没有）。这是正文级别的权宜之计：
# 打开 `--ai` 就在末尾追加一段声明，关掉或再跑一次会自动替换/移除，不会重复堆叠。
# 等编辑器真的提供官方字段时，把这里换成那个字段即可，调用方不用改。
#
# 声明要能追责和复现，所以不写空话：客户端、模型、智能体各占一行。
# **这三项只能由调用方给**——工具不假定自己在哪个运行时里跑，也就不去猜客户端，
# 更不可能知道你背后是哪个模型。取不到就不写那一行，不编。
AI_NOTICE_STEM = "本内容由 AI 参与创作。"
# 声明内部用 <br> 分行，所以不能用 [^<]*（那会在第一个 <br> 停住，永远匹配不上）。
# 允许 <br> 穿插，其余内容仍不含标签。
AI_NOTICE_RE = re.compile(r"\s*<p>\s*本内容由 AI 参与创作(?:[^<]|<br\s*/?>)*</p>\s*$")



def _ai_notice_text(client: str = "", model: str = "", agent: str = "") -> str:
    """按「一项一行」拼声明。空项跳过，不占位、不编造。

    格式沿用 NixKits 主文档的作者区块：`<名字> feat. <模型>` ——
    `feat.` 跟在生成者后面，模型不是「工具」，是参与制作者。
    """
    a = agent.strip().rstrip("。")
    m = model.strip().rstrip("。")
    c = client.strip().rstrip("。")

    lines = [AI_NOTICE_STEM]
    if a and m:
        lines.append(f"智能体：{a} feat. {m}")   # 有名字才用 feat.
    elif a:
        lines.append(f"智能体：{a}")
    elif m:
        lines.append(f"模型：{m}")               # 没名字就别硬套 feat.
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
    """保存类调用，把 B 站的限流码翻成人话。"""
    try:
        return await _post(path, cred, **data)
    except Exception as exc:
        code = getattr(exc, "code", None)
        if code == 20019:
            raise BiliError(
                "api_error",
                "刚发布/刚改过的文章有冷却，短时间不能再用相同标题提交。",
                hint="等几分钟再试，或者换一个标题——20019 只在标题和上一篇相同时触发，"
                     "所以改标题能立刻绕过（正文不变也算改）。",
                detail={"bili_code": 20019},
            ) from exc
        if code == 37109:
            # 这是 B 站侧的每日上限，guard 拦不到——Lv5 每天 5 篇，Lv1-3 每天 1 篇。
            raise BiliError(
                "rate_limited",
                "今天的专栏发布额度用完了（B 站按等级限制，Lv5 及以上每天 5 篇封顶，Lv1-3 每天 1 篇）。",
                hint="明天再发。这个额度是 B 站侧的，和本地 guard 的每小时 10 次是两回事；"
                     "Lv6 也一样是 5 篇，没有额外额度。",
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


async def _read_article(cred, cvid: int) -> dict[str, Any]:
    """作者侧读取，审核期也能用（公开的 x/article/view 此时是 404）。"""
    try:
        return await _get("article/view", cred, aid=cvid) or {}
    except Exception as exc:
        if getattr(exc, "code", None) == 37104:
            # B 站原话是「无权限操作他人专栏」，实际最常见的原因是：这个数字是草稿 ID，
            # 不是文章 ID。照原文显示会把人引到「我是不是没权限」上去。
            raise BiliError(
                "not_found",
                f"{cvid} 不是一篇已发布的文章。",
                hint="草稿要先用 `bili article publish` 发布；发布后 cvid 会变成另一个数字（publish 的返回里有两个）。",
                detail={"bili_code": 37104},
            ) from exc
        raise


async def _read_draft(cred, cvid: int) -> dict[str, Any]:
    try:
        return await _get("draft/view", cred, aid=cvid) or {}
    except Exception as exc:
        raise BiliError(
            "not_found",
            f"读不到草稿 {cvid}。",
            hint="只对未发布的草稿有效；已发布的请用 `bili article show`。",
        ) from exc


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
    """幂等：先摘掉已有的声明，再按开关决定加不加。

    `reuse` 是「沿用原文」的通道：更新草稿时如果调用方没重新给三项，
    就把原来那整段声明原样放回去——只保住「有声明」而丢掉里面的模型和
    智能体信息，等于把一份能追责的声明降级成一句空话。
    """
    html = AI_NOTICE_RE.sub("", html).rstrip()
    if not enabled:
        return html
    if reuse:
        return html + "\n<p>" + reuse + "</p>"
    return html + f"\n<p>{_ai_notice_text(client, model, agent)}</p>"


def _check_tags(html: str) -> list[str]:
    """Tags the editor will not keep as-is."""
    return sorted({m.group(1).lower() for m in TAG_RE.finditer(html)} - set(ALLOWED_TAGS))


def _read_body(file: Optional[Path], text: Optional[str]) -> str:
    if bool(file) == bool(text):
        raise BiliError("invalid_argument", "正文只能给一个：`--file` 或 `--content`。")
    if file:
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
    """专栏编辑器实际保存的图片结构。裸 <img> 能显示，但没有 figcaption 的样式和留白。"""
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
    """上传一张正文配图，直接吐出可以粘进正文的 <figure> 片段。"""

    async def body():
        if not path.is_file():
            raise BiliError("invalid_argument", f"图片不存在：{path}")
        params = {"file": str(path), "size": path.stat().st_size, "caption": caption}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "article_image", "params": params}
        cred = config.require_credential()
        guard.check("article_image")   # 单独限流：一篇文章可能要传很多图
        data = await _post("article/upcover", cred, cover=_data_uri(path))
        url = str((data or {}).get("url") or (data or {}).get("image_url") or "")
        if not url:
            raise BiliError("api_error", "上传成功但没有拿到图片地址。", detail=data)
        url = url.replace("http://", "https://")
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
    title: str = typer.Option(..., "--title", "-t", help="标题，40 字以内。"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="正文 HTML 文件；`-` 表示 stdin。"),
    content: Optional[str] = typer.Option(None, "--content", help="正文 HTML 文本（与 --file 二选一）。"),
    category: Optional[int] = typer.Option(None, "--category", help="二级分类 ID；用 `bili article categories` 查。"),
    cover: Optional[str] = typer.Option(None, "--cover", help="封面图 URL（用 `bili article image` 得到）。"),
    original: bool = typer.Option(True, "--original/--reprint", help="原创还是转载。"),
    # 不能用 `--ai/--no-ai`：typer 的双 flag 固定返回 bool（默认 False），
    # Optional[bool] 收不到 None，三态就失效了。拆成两个 flag 才分得清「不给」。
    ai: bool = typer.Option(False, "--ai", help="AI 创作声明（专栏暂无官方字段，权宜之计）。"),
    no_ai: bool = typer.Option(False, "--no-ai", help="显式移除已有的 AI 声明。"),
    ai_client: str = typer.Option("", "--ai-client", help="声明里的客户端，例如 `DeepSeek Harness`。"),
    ai_model: str = typer.Option("", "--ai-model", help="声明里的模型，例如 `deepseek-flash`。"),
    ai_agent: str = typer.Option("", "--ai-agent", help="声明里的智能体名，例如 `小爪`。"),
    cvid: Optional[int] = typer.Option(None, "--cvid", help="给了就更新这个草稿，否则新建。"),
) -> None:
    """新建一个草稿（或更新已有草稿）。发布用 `bili article publish`。"""

    async def body():
        if len(title) > 40:
            raise BiliError("invalid_argument", "标题不能超过 40 字。")
        cred = config.require_credential()
        raw = _read_body(file, content)
        if not raw.strip():
            raise BiliError("invalid_argument", "正文不能为空。")
        # 新建草稿：不给就不加（干净起步）。
        # 更新草稿：不给就保持现状——判断依据是**草稿里现在的内容**，不是新正文。
        # 新正文里本来就没有声明，拿它判断永远是 False，那这个「保持」就是假的。
        reuse = ""
        if no_ai:
            enabled = False
        elif ai:
            enabled = True
        else:
            enabled = False
            if cvid:
                try:
                    existing = await _read_draft(cred, cvid)
                except Exception:
                    existing = {}
                old = (existing or {}).get("content") or ""
                found = AI_NOTICE_RE.search(old)
                if found:
                    enabled = True
                    # 没重新给三项时，沿用原文（连同里面的模型和智能体）
                    if not (ai_client or ai_model or ai_agent):
                        reuse = found.group(0).strip()[len("<p>"):-len("</p>")].strip()
        html = _apply_ai_notice(raw, enabled, ai_client, ai_model, ai_agent, reuse)
        dropped = _check_tags(html)
        params: dict[str, Any] = {
            "cvid": cvid, "title": title, "category": category, "cover": bool(cover),
            "original": original, "ai": enabled, "chars": len(_plain(html)),
            "ai_declares": {"client": ai_client or None, "model": ai_model or None, "agent": ai_agent or None},
        }
        # 声明不完整时先提醒，dry-run 也要能看见——不然试跑一遍是干净的，真发出去才发现缺东西
        missing = [f for f, v in (("--ai-model", ai_model), ("--ai-agent", ai_agent)) if not v.strip()]
        warning = None
        if enabled and missing:
            warning = (f"AI 声明缺 {' 和 '.join(missing)}，只剩一句空话。"
                       "声明要能追责和复现：补上客户端、模型、智能体三项再建一次。")
        if output.settings.dry_run:
            out = {"dry_run": True, "action": "article.draft", "params": params, "dropped_tags": dropped}
            if warning:
                out["warning"] = warning
            return out
        guard.check("article")
        payload: dict[str, Any] = {"title": title, "content": html, "original": 1 if original else 0}
        if cvid:
            payload["aid"] = cvid
        if category is not None:
            # 是 `category`，不是 `category_id`——后者会被当成 template_id 悄悄吃掉。
            payload["category"] = category
        if cover:
            payload["banner_url"] = cover
        data = await _post("draft/addupdate", cred, **payload)
        aid = int((data or {}).get("aid") or cvid or 0)
        guard.record("article", params, {"cvid": aid})
        out: dict[str, Any] = {
            "cvid": aid,
            "title": title,
            "next": f"发布：`bili article publish {aid}`",
        }
        if warning:
            out["warning"] = warning
        if category is None:
            out["warning"] = "没有分类，publish 会报 20022。用 `bili article categories` 查一个再补。"
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
            # 没有分类时 submit 必然 20022，提前拦住比让 B 站报错更有用。
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
            "reprint": 0,
            "original": 1 if d.get("original", True) else 0,
        }
        if d.get("banner_url"):
            payload["banner_url"] = d["banner_url"]
        guard.check("article")
        data = await _post_article("article/submit", cred, **payload)
        guard.record("article", params, {"cvid": cvid, "state": (data or {}).get("state")})
        # 发布后文章会拿到一个新的 cvid（草稿 ID 不是它），真值藏在预览链接里。
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
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="新的正文 HTML 文件；`-` 表示 stdin。"),
    content: Optional[str] = typer.Option(None, "--content", help="新的正文 HTML 文本。"),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="新标题；不给就沿用原标题。"),
    cover: Optional[str] = typer.Option(None, "--cover", help="新封面 URL；不给就沿用原封面。"),
    ai: bool = typer.Option(False, "--ai", help="追加 AI 创作声明。"),
    no_ai: bool = typer.Option(False, "--no-ai", help="移除已有的 AI 声明。"),
    ai_client: str = typer.Option("", "--ai-client", help="声明里的客户端。"),
    ai_model: str = typer.Option("", "--ai-model", help="声明里的模型。"),
    ai_agent: str = typer.Option("", "--ai-agent", help="声明里的智能体名。"),
) -> None:
    """原地编辑一篇已发布的文章。会重新进审核，cvid 与动态 ID 不变。"""

    async def body():
        cred = config.require_credential()
        current = await _read_article(cred, cvid)   # 作者侧，审核期也能读
        new_html = _read_body(file, content) if (file or content) else (current.get("content") or "")
        if ai or no_ai:
            new_html = _apply_ai_notice(new_html, ai and not no_ai, ai_client, ai_model, ai_agent)
        if not new_html.strip():
            raise BiliError("invalid_argument", "正文不能为空。")
        new_title = title or current.get("title") or ""
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
            "reprint": 0,
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
            "state": STATES.get((data or {}).get("state"), (data or {}).get("state")),
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
        # 先确认是自己的：文章和草稿走的是两个接口，一个读不到不代表不是你的。
        # 顺带记住它是哪一种——删除也得用对应的那个接口，混用会报 37104。
        found = None
        kind = None
        for reader, k in ((_read_article, "article"), (_read_draft, "draft")):
            try:
                found = await reader(cred, cvid)
            except Exception:
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
        # 草稿和文章是两个删除接口：草稿用 draft/delete，用错会报 37104。
        await _post("draft/delete" if kind == "draft" else "article/delete", cred, aid=cvid)
        guard.record("article", params, "ok")
        return {"cvid": cvid, "deleted": True, "kind": kind, "title": found.get("title")}

    run(body)


# ── show ─────────────────────────────────────────────────────────────────────


@app.command()
def show(
    cvid: int = typer.Argument(..., help="专栏 cvid。"),
    text: bool = typer.Option(False, "--text", help="输出纯文本正文，而不是摘要。"),
) -> None:
    """读一篇专栏：标题、字数、图片数、封面；`--text` 给出正文。"""

    async def body():
        cred = config.require_credential()
        d = await _read_article(cred, cvid)
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

# ── watch ────────────────────────────────────────────────────────────────────


@app.command()
def watch(
    cvid: int = typer.Argument(..., help="要盯的专栏 cvid。"),
    interval: int = typer.Option(30, "--interval", help="轮询间隔（秒）。"),
    timeout: int = typer.Option(600, "--timeout", help="最长等待（秒），0 表示一直等。"),
) -> None:
    """等一篇专栏过审：轮询作者侧状态，通过/驳回/超时再返回。

    状态来自 `x/article/creative/article/view` 的 `state`：-2 审核中、0 已发布、2 未通过。
    这比等私信可靠——B 站不会把过审结果发给作者本人，但状态字段一直可读。
    """

    async def body():
        cred = config.require_credential()
        import time as _t

        started = _t.monotonic()
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
            if state != -2:
                out = {
                    "cvid": cvid,
                    "state": label,
                    "polls": polls,
                    "waited": round(_t.monotonic() - started),
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
            if timeout and (_t.monotonic() - started) >= timeout:
                raise BiliError(
                    "api_error",
                    f"等了 {timeout} 秒还在审核中（state=-2）。",
                    hint=f"稍后再跑 `bili article watch {cvid}`，或用 `bili article show {cvid}` 看一次。",
                    detail={"cvid": cvid, "polls": polls},
                )
            await asyncio.sleep(interval)

    run(body)

# ── embed ────────────────────────────────────────────────────────────────────

BLOCK_END_RE = re.compile(r"</(?:p|h1|figure)>", re.I)


def _find_block_end(html: str, anchor: str) -> int:
    """返回包含 anchor 的块在其结束标签之后的位置。找不到时抛错，不猜。"""
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
    after: str = typer.Option(..., "--after", "-a", help="插在这段文字所在块之后；必须原文一字不差且唯一。"),
    caption: str = typer.Option("", "--caption", "-c", help="图注。"),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="写到这个文件；不给就打印到 stdout 的 data.html。"),
) -> None:
    """上传一张图，并插到正文里指定段落之后，输出新的正文。

    「插在哪」由 `--after` 决定：给一段正文里**独一无二**的文字，工具找到它所在的块
    （`<p>` / `<h1>` / `<figure>`），把图片插在那个块之后。锚点找不到或不唯一都会报错，
    不会退而求其次塞到末尾——猜错位置比失败更糟。
    """

    async def body():
        if not file.is_file():
            raise BiliError("invalid_argument", f"正文文件不存在：{file}")
        if not image.is_file():
            raise BiliError("invalid_argument", f"图片不存在：{image}")
        html = file.read_text(encoding="utf-8")
        pos = _find_block_end(html, after)   # 先定位，锚点有问题就别浪费一次上传
        params = {"file": str(file), "image": str(image), "after": after}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "article.embed", "params": params,
                    "insert_at": pos, "preview": html[:pos] + "\n<!-- 图插在这里 -->\n" + html[pos:]}
        cred = config.require_credential()
        guard.check("article_image")
        data = await _post("article/upcover", cred, cover=_data_uri(image))
        url = str((data or {}).get("url") or "").replace("http://", "https://")
        if not url:
            raise BiliError("api_error", "上传成功但没有拿到图片地址。", detail=data)
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
