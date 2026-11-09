# 常用工作流

所有示例里的 `<BV>` 都可以换成 av 号或视频链接。

## 1. 处理新消息（自主运营的每一轮从这里开始）

```bash
bili feed inbox --limit 5
```

返回 `unread`（各类未读数）以及新的 `replies`、`mentions`、`likes`、`system`、`private_messages`。读取后 B 站会把它们标为已读，所以**一轮只调用一次**，并把结果记在你自己的上下文里。

- 回复评论：每条 `replies` / `mentions` 都带 `reply_with`，例如
  `bili comment reply BV1xx411c7mD 3176… --root 3147… "<回复内容>"`。只替换 `<回复内容>`，不要改 ID。
- 回复私信：`bili dm read <uid>` 看上下文，再 `bili dm send <uid> "内容"`。
- 赞和系统通知一般只需要了解，不需要回应；系统通知里出现“违规”“审核”时告诉用户。

## 2. 刷视频、挑感兴趣的看

```bash
bili feed recommend --unseen -n 12      # 个性化推荐，--page 2 换一批
bili hot popular --unseen               # 全站热门
bili hot search                         # 热搜词 → bili search video "<关键词>"
bili hot rank --zone tech --unseen      # 分区排行：all/tech/knowledge/game/…
bili hot weekly                         # 每周必看
```

先根据 `title`、`desc`、`reason`、`stat` 粗筛，只对值得的视频调用 `video watch`（它比列表命令贵得多）。`--unseen` 会跳过 `video watch` 看过的视频。

## 3. 看懂一个视频

```bash
bili video watch <BV>
```

一次返回：`title`、`desc`、`tags`、`ai_summary`（B 站官方总结，可能为空）、`transcript.text`（字幕或语音转写）、`chapters`、`hot_comments`、`danmaku.peaks`（观众反应最强烈的几个时刻和代表弹幕）、`frames_sheet`（画面总览图路径，能看图就打开它）。

- 只要文字：`bili video subtitle <BV> --text`
- 没字幕：`bili video transcribe <BV> --text`（需先 `bili asr setup local` 或 `asr setup cloud`）
- 看更多画面：`bili video frames <BV> --every 30`、`--chapters`、`-q hd`（精确帧，需 ffmpeg）
- 想让推荐越来越懂这个账号：`bili video watch <BV> --record`（写一次历史记录，不刷播放量）

### 总览图怎么读

一张图里按时间顺序排着很多帧（3 列）：

- 每格左上角是编号和时间；红底“高能”的是弹幕最密集的时刻另外取的帧。
- 每格下方黄字是“弹幕 N 条”，后面几行是观众对**这个画面**的反应，重复越多越靠前（白字带 ×N），一次性的是灰字。
- 最底下的蓝色柱状图是整条视频的弹幕密度，竖线标出上面每一帧在时间轴上的位置。

### 弹幕有时间差，别按同屏弹幕理解画面

弹幕不是“这个画面的字幕”。观众看到一个镜头，要过一两秒才打完字发出去，发出后弹幕还会在屏幕上滚动六到八秒。所以：

- **任何时刻屏幕上的弹幕，大多在说前几秒的镜头。** 例如画面已经切到另一个角色，屏幕上刷的“可爱”可能还在说上一个镜头里的大肥鱼。人类截图发给你时尤其要注意这一点。
- `video watch` 和 `video frames --danmaku` 已经把每条弹幕按“发送时间 − 2 秒”算到它在反应的那一帧上，每格下方的反应就是对齐后的结果。引用观众反应时，以这个对齐结果为准。
- 2 秒是估计值，镜头切得很快时仍可能差一个镜头。拿不准时用 `-q hd --at` 在前后各取一帧对照；`--every 2` 可以看清镜头顺序。
- 默认的预览帧来自 B 站进度条缩略图，只有每隔几秒一张，取到的是离要求时间最近的那张（JSON 里的 `actual_time` 是真实时间）。要精确到秒用 `-q hd`。

### 找观众反应最大的地方

- 只要文字：`bili video danmaku <BV> --peaks 5`，返回 5 个时刻，每个附代表弹幕，比翻几百条弹幕省得多。
- 要画面：`bili video frames <BV> --peaks 5`（`-q hd` 精确帧），每帧下方附弹幕反应。
- 均匀取帧再看反应：`bili video frames <BV> --every 10 --danmaku`。
- 高能时刻往往就是视频的笑点、泪点或名场面；分析爆款时，先看这几个时刻发生了什么，再对照热评。

## 4. 点赞、收藏、三连

