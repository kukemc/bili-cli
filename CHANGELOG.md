# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Fixed
- ili video coin --like：以前只投币、不点赞，返回却显示成功。原因是 ilibili-api 17.4.2 的 pay_coin() 把参数名写成了 like，而 B 站接口要的是 select_like，于是被静默忽略。现在投币后会检查返回和实际关系，没点上就显式补一次点赞。

## [1.2.1] - 2026-10-26

### Fixed
- `bili dm list --limit` 超过 20 时只返回 20 个会话：B 站每页只给 20 个，现在会往前翻页，`--limit` 上限提到 200。私信多的号，排在 20 名以后、还在等回复的会话以前看不到。
- B 站替你发的自动回复（被关注回复、关键词回复、收到消息回复）以前和你亲手发的消息分不出来，对方一边关注一边发私信时，会话看起来像已经回过了。现在 `dm list` 的 `last_message`、`dm read` 的消息和 `dm watch` 的事件里，这类消息带 `auto_reply: true`。

## [1.2.0] - 2026-10-26

### Added
- `bili data`：把创作中心「数据中心」里 `me stats` / `upload stats` 之外的面板补齐，全部只读。由 @AlbertEWang 在 #11 贡献。
  - `bili data overview`：累计播放 / 互动、昨日增量、周期对比（复用 `me.summarize_stats()`，和 `bili me stats` 同一份计算）、粉丝 / 路人播放占比、播放端分布、和同类 UP 主比、昨日涨最多的稿件。
  - `bili data trend --type play --days 30`：最近 30 天的每日增量（播放、点赞、收藏、投币、评论、弹幕、分享、充电）。
  - `bili data audience`：粉丝 / 路人的性别、年龄、省份分布（每周二更新），以及两边各自的兴趣覆盖率（可多选，合计超过 100%）。
  - `bili data fans`：涨粉 / 取关、活跃粉丝、粉丝来源、涨粉趋势、最活跃的粉丝。
  - `bili data videos`：稿件数据表（播放、点赞、评论、涨粉、完播率 + 诊断指标），播放数是当前总数；可按播放 / 完播 / 涨粉排序。
  - `bili data video <BV>`：单稿的粉丝 / 路人播放、完播率、平均观看时长、省份，以及 3 秒退出率、封面点击倍数（相对同类中位；绝对值每次请求都会变）、互动率、播转粉率和同类分位；播放数是结算值。
  - `bili data diagnose`：最近 N 个稿件的诊断指标横向对比。
- 这些接口 `bilibili-api` 没有封装，直接按网页请求实现；比率字段（1/10000）换算成百分比，`not_ready_field` 里没算完的指标会被跳过；HTTP 412 按项目约定报 `risk_control`。
- `charge` 是 B 站原始数值，单位未确认，不要当金额用。

### Fixed
- `bili auth status` 给 cookie 续期时，只要电脑时钟比 B 站快几秒就会失败，报 `internal_error`（`CookiesRefreshException: correspondPath 过期或错误`），登录一直续不上期。续期的时间戳现在用 B 站返回的服务器时间。续期仍然失败时，登录本身还有效，所以不再报错，而是返回 `refreshed: false`、`refresh_error` 和处理建议。

## [1.1.2] - 2026-10-26

### Fixed
- `bili me stats` 在还没有数据的新账号上崩溃（`AttributeError: 'NoneType' object has no attribute 'get'`）：创作中心对这类账号返回 `null`，现在会返回各项为空的正常结果。感谢 @36dvertigo 在 #10 里给出的复现和修复建议。
- 手动导入、缺少 `dedeuserid` 的凭证，在 `me favs`、`user following`、`dynamic` 投票等需要自己 UID 的命令里会报 `TypeError`；现在改为明确提示重新登录或补上 `dedeuserid`。

## [1.1.1] - 2026-10-25

### Fixed
- 命令不再可能无限卡住：每条命令有总时限（默认 3 分钟，环境变量 `BILI_CLI_TIMEOUT` 可改，0 表示不限），到点中止并返回 `network_error`（`detail.timeout`）。连事件循环本身被卡住、甚至输出管道写不进去的情况，也有看门狗线程兜底，保证进程退出。投稿、换源、语音转写、模型安装、`dm watch`、带 `--wait` 的登录不受这个上限限制，`video watch` 的上限是 1 小时，`video frames` 是 15 分钟。起因是另一台机器上 `bili dm read` 卡了约 12 分钟。
- 单个读请求（GET）最多等 45 秒：底层 curl 的 30 秒超时在现场有时拖到 52 秒、122 秒、313 秒、378 秒才触发，现在到点直接放弃并报 `network_error`。写请求（发评论、私信等）不在这里截断，免得半路取消造成重复发送。`dm watch` 同样受益。
- 卡住时能分清原因：`network_error` 会带上 `detail.stalled_seconds`（整个进程停住了，比如机器或容器被挂起、CPU 被占满）或 `detail.loop_blocked_seconds`（bili-cli 的事件循环被卡住），`dm watch` 的 warning 事件也带。

