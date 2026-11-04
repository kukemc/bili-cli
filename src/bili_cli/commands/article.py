"""bili article: write a 专栏 (column article) — draft, upload images, publish, edit, delete, read.

The creative-center article API is not wrapped by `bilibili-api`, so these are the same requests the
web editor makes. Notes that cost us a few rounds of debugging:

* The category field is **`category`**, not `category_id` — the latter is silently stored as
  `template_id`, and the article then shows up with no category.
* `submit` validates field by field and rejects a bare `aid`: `title`, `content`, `category`,
  `reprint` must all be present, or you get 20022 / 20023 / 20027 / 20028 in that order.
* Uploading an image wants a **`data:` URI**, not raw base64 (`-400` 请求错误 otherwise).
* Editing a published article goes through `article/update`; `submit` would create a new one.
  `update` re-enters review, so the article is briefly `in_audit` and the public view API answers 404.
"""

from __future__ import annotations

import re

import base64
import mimetypes
from pathlib import Path
from typing import Any, Optional

import typer
from bilibili_api.utils.network import Api

from .. import config, guard, output
from ..errors import BiliError
from ..runtime import iso_time, run

app = typer.Typer(help="专栏：建草稿、传图、发布、编辑、删除、读取正文。", no_args_is_help=True)

BASE = "https://api.bilibili.com/x/article/creative"
VIEW = "https://api.bilibili.com/x/article/view"

# 内容状态。审核期间公开接口返回 -404，但作者侧仍能读。
STATES = {-2: "审核中", 0: "已发布", 1: "待审核", 2: "未通过"}

# 专栏编辑器的标签白名单（实测）。不在名单里的标签（例如 <pre> / <code>）会被降级成普通文本，
# 代码块的换行与对齐都会丢失——代码请用 `bili article image` 传图后在正文里引用 URL。
ALLOWED_TAGS = ("p", "span", "br", "h1", "figure", "img", "figcaption", "a")


def _api(path: str, method: str = "POST") -> Api:
    return Api(url=f"{BASE}/{path}", method=method, verify=True, no_csrf=True)


async def _call(path: str, cred, *, method: str = "POST", params: dict[str, Any] | None = None) -> Any:
    """One creative-center call, csrf attached. `t` is not passed so the response is JSON."""
    api = _api(path, method).update_params(csrf=cred.bili_jct, t=0)
    if params:
        api = api.update_params(**params)
    return await api.result


async def _read_draft(cred, cvid: int) -> dict[str, Any]:
    api = Api(url=f"{BASE}/draft/view", method="GET", verify=True, credential=cred).update_params(aid=cvid)
    return await api.result


def _summary(cvid: int, d: dict[str, Any]) -> dict[str, Any]:
    html = d.get("content") or ""
    return {
        "cvid": cvid,
        "title": d.get("title"),
        "category": (d.get("category") or {}).get("id") or None,
        "cover": d.get("banner_url") or None,
        "chars": len(_plain(html)),
        "images": html.count("<img"),
        "editable": bool(d.get("editable", True)),
        "url": f"https://www.bilibili.com/read/cv{cvid}",
    }


def _plain(html: str) -> str:
    """Rough text length; the website counts differently, this is only for a size hint."""
    import re

    return re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", html))


def _check_tags(html: str) -> list[str]:
    """Return tags used in the body that the editor will not keep as-is."""
    import re

    used = {m.group(1).lower() for m in re.finditer(r"<\s*([a-z0-9]+)", html, re.I)}
    return sorted(used - set(ALLOWED_TAGS))


def _read_body(file: Optional[Path], text: Optional[str]) -> str:
    if bool(file) == bool(text):
        raise BiliError("invalid_argument", "正文只能给一个：`--file` 或 `--content`。")
    if file:
        if str(file) == "-":
            import sys

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


# ── image ────────────────────────────────────────────────────────────────────