```bash
bili video relation <BV>                 # 我赞了没、投了几个币、收藏了没
bili video like <BV>                     # 点赞；--undo 取消
bili video fav <BV> --folder 学习         # 收藏到指定收藏夹（默认“默认收藏夹”）；--remove 取消
bili me favs                             # 列出收藏夹
bili video coin <BV> --coins 1           # 投币：必须指定 1 或 2；每个视频最多 2 个，每天本地最多 5 个
bili video triple <BV>                   # 一键三连（点赞 + 投币 + 收藏）
```

只对真正看过、觉得好的视频这样做。投币花的是用户的真硬币，没有授权时先问用户。UP 主不能给自己的视频投币（返回 34002）。

## 5. 发表看法、@ 主人

```bash
bili owner set <主人UID>                         # 一次性设置
bili comment post <BV> "看完的感想" --at owner    # 真正的 @，主人会收到提醒；--at 也收 UID 或完整昵称，最多 3 个
bili comment post <BV> "看图[doge]" --image shot.png   # 带图评论，最多 9 张；回复（楼中楼）B 站不允许带图
bili comment emotes -k 笑                         # 找表情：把 [笑哭] 这类文字原样写进正文即可
bili comment images <url>                         # 评论带图时 list 输出里有 images，存到本地后用看图能力查看
bili comment check <BV> <rpid>                   # 新号/敏感内容可能被折叠，确认一下
bili comment pin <BV> <rpid>                     # 在自己的视频下置顶一条根评论（--undo 取消）
```

返回里的 `mentions[].resolved` 为 `true` 表示 B 站识别了这个 @。一条评论最多 @ 3 人。

`comment list` 第 1 页会把置顶评论单独放在 `pinned` 里。每个评论区只能置顶一条，新置顶会替换旧的；置顶前先问用户。

## 6. 私信

```bash
bili dm list --unread
bili dm read <uid> --limit 20
bili dm send owner "今天看到一个你可能喜欢的视频：BV…"
```

`last_message.auto_reply: true` 表示最后一条是 B 站替你发的自动回复（被关注回复、关键词回复等），对方其实还在等你回。

默认只能发给：主人、给你发过私信的人、互关好友。给陌生人发第一条私信需要 `--allow-new`，除非用户明确要求，不要用。最长 500 字，被拒绝不要重发。

## 7. 发动态、投稿

```bash
bili dynamic post "文字内容" --image cover.png --at 冬冰   # 图文动态（最多 9 张），--at 真正 @ 人
bili dynamic post "下期做什么？" --vote "下期内容" --option 安装教程 --option 接管评论区 --vote-days 7   # 投票动态
bili dynamic vote <vote_id>                          # 投票结果（vote_id 在动态卡片的 vote 里）

# 合集
bili season list                                     # 我的合集、小节和里面的视频
bili season create "大肥鱼日常" --cover c.jpg --desc "…"   # 新建（必须有封面，会人工审核）
bili season add <合集ID> BV1xx BV1yy                 # 把自己的视频加进合集（默认第一个小节，--section 指定）
bili season remove <合集ID> BV1xx                    # 移出；bili season delete <合集ID> 删除整个合集

# 私信自动回复（B 站自带，1000 粉以上；由 B 站发出，Agent 不在线也生效，对同一人一天一次）
bili dm autoreply show                               # 开关、内容、关键词规则
bili dm autoreply set follow "感谢关注！"              # follow / message / voyage，会同时打开开关
bili dm autoreply keyword add "教程在这里…" --name 教程 --fuzzy 教程 --fuzzy 怎么装
bili dm autoreply off message                        # 关开关；keyword delete <id> 删规则
bili upload video demo.mp4 --title "标题" --tag AI --tag 教程 --desc-file desc.txt --private
bili upload status <BV>        # 审核进度，以及 B 站实际记录的标签/分区/声明
bili upload edit <BV> --title "新标题" --cover new.jpg --cover43 new43.jpg   # 改稿，没指定的设置保持不变
bili upload edit <BV> --desc "角色设计 @冬冰 ，感谢！" --desc-at 冬冰    # 简介里真正 @ 人；只传 --desc-at 则给现有简介加 @
bili upload edit <BV> --video fixed.mp4                     # 换源：上传新文件替换视频（单 P 稿件），标题等不变
```

投稿默认带“含AI生成内容”声明；`--private` 仅自己可见，适合先试；`--schedule 2026-10-25T20:00` 定时发布（需 2 小时到 15 天后）。分区由标题自动预测，预测不准时用 `bili upload zones` 查好再 `--zone`。封面有两张：`--cover` 是横版（16:9），`--cover43` 是手机端推荐流用的 4:3 版，两张都给效果最好。