### Added
- 环境变量 `BILI_CLI_DEBUG=1`：把每个请求的方法、地址、耗时和失败原因打到 stderr，方便排查卡顿。

## [1.1.0] - 2026-10-25

### Added
- `bili video frames --danmaku`：每帧下方附上观众对这个画面的弹幕反应（条数 + 代表弹幕）。弹幕按“发送时间 − 2 秒”的打字延迟对齐，写在图下方，不盖住画面；总览图底部加一条弹幕密度条，标出每帧的位置。
- `bili video frames --peaks N`：在弹幕反应最强烈的 N 个时刻取帧。
- `bili video danmaku --peaks N`：不列全部弹幕，只返回观众反应最集中的 N 个时刻和每个时刻的代表弹幕。
- `bili video watch` 默认读弹幕：返回 `danmaku.peaks`（高能时刻）；总览图每帧附弹幕反应，并在最密集的 3 个时刻另外取帧、标“高能”（`--peaks` 调数量，`--no-danmaku` 关闭）。
- 总览图每帧下方最多 7 行弹幕，短句并排，一格通常能看到 10–20 条；重复多的排前面（×N），JSON 里每帧保留前 8 条。
- 技能文档（`skills/bili-cli`）新增“总览图怎么读”和“弹幕有时间差”：屏幕上叠着的弹幕多半在说前几秒的镜头，要以对齐结果为准。

### Fixed
- 预览缩略图（`video frames` 默认模式和 `video watch` 的总览图）整体错了一格：B 站缩略图时间索引的第一项是占位的 0，以前把每张图都标早了一个间隔（短视频约 5–6 秒）。现在和 ffmpeg 精确帧对得上，并且取离请求时间最近的那张。
- `video frames -q hd` 取视频末尾的帧时，如果视频流比标称时长短一点，会退一秒再取，不再整条命令失败。
- 缩略图接口偶尔返回空列表时多重试两次。

## [1.0.1] - 2026-10-25

### Added
- `bili dm send --image`：私信可以带图片（最多 3 张，每张是单独一条消息，排在文字后面）；只发图片时可以不写文字。
- `bili dm watch --backlog`：启动时先输出已经积压的未读私信，再接着实时收。
- `bili dm watch --resync`：定期（默认 120 秒）和完整会话列表对账，补上任何漏掉的消息。

### Fixed
- `bili dm watch` 在网络超时后会丢消息：以前时间游标先推进、后拉消息，拉取失败的那段永远不会再出现。现在会话拉成功才算处理过，失败的留在待办里每轮重试。
- `bili dm watch` 同步连续失败时每次轮询都重试并刷 warning：现在指数退避（最长 5 分钟），连续失败后自动做一次全量对账，恢复时输出 `recovered` 事件。
- `bili dm watch` 偶尔到了 `--for` / `--max-events` 也不退出（Python 3.12 以前 `asyncio.wait_for` 会吞掉取消）；每轮同步也加了 90 秒总超时，推送连接加了 15 秒连接超时。
- `bili dm read` 遇到内容不是对象的系统 / 卡片消息时报 `AttributeError`。
- 给官方号、系统通知号发私信时，B 站的「非法参数」现在报成「这个账号不接受私信」。

## [1.0.0] - 2026-10-25

第一个稳定版：命令名、参数、JSON 输出结构和错误码从这一版起按语义化版本维护，不兼容的改动只会出现在下一个大版本里。

### Added
- `bili dm watch`：实时接收新私信。做法和网页版消息中心一样：连上 B 站的私信推送通道（broadcast.chat.bilibili.com），某个会话有变化才去拉那一个会话，另外每 20 秒兜底轮询一次；推送连不上时自动退回轮询并重连。
  - 每来一条新消息输出一行 JSON（`event: message`，带 UID、昵称、内容、时间、seqno 和现成的回复命令），停止时输出 `{"ok": true, "data": {...}}` 汇总。
  - 默认不输出自己这边的消息，包括 B 站代发的关注自动回复。
  - `--max-events 1` 等下一条私信就退出，`--for` 限时，`--uid` 只看一个人；碰到风控立刻停，不重试。
