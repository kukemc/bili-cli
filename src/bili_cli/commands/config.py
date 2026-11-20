"""bili config: user preferences. The human decides; the AI/model must not change them."""

from __future__ import annotations

from typing import Optional

import typer

from .. import output
from ..runtime import run

app = typer.Typer(help="用户设置：拾取模式与来源由人类决定，AI / 模型不得自行修改。", no_args_is_help=True)


@app.command("owner-prompt")
def owner_prompt(
    mode: Optional[str] = typer.Option(None, "--mode", help="发布者提示词模式：ignore（无视，默认）/ relaxed（轻松）/ strict（严格）。"),
    sources: Optional[str] = typer.Option(
        None,
        "--sources",
        help="拾取来源，逗号分隔：description（视频简介）/ first_frame（第一帧）/ last_frame（最后一帧）/ any_frame（任意帧采样）/ subtitle（CC字幕）/ owner_comment（发布者的评论）。",
    ),
) -> None:
    """查看或设置“发布者提示词”的拾取模式与来源。两个参数都不传时显示当前设置。"""

    from .. import owner_prompt as op

    if mode is None and sources is None:
        output.success(op.load())
        return

    async def body():
        return op.update(mode=mode, sources=op.parse_sources(sources))

    run(body)
