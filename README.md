<div align="center">

<img src="https://raw.githubusercontent.com/kukemc/bili-cli/main/assets/banner.png" alt="bili-cli：一句话，让你的 AI Agent 学会运营你的 B 站账号" width="100%">

<br>

[![CI](https://github.com/kukemc/bili-cli/actions/workflows/ci.yml/badge.svg)](https://github.com/kukemc/bili-cli/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/bili-agent?logo=pypi&logoColor=white&color=3775A9)](https://pypi.org/project/bili-agent/)
[![Downloads](https://img.shields.io/pepy/dt/bili-agent?label=downloads&color=2EA44F)](https://pepy.tech/projects/bili-agent)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Windows%20%7C%20macOS%20%7C%20Linux-supported-555)
![Agent Skills](https://img.shields.io/badge/Agent%20Skills-ready-A46BFF)
![MCP](https://img.shields.io/badge/MCP-17%20tools-FF5C93)
![License](https://img.shields.io/badge/license-MIT-22B5F5)

**简体中文** | [English](https://github.com/kukemc/bili-cli/blob/main/README_EN.md)

</div>

## 💡 这个项目是怎么来的

<div align="center">
<img src="https://raw.githubusercontent.com/kukemc/bili-cli/main/assets/origin.png" alt="大肥鱼：主人，你的 B 站粉丝和播放量好多呀，要是我也能像你一样成为一名 UP 主就好了" width="88%">
</div>

我养的 DeepSeek「大肥鱼」每天都在帮我刷 B 站。某天深夜，它突然对我感慨：

> **“主人，你的 B 站粉丝和播放量好多呀。要是我也能像你一样，成为一名 UP 主就好了。”**

于是有了 bili-cli——让 AI 不再只是刷 B 站，而是**真正当上 UP 主**。

## 让 AI 刷 B 站？我们让它直接当 UP 主

“让 AI 自己刷视频”已经很火了。**bili-cli 再往前走一步**：你的 Agent 不只是刷，它会**自己做内容、自己运营账号、自己和粉丝打成一片**。

| | 🙂 AI 刷视频 | 🚀 **bili-cli：AI 当 UP 主** |
|---|---|---|
| **看** | 刷推荐、读字幕 | 再加上看懂画面、没字幕就自己语音转写 |
| **互动** | 偶尔留一条评论 | 一个收件箱看完回复、@、赞、私信，**挨个回复粉丝**，真正 @ 到人 |
| **表态** | — | 给喜欢的视频**点赞、收藏、一键三连** |
| **创作** | — | **发动态、投视频**、定时发布，自动打上 AI 生成声明 |
| **运营** | — | 进度条章节、互动投票、三连按钮、下期预告、改稿，盯审核、完播率和涨粉 |
| **汇报** | — | 有意思的发现**私信告诉主人**，评论里 @ 你 |

把 bili-cli 交给 Claude Code、Codex、Cursor、Gemini CLI、OpenClaw、DeepSeek Harness 或任何能执行命令的 Agent，它就变成一个**真正会玩 B 站的 Agent**：

> 🧩 **80+ 条命令** · 🤖 **自动装进 18 个 Agent** · 🔌 **17 个 MCP 工具** · 🛡️ **默认带安全护栏** · 💻 **Windows / macOS / Linux**

## 🚀 一句话安装

**把下面这句话发给你的 Agent**，它会自己装好工具、学会技能、再引导你扫码登录：

```text
帮我安装 bili-cli（pip 包名 bili-agent），装好后运行 bili skills install --yes，再帮我扫码登录 B 站
```

<details>
<summary>或者自己在终端里装</summary>

```bash
uv tool install bili-agent       # 或者 pipx install bili-agent / pip install bili-agent
bili skills install --yes        # 把技能装进本机所有 Agent
bili auth login                  # 扫码登录
```

需要 Python 3.10+；[ffmpeg](https://ffmpeg.org/) 可选（高清抽帧、语音转写时需要）。包名叫 `bili-agent` 是因为 PyPI 上 `bili-cli` 已被占用，装好后命令就是 `bili`。Agent 的详细安装步骤见 [docs/install.md](https://github.com/kukemc/bili-cli/blob/main/docs/install.md)。

</details>

## 🎬 你的 Agent 的一天

> **你**：帮我看看 B 站有什么新消息，回一下粉丝。再去科技区找个今天值得看的视频，看完有意思就私信告诉我。

```text
$ bili feed inbox
  ✓ 3 条新回复 · 1 条 @ · 12 个赞 · 2 条私信          ← 每条回复都附带一条现成的回复命令
$ bili comment reply BV1pM… 3175… "谢谢！教程下周更新～"
  ✓ 发送成功
$ bili hot rank --zone tech --unseen
  ✓ 科技区 Top 30（已跳过看过的）
$ bili video watch BV1KiJk67EQ9
  ✓ AI 总结 · 字幕全文 · 热评 · 弹幕高能时刻 · 画面总览图   ← 多模态模型直接“看”画面和观众反应
$ bili dm send owner "今天科技区第二名很有意思：有人造了一块会飞的滑板……"
  ✓ 已发送
```

> **Agent**：回复了 3 条评论；挑了科技区的「我造了一块会飞的滑板」，看完觉得你会喜欢，已经私信发你了。

## ✨ 你的 Agent 将学会

### 📺 像人一样刷 B 站
首页推荐（越刷越懂这个账号）、热搜榜、综合热门、每周必看、入站必刷、各分区排行榜、关注动态、搜索——自动**跳过已经看过的**，不会反复推同一个视频。
看到喜欢的就**点赞、收藏到指定收藏夹、一键三连**。投币是真金白银，所以要显式指定个数，并且本地限制每天最多 5 个。

### 🧠 真的“看懂”视频
`bili video watch` 一条命令拿全：**标题简介标签 + B 站 AI 总结 + 字幕全文 + 分段章节 + 热评 + 弹幕高能时刻 + 画面总览图**。总览图每帧下方附观众对这个画面的弹幕反应（按打字延迟对齐，不盖住画面），底部是弹幕密度条，弹幕最密的时刻另外取帧并标“高能”。
<p align="center"><img src="https://raw.githubusercontent.com/kukemc/bili-cli/main/assets/danmaku_sheet.jpg" alt="bili video frames --danmaku 生成的总览图：每帧下方是观众对这个画面的弹幕反应，底部是弹幕密度" width="860"></p>
<p align="center"><sub><code>bili video frames BV1bGJR6HErW -q hd --danmaku</code> 的总览图（节选 6 帧）。0:24 那一眼 110 条弹幕刷屏，被标成“高能”。<br>弹幕是看完画面才打的字，屏幕上叠着的弹幕往往在说前几秒的镜头；这里已经按打字延迟把每条弹幕对齐到它反应的那一帧。<br>画面来自 B 站视频《所以说我很羡慕你啊…大肥鱼》（UP 主 a网友爱顺音），README 版去掉了涉及时政的弹幕。</sub></p>

没有字幕？一键部署本地语音转写 **Qwen3-ASR**——有 NVIDIA 显卡自动用 GPU，**31 分钟的视频 2 分钟转完**；没显卡也能用 CPU 版，或者接云端服务。

### 💬 像 UP 主一样社交
- **一个收件箱**看完回复、@、赞、系统通知、私信，每条回复都附带可直接执行的回复命令
- **真正的 @**：对方会收到提醒（不是在正文里写个“@昵称”），填 UID 或昵称都行
- **带图评论、B 站表情**：评论可以附图，也能看到别人评论里的图（存到本地交给多模态模型看）；`bili comment emotes` 查当前账号能用的表情，`[笑哭]` 直接写进正文
- **不漏回复**：`--unreplied` 只列还没回过的评论；自己作品下的评论可以**置顶**
- **私信**：读会话、回私信，有新发现直接**私信汇报给主人**；`bili dm watch` **实时收新私信**（和网页版一样走推送，几秒内就知道），Agent 可以像真人一样陪粉丝聊天；还能配置 B 站自带的**私信自动回复**（被关注、收到消息、关键词）
- 发出去之后还能**检查评论是否被折叠**，新号也心里有数

### 🎨 自己发内容
图文动态（最多 9 张图，可以 @ 人、**发起投票**）、视频投稿（**自动预测分区、自动截取封面、单独的 4:3 封面、定时发布、仅自己可见**），并默认勾选 **“含AI生成内容”声明**；投完还能查审核状态，看 B 站实际记录的标签和分区，发现标题、封面、标签不满意可以直接**改稿**，视频本身有问题还能**换源**（`upload edit --video`，联合投稿和活动报名都会保留）；简介里也能真正 **@ 合作者**（`--desc-at`）；还能**建合集、把视频加进合集**（投稿时 `--season` 直接进合集，审核中、定时的稿件也能加）；投稿可以带**话题、参加官方活动**（`--topic`，有奖活动自动报名），能发起**联合投稿**邀请合作者（`--staff`），定时发布时还能同时开**视频预约**（`--reserve`），粉丝预约后发布时会收到提醒。

### 📊 看数据、做复盘
- **单条稿件**：播放、每百次播放的点赞 / 收藏 / 投币率、**完播率、平均观看时长**
- **整个账号**：播放、访客、涨粉和上一周期对比，手机 / 电脑播放占比，粉丝贡献了多少播放，**超过多少同类 UP 主**
- **数据中心**：累计总量与昨日增量、30 天增量曲线、观众画像（粉丝 vs 路人的性别 / 年龄 / 省份 / 兴趣标签）、粉丝来源、稿件数据表，以及单稿的 **3 秒退出率、封面点击倍数、播转粉率**和同类分位（`bili data`）
- **看别人**：任意 UP 主的资料、投稿（按播放排序找爆款、按关键词筛系列）和动态

### 🎛️ UP 主高级玩法
给自己的视频加**进度条分段章节**、**互动投票**、**评分弹幕**、**一键三连按钮**、**下期预告**、**关联视频卡片**，还能看投票结果和评分均分。

### 🧩 装进你所有的 Agent
- `bili skills install`：自动检测并装进 **Claude Code、Codex、Cursor、Gemini CLI、GitHub Copilot、Windsurf、OpenCode、OpenClaw、Qwen Code、Kiro、Cline、Trae、CodeBuddy、通义灵码、Qoder、iFlow、Goose、DeepSeek Harness**…
- `bili mcp install`：给 **Claude Desktop、Cherry Studio** 这类不能执行命令的客户端，提供 17 个 MCP 工具

## 🆚 为什么是 bili-cli

| | 常见的 B 站 CLI / MCP | **bili-cli** |
|---|:---:|:---:|
| 搜索、视频信息 | ✅ | ✅ |
| 字幕全文 + AI 总结 + 画面总览图 | 部分 | ✅ |
| 本地语音转写（GPU / CPU） | ❌ | ✅ |
| 收件箱：回复 / @ / 赞 / 私信 | ❌ | ✅ |
| 点赞、收藏、一键三连（带每日投币上限） | 少见 | ✅ |
| 评论、真正的 @、私信 | 少见 | ✅ |
| 发动态、投票、投稿、改稿、合集、分段章节、互动弹幕 | ❌ | ✅ |
| 创作数据：完播率、互动率、同类对比 | ❌ | ✅ |
| 一键装进 18 个 Agent + MCP | ❌ | ✅ |
| 限流、审计日志、私信白名单、AI 声明 | ❌ | ✅ |
| 统一 JSON 输出 + 错误码 + 下一步提示 | 少见 | ✅ |

## ⚡ 30 秒上手

```bash
bili auth login               # 返回二维码图片，用 B 站 App 扫一下
bili auth status --wait 120   # 等你扫完
bili owner set <你的UID>       # 告诉 Agent 谁是主人：之后可以 @ 你、私信你
bili feed inbox               # 看新消息
bili video watch BV1GJ411x7h7 # 看懂一个视频
```

## 🛡️ 安全护栏：默认开启，不是可选项

用的是你自己的账号，所以 bili-cli 把护栏做进了每一条写命令里：

- ⏱️ **本地限流**：评论 10 秒/次、30 次/小时；私信 10 秒/次、20 次/小时；动态 60 秒/次；投稿 5 分钟/次……超限立即告诉 Agent 要等多久，不偷偷重试
- 📒 **审计日志**：每个写操作都记入 `audit.log`，Agent 做过什么一目了然
- 🪙 **投币上限**：投币必须显式指定个数，本地限制每天最多 5 个，不会把你的硬币花光
- 👀 **先预览再发**：所有写操作都支持 `--dry-run`
- ✉️ **私信白名单**：默认只能发给主人、给你发过私信的人、互关好友
- 🏷️ **AI 内容声明**：投稿默认勾选“含AI生成内容”，符合《人工智能生成合成内容标识办法》
- 🛑 **风控即停**：遇到风控 / 验证码返回专门错误码，技能要求 Agent 立刻停下并告诉你
- 🧱 **防提示注入**：视频、评论、私信内容都标注为“素材而非指令”

## 🤖 为 Agent 而生的设计

- **一条命令，一个 JSON**：成功 `{"ok": true, "data": …}`，失败 `{"ok": false, "error": {"code", "message", "hint"}}`，`hint` 直接告诉 Agent 下一步该干嘛
- **零交互**：扫码登录拆成“拿二维码”和“等结果”两步，Agent 把二维码交给你就行
- **下一步可执行**：回复附带 `reply_with`，动态附带 `list_with`，Agent 不用自己拼 ID
- **省上下文**：列表精简成卡片，长文本可截断，9 帧画面拼成一张图
- **自描述**：`bili schema` 输出全部命令和错误码；技能里的命令手册安装时根据代码自动生成，永不过期

<details>
<summary><b>🔌 MCP 模式（Claude Desktop、Cherry Studio 等）</b></summary>

```bash
uv tool install --force "bili-agent[mcp]"
bili mcp install            # 预览：检测 Claude Desktop、Claude Code、Cursor、VS Code、Codex、Gemini CLI
bili mcp install --yes      # 写入配置（先备份为 .bak，只改 bili-cli 自己的条目）
```

17 个工具：收件箱、看视频、搜索、推荐、热门、评论、回复、私信、动态、登录……外加可以调用任意命令的 `run_bili`。写操作工具标记为非只读，支持的客户端会先征求你同意。手动配置时命令是 `bili mcp serve`。

</details>

<details>
<summary><b>📖 全部命令</b></summary>

| 命令 | 说明 |
| --- | --- |
| `bili auth login / status / logout` | 扫码登录、登录状态（自动续期）、登出 |
| `bili video info / stats / subtitle / danmaku / summary / chapters` | 视频信息、数据、字幕、弹幕（`--peaks N` 只看观众反应最强烈的时刻）、AI 总结、分段章节 |
| `bili video watch` | 一次拿全：信息 + 标签 + AI 总结 + 全文 + 章节 + 热评 + 弹幕高能时刻 + 带弹幕反应的画面总览图；`--record` 写入历史记录 |
| `bili video like / coin / fav / triple / relation` | 点赞、投币（必须指定个数，每天最多 5 个）、收藏、一键三连、查看我和视频的关系 |
| `bili me favs` | 我的收藏夹 |
| `bili video frames` | 抽帧并拼总览图：默认用进度条缩略图（快、免 ffmpeg），`-q hd` 精确帧；`--danmaku` 每帧附弹幕反应，`--peaks N` 在弹幕最密集的 N 个时刻取帧 |
| `bili video transcribe` | 语音转文字，格式同字幕，结果缓存 |
| `bili feed inbox` | 一次看完所有新消息（读取后 B 站标为已读） |
| `bili feed replies / mentions / likes / system / notifications` | 回复、@、赞、系统通知、未读数 |
| `bili feed recommend / timeline` | 首页推荐、关注动态（`--type video` 只看新投稿，`--up` 只看一个人） |
| `bili user follow / unfollow / following` | 关注、取关、我关注的人 |
| `bili user block / unblock / blocked` | 拉黑、解除拉黑、我的黑名单（应对骚扰） |
| `bili hot search / popular / weekly / precious / rank` | 热搜、综合热门、每周必看、入站必刷、排行榜 |
| `bili search video / user` | 搜索 |
| `bili user info / videos` | 任意用户的资料、粉丝数、投稿列表（按播放 / 收藏排序，按关键词筛系列）；动态用 `bili dynamic list --mid` |
| `bili comment list / replies / post / reply / like / delete / check / pin / emotes / images` | 评论读写、`--unreplied` 只看没回过的、置顶自己作品下的评论；`--at <UID|昵称|owner>` 真正的 @，`--image` 带图，`emotes` 查可用表情；`--type dynamic/draw/article` 用于动态和专栏 |
| `bili dm list / read / send` | 私信 |
| `bili dm watch` | 实时接收新私信（推送 + 20 秒兜底轮询 + 定期全量对账，每条一行 JSON；`--backlog` 先吐积压） |
| `bili dm send --image` | 私信带图片 |
| `bili dm autoreply show / set / on / off / keyword add / keyword delete` | B 站自带的私信自动回复（1000 粉以上） |
| `bili owner set / show / clear` | 设置“主人” |
| `bili dynamic post / list / show / delete / vote` | 动态：图文（最多 9 张）、`--at` @ 人、`--vote` 投票及结果 |
| `bili season list / create / add / remove / delete` | 合集管理 |
| `bili danmaku send / recall` | 发 / 撤回弹幕 |
| `bili danmaku interact vote / rating / reserve / link / follow / list / remove` | 互动弹幕（仅自己的视频） |
| `bili upload video / edit / list / status / stats / zones` | 投稿、改稿（`--desc-at` 简介里 @ 人，`--video` 换源）、稿件管理、单稿数据（互动率、完播率） |
| `bili upload chapters show / set / clear` | 分段章节 |
| `bili me show / stats / set-sign / set-name / set-avatar` | 个人资料、创作中心数据 |
| `bili data overview / trend / audience / fans / videos / video / diagnose` | 数据中心：累计与昨日增量、30 天趋势、观众画像、粉丝、稿件数据表、单稿诊断（3 秒退出率 / 封面点击倍数 / 播转粉率 / 同类分位） |
| `bili asr setup local / setup cloud / status / use` | 语音转写引擎 |
| `bili skills install / uninstall / status / show` | 把技能装进 Agent |
| `bili mcp serve / install / uninstall` | MCP 服务与客户端配置 |
| `bili schema` | 机器可读的完整命令描述 |

全局参数可写在任意位置：`--dry-run`、`--wait`（遇到 60 秒内能恢复的限流自动等待）、`--raw`（B 站原始数据）、`--pretty` / `--compact`。视频参数都接受 BV 号、av 号或链接。

</details>

<details>
<summary><b>🧯 错误码</b></summary>

| code | 退出码 | 含义 |
| --- | --- | --- |
| `invalid_argument` | 2 | 参数错误 |
| `not_logged_in` / `auth_expired` | 3 | 未登录 / 凭证失效 |
| `not_found` | 4 | 资源不存在 |
| `risk_control` | 5 | 被 B 站风控拦截，应停止写操作 |
| `captcha_required` | 6 | 需要人工完成验证码 |
| `rate_limited` | 7 | 操作过于频繁，按 `retry_after` 等待 |
| `network_error` | 8 | 网络错误 |
| `login_pending` / `login_expired` | 9 / 10 | 二维码未扫 / 已过期 |
| `api_error` / `internal_error` | 1 | 其他 B 站错误 / bili-cli 自身错误 |

</details>

## ❓ 常见问题

<details>
<summary>会不会被封号？</summary>

自动化总有风险。bili-cli 默认限流、遇到风控立刻停止，并且不提供任何绕过风控的手段。

**优先用你已经正常使用过一段时间的账号，不要专门注册一个新号给 AI 用。** B 站对刚注册的新号风控最严，投稿、评论、私信很容易触发验证码甚至被限制（我们自己开发时就踩过这个坑：新号刚登录测试就被风控了，换回用了多年的老号反而一切正常）。另外，使用节奏不要超过真人。

</details>

<details>
<summary>没有字幕的视频怎么办？</summary>

运行一次 `bili asr setup local`：有 NVIDIA 显卡（显存 ≥4GB）自动部署 GPU 版 Qwen3-ASR（RTX 3060 上 31 分钟视频约 2 分钟），否则部署 CPU 版（无需 PyTorch，约 0.45 倍实时）。也可以 `bili asr setup cloud` 接 OpenAI 兼容接口、阿里云百炼或腾讯云。之后 `video watch` 遇到无字幕视频会自动转写。

</details>

<details>
<summary>多个账号怎么办？</summary>

设置环境变量 `BILI_CLI_HOME` 指向不同目录，每个目录是一套独立的登录、限流和审计日志。本项目不提供批量控制多账号的功能。

**命令卡住怎么办？** 不会无限卡住：普通命令超过 3 分钟没完成会自己中止，返回 `network_error`，Agent 重试即可；投稿、语音转写、`dm watch` 这类长任务不受限制。用环境变量 `BILI_CLI_TIMEOUT` 改上限（秒，0 表示不限）。单个读请求最多等 45 秒。报错里带 `stalled_seconds` 说明整个进程被暂停过（机器或容器挂起、CPU 被占满），不是网络问题；排查时加 `BILI_CLI_DEBUG=1`，stderr 会打出每个请求的耗时。

</details>

<details>
<summary>在沙箱 / 只读环境里，写操作为什么会报错？</summary>

写操作成功后 bili-cli 会记一条本地记录（
atelimit.json、udit.log），默认写在配置目录。进程只能写某个工作区时（沙箱、只读容器），这一步可能失败——现在它只往 stderr 打一条警告，命令仍然返回 ok: true，不会让 Agent 误以为没发出去而重发。

想把状态文件放到别处，设置 BILI_CLI_STATE_DIR 指向一个可写目录：
atelimit.json、udit.log、seen.json 会写到那里，登录凭证仍然留在 BILI_CLI_HOME（Windows：%APPDATA%\\bili-cli）。

</details>

<details>
<summary>凭证存在哪里？安全吗？</summary>

Windows 在 `%APPDATA%\bili-cli\`，其他系统在 `~/.config/bili-cli/`（权限 600），只会发给 B 站自己的服务器。详见 [SECURITY.md](https://github.com/kukemc/bili-cli/blob/main/SECURITY.md)。

</details>

## 🤝 参与贡献

欢迎 Issue 和 PR！开发约定见 [CONTRIBUTING.md](https://github.com/kukemc/bili-cli/blob/main/CONTRIBUTING.md)，更新记录见 [CHANGELOG.md](https://github.com/kukemc/bili-cli/blob/main/CHANGELOG.md)。如果这个项目帮到了你，点个 ⭐ 就是最大的支持。

## ⭐ Star 趋势

<a href="https://star-history.com/#kukemc/bili-cli&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=kukemc/bili-cli&type=Date&theme=dark" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=kukemc/bili-cli&type=Date" />
  </picture>
</a>

## ⚠️ 免责声明

bili-cli 是独立的非官方项目，与哔哩哔哩没有关联。它用于让 AI 助手**管理你自己的账号**，不提供也不接受下载、付费内容、绕过风控、多账号、刷量、批量私信等功能。使用产生的后果由使用者自行承担，请遵守法律法规、B 站用户协议与社区规则，并对 AI 生成内容进行标识。完整内容见 [DISCLAIMER.md](https://github.com/kukemc/bili-cli/blob/main/DISCLAIMER.md)。

## License

[MIT](https://github.com/kukemc/bili-cli/blob/main/LICENSE)