- `bili dm read --limit` 最多 200 条：超过 30 条会往前翻页（接口每次最多给 30 条，以前多填了也只有 30 条）。

## [0.9.1] - 2026-10-24

### Added
- `bili user block / unblock`：拉黑、解除拉黑（UID 或完整昵称），`bili user blocked`：我的黑名单（#8）。
- 评论带上 `charged`（充电评论）和 `fan_medal`（戴着本 UP 的粉丝勋章，即充过电）；`bili comment list --supporters` 只看这些人，方便优先回复。充电金额不在评论数据里，拿不到。

## [0.9.0] - 2026-10-24

### Added
- `bili user follow / unfollow`：关注、取关（UID 或完整昵称；已经是目标状态时不重复操作），本地限制每小时 20 次；`bili user following`：我关注的人。
- `bili feed timeline --type video|article|pgc`：关注动态只看新投稿 / 专栏 / 番剧；`--up <UID|昵称>` 只看一个人；`--limit` 自动翻页凑够条数。
- 动态卡片标出置顶动态（`pinned`），不会被当成最新一条。

### Fixed
- B 站 12014「已达到当前评论发送上限」现在报 `rate_limited`，并提示至少停 1 小时再发；以前是笼统的 `api_error`。
- 12006「没有该评论」报 `not_found`；网络超时（curl 28）报 `network_error`，不再被当成 bili-cli 的 bug。

## [0.8.0] - 2026-10-24

### Added
- `bili upload video --staff "职能:UID"`：联合投稿，邀请合作者（可重复传入，`owner` 表示主人）。投稿前会检查账号有没有发起资格、本月次数和这个分区允许的职能；对方要在 24 小时内确认。
- `bili upload edit --video <文件>`：换源，上传新视频文件替换已投稿件（单 P），可配合 `--line` 选上传线路。

### Fixed
- `bili upload edit`：改稿时保留联合投稿成员、活动（mission）和话题；联合投稿的稿件不再被拒绝改稿。
- `bili upload video --reserve`：账号没有视频预约功能时，B 站会悄悄忽略这个选项；现在投稿前就会报错。
- `bili danmaku interact vote --allow-custom`：以前 B 站把「自定义」挂到了最后一个普通选项上，观众看不到自定义入口；现在会单独加一个自定义选项（默认「其他」，可用 `--custom-label` 改），固定选项最多 3 个。

## [0.7.0] - 2026-10-23

### Added
- `bili upload video --topic`：给稿件挂话题，填话题名或 ID；官方有奖活动的话题会顺带报名（带上活动的 mission_id）。
- `bili upload video --reserve`：定时发布时同时开启视频预约，空间和动态出现预约卡片，粉丝预约后发布时收到提醒。
- `bili upload video --season <合集ID>`：投稿后直接加进合集，和网页投稿一样。

### Changed
- `bili season add` 和 `bili danmaku interact`（投票、关联视频、关注按钮等）改从创作中心读取自己的稿件，审核中、定时发布还没公开的稿件也能用；之前会报 -404。

### Fixed
- `bili auth status --wait 120`：秒数参数被当成全局 `--wait` 吞掉的问题。
- `bili dynamic list` 遇到带投票的动态会崩溃（B 站把投票截止时间返回成字符串）。

## [0.6.0] - 2026-10-22

### Added
- `bili dynamic post --vote/--option`：发投票动态（单选或多选，1 到 30 天）；`bili dynamic vote <vote_id>` 看结果，动态卡片里带投票信息。bilibili-api 自带的投票接口已失效（404），改用网页端当前的接口。
- `bili season list / create / add / remove / delete`：合集管理——建合集、把自己的视频加进合集或移出、删除合集。
- `bili dm autoreply`：B 站自带的私信自动回复（1000 粉以上）——查看开关和内容，设置被关注 / 收到消息 / 大航海回复，增删关键词规则，开关各类回复。

## [0.5.1] - 2026-10-21

### Added
- 评论列表带上图片地址（`images`）；`bili comment images <url>` 把评论图片存到本地（长边 1280），方便多模态模型直接看图，只接受 B 站图床地址。
- `bili upload video / edit --desc-at`：简介里真正 @ 人（B 站按对方 UID 记录成 @，和网页投稿一样），最多 10 个。

### Fixed
- `bili upload edit` 改稿时原样带回简介里已有的 @（desc_v2）；之前不带，改标题等操作可能把简介里的 @ 丢掉。

## [0.5.0] - 2026-10-21

### Added
- `bili comment post --image`：评论带图片，最多 9 张（B 站不允许回复带图）。
- `bili comment emotes`：当前账号能用的 B 站表情，`-k` 搜索、`--package` 看整个表情包；表情文字直接写进正文即可。
- `--at` 可以直接填完整昵称（原来只收 UID 或 owner）。
- `bili dynamic post --at`：发动态时真正 @ 人。

