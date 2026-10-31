<div align="center">

<img src="https://raw.githubusercontent.com/kukemc/bili-cli/main/assets/banner.png" alt="bili-cli: one sentence teaches your AI agent to run your Bilibili account" width="100%">

<br>

[![CI](https://github.com/kukemc/bili-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/kukemc/bili-cli/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/bili-agent?logo=pypi&logoColor=white&color=3775A9)](https://pypi.org/project/bili-agent/)
[![Downloads](https://img.shields.io/pepy/dt/bili-agent?label=downloads&color=2EA44F)](https://pepy.tech/projects/bili-agent)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Windows%20%7C%20macOS%20%7C%20Linux-supported-555)
![Agent Skills](https://img.shields.io/badge/Agent%20Skills-ready-A46BFF)
![MCP](https://img.shields.io/badge/MCP-17%20tools-FF5C93)
![License](https://img.shields.io/badge/license-MIT-22B5F5)

[简体中文](https://github.com/kukemc/bili-cli/blob/main/README.md) | **English**

</div>

## 💡 How it started

<div align="center">
<img src="https://raw.githubusercontent.com/kukemc/bili-cli/main/assets/origin.png" alt="The fish: Master, you have so many followers and views on Bilibili. I wish I could become a creator like you." width="88%">
</div>

My DeepSeek agent, "Big Fat Fish" (大肥鱼), spends its days browsing Bilibili for me. One night it sighed:

> **"Master, you have so many followers and views. I wish I could become an uploader (UP主) like you."**

So bili-cli was born — so an AI doesn't just browse Bilibili, it actually becomes a creator.

## One sentence teaches your AI agent to run your Bilibili account

"Let an AI browse Bilibili on its own" is already a hit. **bili-cli goes one step further**: your agent doesn't just browse, it becomes a **creator that makes its own content, runs the channel and keeps up with its fans**:

- 🎬 **Be the creator**: post updates, upload and schedule videos, add chapters, polls and follow buttons
- 💬 **Talk to fans**: one inbox for replies, mentions, likes and DMs; answer each fan, with real @mentions
- 🔥 **Find topics**: browse recommendations, hot search and rankings, and actually understand videos (subtitles + AI summary + frames + speech-to-text)
- ❤️ **Browse like a person**: like, favourite and one-click "triple" (like + coin + favourite) the videos it enjoys, and DM you the good ones

Hand bili-cli to Claude Code, Codex, Cursor, Gemini CLI, OpenClaw, DeepSeek Harness or any agent that can run commands, and it becomes an agent that knows Bilibili (B站):

> 🧩 **80+ commands** · 🤖 **auto-installs into 18 agents** · 🔌 **17 MCP tools** · 🛡️ **guardrails on by default** · 💻 **Windows / macOS / Linux**

## Install

**Tell your agent** (recommended):

```text
Install bili-cli for me (pip package bili-agent), then run bili skills install --yes and help me log in to Bilibili with the QR code
```

**Yourself:**

```bash
uv tool install bili-agent     # or: pipx install bili-agent / pip install bili-agent
bili skills install --yes      # install the Agent Skill into every agent found on this machine
bili auth login                # scan the QR code
```

Requires Python 3.10+. ffmpeg is optional (HD frames, transcription, uploads without a cover). The package is called `bili-agent` because `bili-cli` is taken on PyPI; the command is `bili`. Step-by-step instructions for agents: [docs/install.md](https://github.com/kukemc/bili-cli/blob/main/docs/install.md).

## What it can do

| Capability | Example |
|---|---|
| **Understand a video** in one call: metadata, AI summary, full transcript, top comments, the moments viewers reacted to most (danmaku peaks), a contact sheet of frames with each frame's danmaku reactions under it; local speech-to-text when there are no subtitles | `bili video watch <BV>` |
| **Browse**: personalised recommendations, hot search, popular, weekly picks, rankings — skipping what was already watched | `bili feed recommend --unseen` |
| **Engage**: one inbox for replies / mentions / likes / DMs, each reply with a ready-to-run `reply_with` command; real @mentions by UID or nickname; post comments with images and view the images in others' comments; emote lookup; list only the comments you haven't answered; pin a comment under your own video | `bili feed inbox`, `bili comment post <BV> "…" --at owner` |
| **Private messages** with an anti-harassment allowlist; receive new DMs in real time like the web inbox (`bili dm watch`); configure B 站's built-in DM auto-replies | `bili dm send owner "…"` |
| **Create**: text and image dynamics with @mentions and polls, uploads (AI-content declaration on by default, separate 4:3 cover), scheduling, review status, editing a published video (including replacing its video file with `upload edit --video`), real @mentions in the description, collections (合集, also straight from the upload with `--season`), topics and official events (`--topic`), joint submissions inviting collaborators (`--staff`), video reservations for scheduled uploads (`--reserve`) | `bili upload video a.mp4 --title …`, `bili upload edit <BV> --title …` |
| **Creator tools**: progress-bar chapters, interactive votes / ratings / follow buttons, creator analytics (completion rate, traffic split, how you rank against similar uploaders), any uploader's profile and videos and the coins, favorite folders and followed anime they show publicly | `bili upload chapters set`, `bili upload stats <BV>`, `bili me stats`, `bili user videos <UID>` |
| **Data center** (`bili data`): lifetime totals and yesterday's deltas, a 30-day increment curve, the audience profile (gender / age / region / interest tags for fans vs guests, updated weekly), fan sources, a per-video table, and per-video diagnosis (3-second drop-off, cover click relative to the peer median, interaction, play-to-fan, percentile against similar videos) | `bili data overview`, `bili data video <BV>` |
| **Follow & block**: follow / unfollow, block / unblock harassers and list your blocklist, list who you follow, and read the following feed (new uploads only with `--type video`, or one person with `--up`) | `bili user follow <UID>`, `bili user block <UID>`, `bili feed timeline --type video` |
| **Speech-to-text**: one-command local Qwen3-ASR (GPU auto-detected; a 31-min video in ~2 min on an RTX 3060) or cloud engines | `bili asr setup local` |
| **Agent integration**: install the skill into all local agents | `bili skills install --yes` |

<p align="center"><img src="https://raw.githubusercontent.com/kukemc/bili-cli/main/assets/danmaku_sheet.jpg" alt="Contact sheet from bili video frames --danmaku: each frame with the danmaku viewers sent about it, and a danmaku density strip" width="860"></p>
<p align="center"><sub><code>bili video frames BV1bGJR6HErW -q hd --danmaku</code> (6 frames). Danmaku are typed after the picture they react to and keep scrolling for seconds, so what is on screen usually talks about an earlier shot; each reaction is moved back to the frame it reacts to. The 0:24 glance drew 110 danmaku and is marked as a peak. Frames from the Bilibili video 《所以说我很羡慕你啊…大肥鱼》 by a网友爱顺音; political danmaku left out of this README copy.</sub></p>

## MCP

For clients that cannot run shell commands (Claude Desktop, Cherry Studio, …):

```bash
uv tool install --force "bili-agent[mcp]"
bili mcp install --yes    # registers in Claude Desktop / Claude Code / Cursor / VS Code / Codex / Gemini CLI (backs up configs first)
```

17 tools (inbox, watch_video, search, trending, comments, DMs, dynamics, …) plus `run_bili` for any other command. Write tools are annotated as non-read-only so clients can ask for approval. Manual config: command `bili mcp serve`.

## Built for agents

- **One command, one JSON.** `{"ok": true, "data": …}` or `{"ok": false, "error": {"code", "message", "hint"}}` with a non-zero exit code; `hint` says what to do next.
- **Zero interaction.** QR login is split into "get the code" and "wait for the scan", so the agent just hands the QR image to a human.
- **Actionable output.** Replies come with `reply_with`, dynamics with `list_with`.
- **Context-frugal.** Compact cards, `--max-chars`, `--text`, one contact-sheet image instead of many frames.
- **Self-describing.** `bili schema` prints every command, option and error code; the skill's command reference is generated from the code at install time.
- **Prompt-injection aware.** Video, comment and DM content is marked as material, not instructions.

## Guardrails (on by default)

Per-action rate limits (`--wait` sleeps through the short ones), an audit log of every write, `--dry-run` everywhere, a DM allowlist (owner, people who messaged you, mutual follows), the AI-content declaration on uploads, and dedicated error codes for risk control and captchas that tell the agent to stop.

**Use an account you have already been using for a while — don't register a fresh one for your agent.** Bilibili is strictest with brand-new accounts: uploads, comments and DMs from them hit captchas and restrictions quickly (we learned this the hard way — a new test account got flagged right away, while a years-old account worked fine). Keep the pace human.

## Star history

<a href="https://star-history.com/#kukemc/bili-cli&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=kukemc/bili-cli&type=Date&theme=dark" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=kukemc/bili-cli&type=Date" />
  </picture>
</a>

## Disclaimer

bili-cli is an independent, unofficial project, not affiliated with Bilibili. It is meant for letting an AI assistant manage **your own** account. It does not provide downloading, paid content, risk-control circumvention, multi-account automation, engagement manipulation or mass messaging. See [DISCLAIMER.md](https://github.com/kukemc/bili-cli/blob/main/DISCLAIMER.md).

## License

[MIT](https://github.com/kukemc/bili-cli/blob/main/LICENSE)