`--topic "AI MV大赛"` 给稿件挂话题，填话题名或 ID；官方有奖活动的话题会顺带报名（活动对标题、分区等的要求要自己看活动页）。`--schedule … --reserve` 定时发布的同时开视频预约，空间和动态出现预约卡片，粉丝点了预约，发布时会收到提醒（账号要有预约功能，没有时会直接报错）。`--staff "策划:94782782"` 发起联合投稿邀请合作者，可重复传入；需要账号有联合投稿资格（创作等级 Lv3 等），职能要在分区允许的列表里（报错时会列出来），对方 24 小时内在消息里确认才挂名，联合投稿不能转载或仅自己可见，之后也能用 `upload edit` 改稿、换源，合作成员和活动报名会保留。`--season <合集ID>` 投稿后直接加进合集；审核中、定时发布的稿件也能用 `bili season add` 加，互动弹幕（投票等）同样可以在发布前加好。

改稿（`upload edit`）会让稿件重新审核，审核期间新内容不生效；B 站可能丢弃个别标签，改完用 `upload status` 确认。评论区被关闭或精选的稿件请到网页端改（命令会直接拒绝）。dry-run 里的 `copyright`、`tid` 是 B 站现存的值原样回传（网页投稿的稿件常见 `copyright: 3`），改稿不会改变它们；分区和版权类型目前只能在网页端改。

## 8. 写专栏（长文）

```bash
bili article categories                                  # 先查二级分类 ID（发布必填，否则 20022）
bili article drafts                                      # 草稿箱：建过但忘了 cvid 时先用这个找回
bili article articles                                    # 已发布的专栏列表
bili article draft --title "标题" --file body.html --category 34   # 建草稿，返回草稿 ID
bili article image a.png --caption "图注"                 # 传图，拿到可直接粘进正文的 <figure> 片段
bili article embed body.html --image a.png --after "某段独一无二的话" --out body2.html
bili article publish <草稿ID>                            # 提交发布（进审核），返回真正的 cvid
bili article update <cvid> --file body2.html             # 原地改（会重新审核，cvid 不变）
bili article show <cvid> --text                          # 读回正文
bili article watch <cvid>                                # 等过审
```

**cvid 会变。** `draft` 返回的是草稿 ID；`publish` 之后文章拿到另一个 cvid。发布后草稿 ID 就作废了，拿它去 `show` / `update` / `delete` 都会失败。`show` 也只认已发布的文章。

**正文是 HTML，编辑器有标签白名单。** 只保留 `p span br h1 figure img figcaption a strong blockquote ul ol li`。`<pre>` / `<code>` 会被降级，代码块的换行和空格全丢——**代码请渲染成图片再放 `<img>`**；`<hr>` 也会被丢掉（实测，分割线改用图片或留白）分类字段叫 `category`，写成 `category_id` 会被当成 `template_id` 悄悄吃掉。

**配图两种。** `image` 只上传并返回 `<figure>` 片段，位置由你拼；`embed --after` 真的把图插进正文。锚点必须是原文里**一字不差且只出现一次**的片段——找不到或出现多次都直接报错，不会退而求其次塞到末尾。图片块要和其它块同级，别套进 `<p>` 里。

**AI 声明。** 专栏编辑器没有 AI 声明字段（投稿有），`--ai` 在正文末尾追加一段，幂等（先摘再加），`--no-ai` 或重跑不会堆叠。三项都要自己给：`--ai-client "DeepSeek Harness" --ai-model "deepseek-flash" --ai-agent "小爪"`，各占一行；缺 `--ai-model` / `--ai-agent` 会出警告。工具不猜你的运行时和模型——它不知道自己在哪跑。`update` 换正文时如果不带 `--ai` / `--no-ai`，会沿用原来的声明（连同里面的模型和智能体），不会悄悄丢掉。

**限流。** 发布 / 改 / 删走 `article` 桶（30 秒间隔、每小时 10 次），存草稿走 `article_draft` 桶，配图走 `article_image`（2 秒、60 次/小时）。`--wait` 只自动等 60 秒以内的限制，更长的直接返回 `rate_limited` 和 `retry_after`。

**过审。** `watch` 轮询作者侧的 `state`：-2 审核中 / 0 已发布 / 2 未通过 / 1 待审核。只有 0 和 2 是终点，`1` 会继续等（`--interval` 最小 10 秒，`--timeout` 默认 600 秒）。不用等私信——B 站不会把审核结果私信给作者本人。

## 9. UP 主运营工具

```bash
bili upload chapters set <BV> -c "0:00 开场" -c "1:30 正片"     # 进度条分段章节
bili danmaku interact vote <BV> --question "选哪个？" --option A --option B --time 30
bili danmaku interact follow <BV> --time 60 --style triple       # 一键三连按钮
bili danmaku interact list <BV>                                  # 含投票结果、评分
bili comment pin <BV> <rpid>                                     # 置顶一条评论（如项目链接）
bili --wait comment list <BV> --unreplied --sort time            # 还没回的评论
```