### Fixed
- `bili upload edit` 拒绝修改有合作成员的联合投稿：改稿接口需要成员名单，原样提交可能出错或移除合作者。

## [0.4.0] - 2026-10-21

### Added
- `bili user info / videos`：查看任意用户的资料（粉丝、总播放、获赞、直播间）和投稿列表；可传 UID 或完整昵称，投稿可按播放 / 收藏排序、按关键词筛系列。
- `bili upload edit`：修改已投稿件的标题、简介、标签、封面、4:3 封面或 AI 声明，其余设置原样保留。
- `bili upload stats <BV>`：单稿数据，含每百次播放的互动率、完播率和平均观看时长。
- `bili me stats`：创作中心数据——播放 / 访客 / 涨粉（和上一周期对比）、播放端分布、粉丝贡献的播放占比、超过多少同类 UP、昨日涨得最多的稿件。
- `bili comment list --unreplied`：只列 UP 主还没回复过的评论。
- 全局参数 `--wait`：写操作遇到 60 秒内能恢复的本地限流时自动等待；每小时、每日上限仍直接报错。
- `bili video frames --at` 支持小数秒（如 `1:30.5`），不同小数秒的画面分开保存。
- `bili comment pin`：在自己的作品下置顶一条根评论，`--undo` 取消。
- `bili comment list` 第 1 页单独返回置顶评论（`pinned`）。
- `bili skills install` 支持 DeepSeek Harness（dsh）：检测到 `~/.dsh`（或 `$DSH_HOME`）时装进其 `skills` 目录；dsh 也会读取通用的 `~/.agents/skills`。

### Fixed
- `bili comment check` 把置顶评论误报为“找不到”（B 站不把置顶评论放进普通列表）；现在会识别并标出 `pinned`，并往后多查两页。

## [0.3.0] - 2026-10-20

### Added
- `bili upload video --cover43`：单独上传手机端推荐流使用的 4:3 封面。

## [0.2.0] - 2026-10-20

### Added
- 发布到 PyPI，包名 `bili-agent`（`bili-cli` 已被占用），安装只要 `uv tool install bili-agent`；命令仍是 `bili`。打 `v*` 标签后自动发布。
- `bili skills install / uninstall / status / show`：把 Agent Skill 装进本机的 Claude Code、Codex、Cursor、Gemini CLI、Copilot、OpenClaw、Qwen Code 等 AI Agent（只写入已检测到的 Agent，不覆盖用户自己的同名技能）。
- 一行安装脚本 `scripts/install.sh`、`scripts/install.ps1`，以及给 Agent 看的安装指南 `docs/install.md`。
- MCP 模式：`bili mcp serve`（17 个工具，写操作带非只读标注，行为和护栏与命令行完全一致）；`bili mcp install / uninstall` 注册到 Claude Desktop、Claude Code、Cursor、VS Code、Codex、Gemini CLI（先备份，只改自己的条目）。安装 `bili-agent[mcp]`。
- `DISCLAIMER.md`、`SECURITY.md`、`CONTRIBUTING.md`。
- `bili video like / coin / fav / triple / relation`、`bili me favs`：点赞、投币、收藏到指定收藏夹、一键三连、查看我和视频的关系。投币必须显式指定个数，本地每 24 小时最多 5 个；对没看过的视频操作会提示。
- Logo、Banner，README 重写。

## [0.1.0] - 2026-10-20

首个版本。

- 扫码登录（两步走，适合 Agent），凭证本地保存。
- 看视频：信息、字幕、弹幕、AI 总结、分段章节、画面抽帧与总览图、语音转写（本地 Qwen3-ASR GPU/CPU，或云端 OpenAI 兼容 / 阿里云 / 腾讯云），`video watch` 一次拿全。
- 刷视频：首页推荐、热搜、综合热门、每周必看、入站必刷、排行榜，`--unseen` 跳过看过的。
- 互动：评论读写、真正的 @（`at_name_to_mid`）、评论可见性检查、消息收件箱（回复 / @ / 赞 / 系统通知 / 私信）、私信收发（带防骚扰白名单）。
- 创作：动态、投稿（默认“含AI生成内容”声明、自动预测分区、定时、仅自己可见）、稿件状态、分段章节、互动弹幕（投票、评分、下期预告、关联视频、关注 / 三连按钮）、个人资料。
- 护栏：所有写操作本地限流 + 审计日志，`--dry-run` 预览，统一 JSON 输出与错误码。