@app.command()
def image(
    path: Path = typer.Argument(..., help="本地图片路径（png / jpg）。"),
) -> None:
    """上传一张正文配图，返回可直接写进 <img src> 的 URL。"""

    async def body():
        if not path.is_file():
            raise BiliError("invalid_argument", f"图片不存在：{path}")
        params = {"file": str(path), "size": path.stat().st_size}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "article.image", "params": params}
        cred = config.require_credential()
        guard.check("article")
        data = await _call("article/upcover", cred, params={"cover": _data_uri(path)})
        url = str(data.get("url") or data.get("image_url") or "")
        if not url:
            raise BiliError("api_error", "上传成功但没有拿到图片地址。", detail=data)
        url = url.replace("http://", "https://")
        guard.record("article", params, {"url": url})
        return {"url": url, "next": f"正文里写 `<img src=\"{url}\">`，或先 `bili article draft` 再放进去。"}

    run(body)


# ── draft ────────────────────────────────────────────────────────────────────


@app.command()
def draft(
    title: str = typer.Option(..., "--title", "-t", help="标题，40 字以内。"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="正文 HTML 文件；`-` 表示 stdin。"),
    content: Optional[str] = typer.Option(None, "--content", help="正文 HTML 文本（与 --file 二选一）。"),
    category: Optional[int] = typer.Option(None, "--category", help="分类 ID（二级分类）；不填则未分类。"),
    cover: Optional[str] = typer.Option(None, "--cover", help="封面图 URL（用 `bili article image` 得到）。"),
    cvid: Optional[int] = typer.Option(None, "--cvid", help="给了就更新这个草稿，否则新建。"),
) -> None:
    """新建一个草稿（或更新已有草稿）。发布用 `bili article publish`。"""

    async def body():
        if len(title) > 40:
            raise BiliError("invalid_argument", "标题不能超过 40 字。")
        html = _read_body(file, content)
        if not html.strip():
            raise BiliError("invalid_argument", "正文不能为空。")
        dropped = _check_tags(html)
        params: dict[str, Any] = {"cvid": cvid, "title": title, "category": category, "cover": bool(cover), "chars": len(_plain(html))}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "article.draft", "params": params, "dropped_tags": dropped}
        cred = config.require_credential()
        guard.check("article")
        payload: dict[str, Any] = {"title": title, "content": html}
        if cvid:
            payload["aid"] = cvid
        if category is not None:
            # 是 `category`，不是 `category_id`——后者会被当成 template_id 悄悄吃掉。
            payload["category"] = category
        if cover:
            payload["banner_url"] = cover
        data = await _call("draft/addupdate", cred, params=payload)
        aid = int(data.get("aid") or cvid or 0)
        guard.record("article", params, {"cvid": aid})
        out: dict[str, Any] = {
            "cvid": aid,
            "title": title,
            "next": f"发布：`bili article publish {aid}`",
        }
        if dropped:
            out["dropped_tags"] = dropped
            out["note"] = "这些标签专栏编辑器不会保留，会被降级成普通文本。代码块请改用图片。"
        return out

    run(body)


# ── publish ──────────────────────────────────────────────────────────────────


@app.command()
def publish(
    cvid: int = typer.Argument(..., help="草稿的 cvid。"),
    category: Optional[int] = typer.Option(None, "--category", help="分类 ID；不给就用草稿里已有的。"),
) -> None:
    """把草稿提交发布（会进审核）。"""

    async def body():
        cred = config.require_credential()
        d = await _read_draft(cred, cvid)
        html = d.get("content") or ""
        if not html.strip():
            raise BiliError("not_found", f"草稿 {cvid} 没有正文，或它不属于你。")
        cat = category if category is not None else ((d.get("category") or {}).get("id") or None)
        params = {"cvid": cvid, "title": d.get("title"), "category": cat}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "article.publish", "params": params}
        # submit 逐字段校验，缺一个就报一个错码，所以这里把必需的都带上。
        payload: dict[str, Any] = {
            "aid": cvid,
            "title": d.get("title") or "",
            "content": html,
            "reprint": 0,
            "original": 1,
        }
        if cat:
            payload["category"] = cat
        if d.get("banner_url"):
            payload["banner_url"] = d["banner_url"]
        guard.check("article")
        data = await _call("article/submit", cred, params=payload)
        guard.record("article", params, {"cvid": cvid, "state": data.get("state")})
        return {
            "cvid": cvid,
            "state": STATES.get(data.get("state"), data.get("state")),
            "preview": data.get("view_url"),
            "next": "审核期间公开链接会返回 404，属正常；过审后用 `bili article show` 确认。",
        }

    run(body)


