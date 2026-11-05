"""Entry point: the `bili` command."""

from __future__ import annotations

import sys
from typing import Any, Optional

import typer

try:  # typer >= 0.20 vendors its own copy of click
    from typer._click.exceptions import UsageError
except ImportError:  # pragma: no cover - older typer depends on click directly
    from click.exceptions import UsageError

from . import __version__, output
from .commands import article, asr, auth, comment, danmaku, data, dm, dynamic, feed, hot, mcp, me, owner, search, season, skills, upload, user, video
from .errors import ERROR_TABLE, BiliError
from .runtime import run

app = typer.Typer(
    name="bili",
    help=(
        "哔哩哔哩命令行工具，为 AI Agent 设计。\n\n"
        "stdout 永远是一个 JSON：成功 {\"ok\": true, \"data\": ...}，"
        "失败 {\"ok\": false, \"error\": {\"code\", \"message\", \"hint\"}}，退出码非 0。"
        "运行 `bili schema` 获取全部命令的机器可读描述。"
    ),
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    add_completion=False,
)
app.add_typer(auth.app, name="auth")
app.add_typer(video.app, name="video")
app.add_typer(comment.app, name="comment")
app.add_typer(search.app, name="search")
app.add_typer(user.app, name="user")
app.add_typer(feed.app, name="feed")
app.add_typer(danmaku.app, name="danmaku")
app.add_typer(me.app, name="me")
app.add_typer(dynamic.app, name="dynamic")
app.add_typer(upload.app, name="upload")
app.add_typer(season.app, name="season")
app.add_typer(asr.app, name="asr")
app.add_typer(hot.app, name="hot")
app.add_typer(dm.app, name="dm")
app.add_typer(owner.app, name="owner")
app.add_typer(skills.app, name="skills")
app.add_typer(mcp.app, name="mcp")
app.add_typer(data.app, name="data")
app.add_typer(article.app, name="article")

# Global flags may appear anywhere on the command line (agents tend to append
# them at the end); main() moves them in front of the subcommand.
GLOBAL_FLAGS = ("--pretty", "--compact", "--raw", "--dry-run", "--wait")


def _version(value: bool) -> None:
    if value:
        output.success({"version": __version__})
        raise typer.Exit()


@app.callback()
def root(
    pretty: Optional[bool] = typer.Option(
        None, "--pretty/--compact", help="JSON 是否缩进；默认在终端里缩进、在管道里紧凑。"
    ),
    raw: bool = typer.Option(False, "--raw", help="返回 B 站接口的原始数据，而不是精简后的结构。"),
    dry_run: bool = typer.Option(False, "--dry-run", help="写操作只返回将要执行的请求，不真正执行。"),
    wait: bool = typer.Option(False, "--wait", help="写操作碰到 60 秒内能恢复的本地限流时自动等待，而不是报错。"),
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True, help="显示版本。"),
) -> None:
    output.settings.pretty = pretty
    output.settings.raw = raw
    output.settings.dry_run = dry_run
    output.settings.wait = wait


def _describe_params(params: list) -> list[dict[str, Any]]:
    out = []
    for p in params:
        if p.name == "help":
            continue
        is_arg = p.param_type_name == "argument"
        out.append(
            {
                "name": p.name,
                "kind": "argument" if is_arg else "option",
                "flags": None if is_arg else list(p.opts) + list(p.secondary_opts),
                "type": p.type.name,
                "required": p.required,
                "default": None if callable(p.default) else p.default,
                "help": getattr(p, "help", None),
            }
        )
    return out


def _describe(cmd: Any, path: list[str]) -> list[dict[str, Any]]:
    if hasattr(cmd, "commands"):
        out: list[dict[str, Any]] = []
        for name, sub in sorted(cmd.commands.items()):
            out.extend(_describe(sub, path + [name]))
        return out
    return [
        {
            "command": " ".join(["bili"] + path),
            "help": (cmd.help or "").strip(),
            "params": _describe_params(cmd.params),
        }
    ]


@app.command()
def schema() -> None:
    """输出所有命令、参数与错误码的机器可读描述（JSON）。"""

    async def body():
        group = typer.main.get_command(app)
        return {
            "version": __version__,
            "output": {
                "success": {"ok": True, "data": "<command specific>"},
                "failure": {"ok": False, "error": {"code": "str", "message": "str", "hint": "str?", "detail": "obj?"}},
            },
            "global_options": _describe_params(group.params),
            "commands": _describe(group, []),
            "errors": [{"code": c, "exit_code": e, "hint": h} for c, (e, h) in ERROR_TABLE.items()],
        }

    run(body)


def _hoist_global_flags(argv: list[str]) -> list[str]:
    head, rest = [], []
    for i, arg in enumerate(argv):
        if arg == "--":
            rest.extend(argv[i:])
            break
        # `auth status --wait 120` has its own --wait that takes seconds; leave that one to the subcommand
        own_value = arg == "--wait" and i + 1 < len(argv) and argv[i + 1].isdigit()
        (head if arg in GLOBAL_FLAGS and not own_value else rest).append(arg)
    return head + rest


def main() -> None:
    try:
        rc = app(args=_hoist_global_flags(sys.argv[1:]), prog_name="bili", standalone_mode=False)
    except UsageError as exc:
        if type(exc).__name__ == "NoArgsIsHelpError" and exc.ctx is not None:
            # `bili` / `bili video` without a subcommand: show help, not an error.
            sys.stdout.write(exc.ctx.get_help() + "\n")
            sys.exit(0)
        try:
            output.fail(BiliError("invalid_argument", exc.format_message()))
        except typer.Exit as exit_exc:
            sys.exit(exit_exc.exit_code)
    except typer.Exit as exc:
        sys.exit(exc.exit_code)
    except typer.Abort:
        sys.exit(130)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