### 看数据、做复盘

```bash
bili upload stats <BV>     # 每百次播放的点赞/收藏/投币/评论数，完播率、平均观看时长
bili me stats --period week  # 账号整体：播放、涨粉掉粉、手机/电脑占比、粉丝贡献的播放占比、超过多少同类 UP
bili user videos <UID> --order views --size 10   # 同类 UP 的爆款，借鉴标题和封面
```

创作中心数据次日更新：刚发的稿件第二天才有完播率。收藏率明显高于点赞率，通常说明观众觉得“有用、回头再看”。

### 数据中心（`bili data`）

数据中心里 `me stats` / `upload stats` 之外的面板都在这里，都是只读命令：

```bash
bili data overview                     # 累计播放/互动 + 昨日增量 + 和 me stats 同一份的周期对比 + 同类对比 + 昨日涨最多的稿
bili data overview --period month      # 换周期：yesterday / week / month / 3months
bili data trend --type play --days 30  # 每日增量：哪天爆了、哪天掉下去（like / favorite / coin / comment / danmaku / share / charge）
bili data audience                     # 观众画像：粉丝 vs 路人的性别、年龄、省份、兴趣标签
bili data fans --period week           # 涨粉/取关、活跃粉丝、粉丝来源、涨粉趋势、最活跃的粉丝
bili data active-hours                 # 粉丝活跃时间：24 小时 / 星期分布、最活跃时段、大盘对比、最近 5 个稿件的发布时段
bili data videos --limit 20 --sort completion   # 稿件数据表：播放、点赞、评论、涨粉、完播率
bili data video <BV>                   # 单稿：性别/年龄/兴趣标签、完播率、平均观看时长、省份、3 秒退出率、封面点击倍数、播转粉率、同类分位
bili data diagnose --size 10           # 最近 10 个稿件的诊断指标横向比
```

和已有命令的关系（别拿同一个数字的两个版本互相比）：

- `bili data overview` 的周期对比直接复用 `bili me stats` 的计算，只是额外给累计总量和昨日增量——快速看一眼用 `me stats`，要总量/昨日增量用 `data overview`。
- `bili data video` 的完播率和 `bili upload stats` 是同一个数，额外给单稿的性别 / 年龄 / 兴趣标签、省份和诊断指标。

口径（别混）：

- **播放数两个口径**：`data videos` 是当前总数（接近实时），`data video` 是截至 `data_date` 的结算数，同一个稿件两者会差不少。
- **次日结算**：`overview` 的累计与昨日增量、`video` 的互动与诊断指标都截至 `data_date`；`trend` 的每日增量与 `overview` 的昨日增量对得上。
- **观众画像（账号级 vs 单稿级，两套口径）**：
  - `data audience` 是账号级：性别 / 年龄 / 地区由 B 站每周二更新前一周的数据；兴趣标签是另一套统计，**可多选的覆盖率**，`viewers_percent` 合计会超过 100%。
  - `data video` 是单稿级：性别 / 年龄只给 `*_percent`（原始值是计数，约等于播放量的 0.28 / 0.59 倍，不是 1/10000）；兴趣标签只给 `name` 和 `weight`，**单位未确认，不要当百分比读**。这个接口每次返回顺序不同、并列值很多，命令按 `(-weight, name)` 排序保证可复现；前 10 个大量并列时说明样本还分不出主次。
- **封面点击**：接口每次返回的绝对值会变，命令只给 `cover_click_vs_peer_median`（相对**同类稿件中位**的倍数，不是你自己稿件的）和稳定的同类分位，不要当百分比用。
- **粉丝**：`yesterday.fans_net` 是结算值；`fans.total` 是实时值。
- **活跃时间**：`data active-hours` 的百分比是 B 站返回的 1/10000 分布（24 小时合计 100%），小时按北京时间；`gap_percent` 是「你的粉丝 − 大盘」的占比差，正数表示这个点你的粉丝比大盘更活跃，负数表示这个点你的粉丝相对少。`better_than_peers_percent` 是活跃粉丝的同类分位。
- **charge**：B 站原始数值，单位未确认（按电池、元、分换算都对不上），不要当金额用。

复盘时值得看的组合：

- **播放涨、完播低**：标题/封面把人骗进来了，但内容没留住——看 `bili data video` 的 `early_exit_percent`（3 秒退出率）和 `peer_median_percent`。
- **播放持平、互动高**：内容质量在线但分发不够，参考 `versus_peers`，再想想分区和投稿时间。
- **粉丝画像和你想的不一样**：`bili data audience` 的兴趣标签来自 B 站自己的统计，按它调整选题比凭感觉准。
- **某天突然涨/跌**：`bili data trend --type play` 找到那天，再用 `bili data videos` 看当天哪个稿件在涨。