# ── update ───────────────────────────────────────────────────────────────────


@app.command()
def update(
    cvid: int = typer.Argument(..., help="已发布文章的 cvid。"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="新的正文 HTML 文件；`-` 表示 stdin。"),
    content: Optional[str] = typer.Option(None, "--content", help="新的正文 HTML 文本。"),
    title: Optional[str] = typer.Option(None, "--title", "-t", help="新标题；不给就沿用原标题。"),
    cover: Optional[str] = typer.Option(None, "--cover", help="新封面 URL；不给就沿用原封面。"),
) -> None:
    """原地编辑一篇已发布的文章。会重新进审核，cvid 与动态 ID 不变。"""

    async def body():
        cred = config.require_credential()
        current = await _view(cvid)
        new_html = _read_body(file, content) if (file or content) else (current.get("content") or "")
        if not new_html.strip():
            raise BiliError("invalid_argument", "正文不能为空。")
        new_title = title or current.get("title") or ""
        if len(new_title) > 40:
            raise BiliError("invalid_argument", "标题不能超过 40 字。")
        cat = (current.get("category") or {}).get("id") or None
        params = {"cvid": cvid, "title": new_title, "category": cat, "chars": len(_plain(new_html))}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "article.update", "params": params, "dropped_tags": _check_tags(new_html)}
        payload: dict[str, Any] = {
            "aid": cvid,
            "title": new_title,
            "content": new_html,
            "reprint": 0,
            "original": 1,
        }
        if cat:
            payload["category"] = cat
        if cover or current.get("banner_url"):
            payload["banner_url"] = cover or current["banner_url"]
        guard.check("article")
        data = await _call("article/update", cred, params=payload)
        guard.record("article", params, {"cvid": cvid})
        return {
            "cvid": cvid,
            "state": STATES.get(data.get("state"), data.get("state")),
            "next": "编辑会重新进审核；审核期间 `bili article show` 可能读不到，等一会儿再试。",
        }

    run(body)


# ── delete ───────────────────────────────────────────────────────────────────


@app.command()
def delete(
    cvid: int = typer.Argument(..., help="要删除的 cvid（草稿或已发布都行）。"),
) -> None:
    """删除一篇自己的专栏。不可撤销。"""

    async def body():
        params = {"cvid": cvid}
        if output.settings.dry_run:
            return {"dry_run": True, "action": "article.delete", "params": params}
        cred = config.require_credential()
        guard.check("article")
        await _call("article/delete", cred, params={"aid": cvid})
        guard.record("article", params, "ok")
        return {"cvid": cvid, "deleted": True}

    run(body)


# ── show ─────────────────────────────────────────────────────────────────────


async def _view(cvid: int) -> dict[str, Any]:
    api = Api(url=VIEW, method="GET").update_params(id=cvid)
    try:
        return await api.result
    except Exception as exc:  # -404 在审核期间是常态
        raise BiliError(
            "not_found",
            f"读不到专栏 {cvid}。",
            hint="刚发布或刚编辑的文章在审核期间会返回 404；稍后再试。",
        ) from exc


@app.command()
def show(
    cvid: int = typer.Argument(..., help="专栏 cvid。"),
    text: bool = typer.Option(False, "--text", help="输出纯文本正文，而不是摘要。"),
) -> None:
    """读一篇专栏：标题、字数、图片数、封面；`--text` 给出正文。"""

    async def body():
        d = await _view(cvid)
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
            "tags": sorted({m.group(1).lower() for m in re.finditer(r"<\s*([a-z0-9]+)", html, re.I)}),
            "published": iso_time(d.get("publish_time")),
            "url": f"https://www.bilibili.com/read/cv{cvid}",
        }

    run(body)


def _plain_keep_lines(html: str) -> str:
    import re

    s = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    s = re.sub(r"</p>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()
