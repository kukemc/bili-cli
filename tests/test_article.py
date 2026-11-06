"""Offline tests for `bili article`. No network, no account.

The creative-center API is reached through `bilibili_api.utils.network.Api`, so each test swaps
`article.Api` for a fake that records the parameters it was called with. That keeps these tests
honest about the two things most likely to regress: **which field name carries the category**, and
**whether `submit` sends a complete payload**.
"""

from __future__ import annotations

import base64
import json

import pytest

from bili_cli.commands import article
from bili_cli.errors import BiliError


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("BILI_CLI_HOME", str(tmp_path))
    return tmp_path


class FakeApi:
    """Stands in for network.Api: records params, returns a preset result."""

    calls: list[tuple[str, str, dict]] = []
    result_for: dict[str, object] = {}

    def __init__(self, url: str, method: str = "GET", **kwargs):
        self.url = url
        self.method = method
        self.params: dict = {}
        self.data: dict = {}
        self.extra: dict = {}

    def update_params(self, **kwargs):
        self.params = kwargs
        return self

    def update_data(self, **kwargs):
        self.data = kwargs
        return self

    @property
    async def result(self):
        FakeApi.calls.append((self.method, self.url, {**self.params, **self.data, **self.extra}))
        for frag, value in FakeApi.result_for.items():
            if frag in self.url:
                if isinstance(value, Exception):
                    raise value
                return value
        return {}


@pytest.fixture(autouse=True)
def fake_credential(monkeypatch):
    """所有命令都先 require_credential()，离线测试里给个假的就行。"""

    class FakeCred:
        sessdata = "s"
        bili_jct = "j"
        buvid3 = "b"
        dedeuserid = "1"

    monkeypatch.setattr(article.config, "require_credential", lambda: FakeCred())
    return FakeCred()


@pytest.fixture
def fake_api(monkeypatch):
    FakeApi.calls = []
    FakeApi.result_for = {}
    monkeypatch.setattr(article, "Api", FakeApi)
    return FakeApi


def run_cmd(fn, **kwargs):
    """Invoke a Typer command function directly and return the dict its body produced."""
    captured = {}
    real_run = article.run

    # Typer 命令体是 async 的闭包，直接取不到；替换 run 来同步执行。
    def capturing_run(body):
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            raise RuntimeError("测试里不能有正在跑的事件循环")
        captured["value"] = asyncio.run(body())

    article.run = capturing_run
    try:
        fn(**kwargs)
    finally:
        article.run = real_run
    return captured["value"]


# ── 纯函数 ──────────────────────────────────────────────────────────────────


def test_check_tags_flags_pre_and_code():
    """<pre> / <code> 不在白名单里——这是这次踩过的坑，必须能被检测出来。"""
    html = "<p>说明</p><pre><code>line1\nline2</code></pre>"
    dropped = article._check_tags(html)
    assert "pre" in dropped and "code" in dropped


def test_check_tags_accepts_whitelist():
    html = '<h1>标题</h1><p>正文</p><figure><img src="x"><figcaption>c</figcaption></figure>'
    assert article._check_tags(html) == []


def test_plain_strips_markup_and_whitespace():
    assert article._plain("<p>你好 世界</p>") == "你好世界"


def test_plain_keep_lines_preserves_breaks():
    out = article._plain_keep_lines("<p>一</p><p>二</p>")
    assert out.splitlines() == ["一", "二"]


def test_data_uri_is_data_uri_not_raw_base64(tmp_path):
    """裸 base64 会被服务端拒绝（-400），必须以 data: 开头。"""
    p = tmp_path / "a.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
    uri = article._data_uri(p)
    assert uri.startswith("data:image/png;base64,")
    assert base64.b64decode(uri.split(",", 1)[1]) == p.read_bytes()


def test_data_uri_rejects_non_image(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("nope")
    with pytest.raises(BiliError):
        article._data_uri(p)


# ── 命令 ────────────────────────────────────────────────────────────────────


def test_draft_dry_run_does_not_call_api(fake_api, tmp_path, monkeypatch):
    monkeypatch.setattr(article.output.settings, "dry_run", True, raising=False)
    body = tmp_path / "b.html"
    body.write_text("<p>hi</p>", encoding="utf-8")
    out = run_cmd(article.draft, title="标题", file=body, content=None, category=34, cover=None, original=True, ai=False, no_ai=False, ai_client="", ai_model="", ai_agent="", cvid=None)
    assert out["dry_run"] is True
    assert out["action"] == "article.draft"
    assert fake_api.calls == []


def test_draft_rejects_overlong_title(fake_api):
    with pytest.raises(BiliError):
        run_cmd(article.draft, title="x" * 41, file=None, content="<p>a</p>", category=None, cover=None, cvid=None)


def test_draft_rejects_empty_body(fake_api):
    with pytest.raises(BiliError):
        run_cmd(article.draft, title="t", file=None, content="   ", category=None, cover=None, cvid=None)


def test_publish_sends_category_field_not_category_id(fake_api):
    """回归点：category 才是分类字段；写 category_id 会被当成 template_id 静默丢掉。"""
    fake_api.result_for = {
        "draft/view": {"title": "T", "content": "<p>x</p>", "category": {"id": 34}, "banner_url": "http://c/1.jpg"},
        "article/submit": {"state": -2, "view_url": "https://example/preview"},
    }
    out = run_cmd(article.publish, cvid=123, category=None)
    sub = [c for c in fake_api.calls if "article/submit" in c[1]][0][2]
    assert sub["category"] == 34
    assert "category_id" not in sub
    assert sub["title"] == "T" and sub["content"] == "<p>x</p>"
    assert sub["reprint"] == 0
    assert out["state"] == "审核中"


def test_publish_without_category_fails_early(fake_api):
    """没有分类时 submit 必然 20022，所以 publish 应该在发请求前就报错。"""
    fake_api.result_for = {
        "draft/view": {"title": "T", "content": "<p>x</p>", "category": {"id": 0}},
    }
    with pytest.raises(BiliError):
        run_cmd(article.publish, cvid=1, category=None)
    assert not any("article/submit" in c[1] for c in fake_api.calls)


def test_publish_reads_body_from_draft_not_from_aid_only(fake_api):
    """submit 缺 content 会报 20028，所以必须从草稿读出来带上。"""
    fake_api.result_for = {
        "draft/view": {"title": "T", "content": "<p>body</p>", "category": {"id": 1}},
        "article/submit": {"state": -2},
    }
    run_cmd(article.publish, cvid=9, category=None)
    sub = [c for c in fake_api.calls if "article/submit" in c[1]][0][2]
    assert sub["content"] == "<p>body</p>"


def test_image_returns_url_and_uses_data_uri(fake_api, tmp_path):
    fake_api.result_for = {"upcover": {"url": "http://article.biliimg.com/x.png"}}
    p = tmp_path / "i.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    out = run_cmd(article.image, path=p, caption="")
    assert out["url"].startswith("https://")
    sent = [c for c in fake_api.calls if "upcover" in c[1]][0][2]
    assert sent["cover"].startswith("data:image/png;base64,")


def test_update_keeps_existing_cover_when_not_given(fake_api, tmp_path):
    fake_api.result_for = {
        "/article/view": {"title": "旧", "content": "<p>旧</p>", "category": {"id": 5}, "banner_url": "https://c/keep.jpg"},
        "article/update": {"state": 0},
    }
    body = tmp_path / "n.html"
    body.write_text("<p>新</p>", encoding="utf-8")
    run_cmd(article.update, cvid=7, file=body, content=None, title=None, cover=None, ai=False, no_ai=False, ai_client="", ai_model="", ai_agent="")
    sent = [c for c in fake_api.calls if "article/update" in c[1]][0][2]
    assert sent["banner_url"] == "https://c/keep.jpg"
    assert sent["content"] == "<p>新</p>"
    assert sent["title"] == "旧"


def test_delete_calls_delete_endpoint(fake_api):
    fake_api.result_for = {"article/view": {"title": "要删的"}, "article/delete": {}}
    out = run_cmd(article.delete, cvid=42)
    assert out["deleted"] is True
    assert any("article/delete" in c[1] for c in fake_api.calls)


def test_show_reports_images_and_tags(fake_api):
    fake_api.result_for = {
        "/article/view": {
            "title": "T",
            "content": '<h1>a</h1><p>b</p><img src="1"><img src="2">',
            "category": {"id": 34},
            "banner_url": "https://c/x.jpg",
        }
    }
    out = run_cmd(article.show, cvid=3, text=False)
    assert out["images"] == 2
    assert out["category"] == 34
    assert out["dropped_tags"] == []   # h1/p/img 都在白名单里

# ── 真实 Api 构造（不 mock）───────────────────────────────────────────────────


def test_api_carries_credential_and_verify(fake_credential):
    """回归点：早先 `_api()` 没带 credential，真实账号上直接抛
    CredentialNoSessdataException。这条不 mock Api，直接检查真实构造出来的对象。"""
    from bilibili_api.utils.network import Api

    api = article._api("draft/addupdate", "POST", fake_credential)
    assert isinstance(api, Api)
    assert api.credential is fake_credential
    assert api.verify is True
    assert "draft/addupdate" in api.url


def test_post_uses_update_data_and_keeps_csrf(fake_credential):
    """回归点：update_params 是整体替换（self.params = kwargs），二次调用会把 csrf 冲掉。
    POST 必须走 update_data，csrf 由库在 prepare 阶段追加。"""
    api = article._api("article/submit", "POST", fake_credential).update_data(aid=1, title="t")
    assert api.data == {"aid": 1, "title": "t"}
    assert api.params == {}          # csrf 不在这里，也不会被覆盖
    assert api.no_csrf is False      # 库会自动追加 csrf


def test_get_uses_update_params(fake_credential):
    api = article._api("draft/view", "GET", fake_credential).update_params(aid=7)
    assert api.params == {"aid": 7}
    assert api.data == {}

# ── AI 创作声明 ──────────────────────────────────────────────────────────────


def test_ai_notice_appended_when_enabled():
    out = article._apply_ai_notice("<p>正文</p>", True)
    assert article.AI_NOTICE_STEM in out
    assert "<p>正文</p>" in out


def test_ai_notice_removed_when_disabled():
    once = article._apply_ai_notice("<p>正文</p>", True)
    assert article._apply_ai_notice(once, False) == "<p>正文</p>"


def test_ai_notice_is_idempotent():
    """重复开不会堆出两份。"""
    once = article._apply_ai_notice("<p>正文</p>", True)
    twice = article._apply_ai_notice(once, True)
    assert once == twice
    assert twice.count(article.AI_NOTICE_STEM) == 1


def test_ai_notice_replaces_stale_tail():
    """正文被重写后再开，旧声明要跟着走，而不是叠在中间。"""
    old = "<p>旧正文</p>\n<p>" + article.AI_NOTICE_STEM + "</p>"
    new = article._apply_ai_notice("<p>新正文</p>" + old[len("<p>旧正文</p>"):], True)
    assert new.count(article.AI_NOTICE_STEM) == 1
    assert new.rstrip().endswith("</p>")


def test_draft_sends_notice_when_ai_flag_set(fake_api, tmp_path):
    fake_api.result_for = {"draft/addupdate": {"aid": 555}}
    body = tmp_path / "b.html"
    body.write_text("<p>x</p>", encoding="utf-8")
    run_cmd(article.draft, title="t", file=body, content=None, category=34, cover=None, original=True, ai=True, no_ai=False, ai_client="Harness", ai_model="m", ai_agent="a", cvid=None)
    sent = [c for c in fake_api.calls if "addupdate" in c[1]][0][2]
    assert article.AI_NOTICE_STEM in sent["content"]


def test_draft_omits_notice_by_default(fake_api, tmp_path):
    fake_api.result_for = {"draft/addupdate": {"aid": 556}}
    body = tmp_path / "b.html"
    body.write_text("<p>x</p>", encoding="utf-8")
    run_cmd(article.draft, title="t", file=body, content=None, category=34, cover=None, original=True, ai=False, no_ai=False, ai_client="", ai_model="", ai_agent="", cvid=None)
    sent = [c for c in fake_api.calls if "addupdate" in c[1]][0][2]
    assert article.AI_NOTICE_STEM not in sent["content"]

# ── watch ───────────────────────────────────────────────────────────────────


def test_watch_returns_immediately_when_published(fake_api):
    fake_api.result_for = {"article/view": {"state": 0, "title": "T"}}
    out = run_cmd(article.watch, cvid=1, interval=1, timeout=10)
    assert out["state"] == "已发布"
    assert out["polls"] == 1
    assert "rejected" not in out


def test_watch_reports_rejection_with_reason(fake_api):
    fake_api.result_for = {"article/view": {"state": 2, "title": "T", "reason": "标题不合规"}}
    out = run_cmd(article.watch, cvid=1, interval=1, timeout=10)
    assert out["state"] == "未通过"
    assert out["rejected"] is True
    assert out["reason"] == "标题不合规"


def test_watch_times_out_while_still_in_review(fake_api):
    """一直 -2 就该超时报错，而不是无限转下去。"""
    fake_api.result_for = {"article/view": {"state": -2, "title": "T"}}
    with pytest.raises(BiliError) as ei:
        run_cmd(article.watch, cvid=1, interval=0.01, timeout=0.2)
    assert "审核中" in str(ei.value)

# ── embed ───────────────────────────────────────────────────────────────────


def test_find_block_end_after_paragraph():
    html = "<p>第一段</p><p>目标段落</p><p>第三段</p>"
    pos = article._find_block_end(html, "目标段落")
    assert html[:pos] == "<p>第一段</p><p>目标段落</p>"


def test_find_block_end_rejects_missing_anchor():
    with pytest.raises(BiliError):
        article._find_block_end("<p>没有那个词</p>", "目标段落")


def test_find_block_end_rejects_ambiguous_anchor():
    """锚点出现两次就必须报错，不能挑第一个。"""
    with pytest.raises(BiliError) as ei:
        article._find_block_end("<p>重复</p><p>重复</p>", "重复")
    assert "不唯一" in str(ei.value)


def test_figure_matches_editor_markup():
    fig = article._figure("https://x/y.png", "说明")
    assert fig.startswith('<figure class="img-box"')
    assert '<img src="https://x/y.png">' in fig
    assert '<figcaption class="caption"' in fig
    assert "说明" in fig


def test_embed_inserts_after_anchor(fake_api, tmp_path):
    fake_api.result_for = {"upcover": {"url": "http://article.biliimg.com/z.png"}}
    body = tmp_path / "b.html"
    body.write_text("<p>一</p><p>二</p><p>三</p>", encoding="utf-8")
    img = tmp_path / "i.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    out = tmp_path / "o.html"
    res = run_cmd(article.embed, file=body, image=img, after="二", caption="图注", out=out)
    merged = out.read_text(encoding="utf-8")
    assert merged.index("z.png") < merged.index("三")
    assert merged.index("二") < merged.index("z.png")
    assert res["written"] == str(out)


def test_embed_checks_anchor_before_uploading(fake_api, tmp_path):
    """锚点不存在时不该浪费一次上传请求。"""
    body = tmp_path / "b.html"
    body.write_text("<p>一</p>", encoding="utf-8")
    img = tmp_path / "i.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(BiliError):
        run_cmd(article.embed, file=body, image=img, after="不存在", caption="", out=None)
    assert fake_api.calls == []

def test_delete_accepts_draft_when_article_read_fails(fake_api):
    """帮助里写「草稿或已发布都行」，所以文章接口读不到时要退到草稿接口。"""
    fake_api.result_for = {"draft/view": {"title": "草稿标题"}, "draft/delete": {}}
    out = run_cmd(article.delete, cvid=42)
    assert out["deleted"] is True
    assert out["kind"] == "draft"
    assert out["title"] == "草稿标题"
    # 草稿必须用 draft/delete；用 article/delete 会报 37104
    assert any("draft/delete" in c[1] for c in fake_api.calls)
    assert not any("article/delete" in c[1] for c in fake_api.calls)


def test_delete_refuses_when_neither_read_works(fake_api):
    with pytest.raises(BiliError) as ei:
        run_cmd(article.delete, cvid=999)
    assert "找不到" in str(ei.value)
    assert not any("article/delete" in c[1] for c in fake_api.calls)

def test_ai_notice_uses_feat_for_model():
    """沿用 NixKits 作者区块的写法：名字后面跟 feat. 模型。"""
    text = article._ai_notice_text("DeepSeek Harness", "DeepSeek V4.1 Flash", "小爪（キツのめ）")
    lines = text.split("<br>")
    assert lines[0] == article.AI_NOTICE_STEM
    assert lines[1] == "智能体：小爪（キツのめ） feat. DeepSeek V4.1 Flash"
    assert lines[2] == "运行时：DeepSeek Harness"


def test_ai_notice_without_agent_does_not_fake_feat():
    """没有名字时不该硬套 feat.——`智能体：feat. X` 读起来是错的。"""
    text = article._ai_notice_text("", "DeepSeek V4.1 Flash", "")
    assert text.split("<br>")[1] == "模型：DeepSeek V4.1 Flash"


def test_ai_notice_skips_empty_fields():
    text = article._ai_notice_text("", "", "小爪")
    assert text.split("<br>")[1] == "智能体：小爪"
    assert "运行时" not in text


def test_ai_notice_does_not_guess_client():
    """工具不假定自己在哪个运行时里跑——没给就什么都不写。"""
    assert article._ai_notice_text() == article.AI_NOTICE_STEM


def test_draft_warns_when_ai_note_missing(fake_api, tmp_path, monkeypatch):
    """只给 --ai 不给 --ai-note 时，dry-run 里就要看得见警告。"""
    monkeypatch.setattr(article.output.settings, "dry_run", True, raising=False)
    body = tmp_path / "b.html"
    body.write_text("<p>x</p>", encoding="utf-8")
    out = run_cmd(article.draft, title="t", file=body, content=None, category=34, cover=None,
                  original=True, ai=True, no_ai=False, ai_client="Harness", ai_model="", ai_agent="", cvid=None)
    assert out["dry_run"] is True
    assert "warning" in out
    assert "ai-model" in out["warning"]

def test_publish_parses_cvid_from_preview_url(fake_api):
    """回归：真实 submit 返回的是 /read/preview/<id>，preview 后面没有 c。
    之前正则只认 group(1)，这条路径 group(1) 是 None，于是 int(None) 抛 TypeError——
    而那时 submit 已经执行完，文章已经发出去了，用户看到的是「报错但已发布」。"""
    fake_api.result_for = {
        "draft/view": {"title": "T", "content": "<p>x</p>", "category": {"id": 34}},
        "article/submit": {"state": -2, "view_url": "https://www.bilibili.com/read/preview/53767611"},
    }
    out = run_cmd(article.publish, cvid=386734, category=None)
    assert out["cvid"] == 53767611
    assert out["draft_id"] == 386734
    assert "warning" not in out


def test_publish_parses_cvid_from_cv_url(fake_api):
    fake_api.result_for = {
        "draft/view": {"title": "T", "content": "<p>x</p>", "category": {"id": 34}},
        "article/submit": {"state": 0, "view_url": "https://www.bilibili.com/read/cv53767611"},
    }
    assert run_cmd(article.publish, cvid=1, category=None)["cvid"] == 53767611


def test_publish_without_view_url_does_not_crash(fake_api):
    """submit 没给 view_url 时也要正常返回，不能 int(None)。"""
    fake_api.result_for = {
        "draft/view": {"title": "T", "content": "<p>x</p>", "category": {"id": 34}},
        "article/submit": {"state": -2},
    }
    out = run_cmd(article.publish, cvid=9, category=None)
    assert out["cvid"] is None
    assert "warning" in out

def test_draft_new_without_ai_flag_adds_nothing(fake_api, tmp_path):
    """新建草稿：不给 --ai 就是不加，干净起步。"""
    fake_api.result_for = {"draft/addupdate": {"aid": 601}}
    body = tmp_path / "b.html"
    body.write_text("<p>x</p>", encoding="utf-8")
    run_cmd(article.draft, title="t", file=body, content=None, category=34, cover=None,
            original=True, ai=False, no_ai=False, ai_client="", ai_model="", ai_agent="", cvid=None)
    sent = [c for c in fake_api.calls if "addupdate" in c[1]][0][2]
    assert article.AI_NOTICE_STEM not in sent["content"]


def test_draft_update_without_ai_flag_keeps_existing_notice(fake_api, tmp_path):
    """更新草稿：正文里已有声明时不给 --ai 要保持它——否则改个错字就把声明抹掉了。"""
    # 草稿里现在有声明；新正文里没有。判断依据应该是前者。
    fake_api.result_for = {
        "draft/view": {"content": "<p>旧</p>\n<p>" + article.AI_NOTICE_STEM + "智能体：小爪</p>"},
        "draft/addupdate": {"aid": 602},
    }
    body = tmp_path / "b.html"
    body.write_text("<p>x</p>", encoding="utf-8")
    run_cmd(article.draft, title="t", file=body, content=None, category=34, cover=None,
            original=True, ai=False, no_ai=False, ai_client="", ai_model="", ai_agent="", cvid=602)
    sent = [c for c in fake_api.calls if "addupdate" in c[1]][0][2]
    assert article.AI_NOTICE_STEM in sent["content"]


def test_draft_update_with_no_ai_flag_removes_notice(fake_api, tmp_path):
    """显式 --no-ai 才移除。"""
    fake_api.result_for = {
        "draft/view": {"content": "<p>旧</p>\n<p>" + article.AI_NOTICE_STEM + "智能体：小爪</p>"},
        "draft/addupdate": {"aid": 603},
    }
    body = tmp_path / "b.html"
    body.write_text("<p>x</p>", encoding="utf-8")
    run_cmd(article.draft, title="t", file=body, content=None, category=34, cover=None,
            original=True, ai=False, no_ai=True, ai_client="", ai_model="", ai_agent="", cvid=603)
    sent = [c for c in fake_api.calls if "addupdate" in c[1]][0][2]
    assert article.AI_NOTICE_STEM not in sent["content"]

# ── 声明正则必须能认出「带 <br> 的声明」 ─────────────────────────────────


def test_notice_regex_matches_multiline_notice():
    """回归：声明内部用 <br> 分行，正则要是用 [^<]* 就会在第一个 <br> 停住，
    于是既保持不住（update 时认不出来）也清不掉（--no-ai 删不掉）。"""
    body = ('<p>正文</p>\n<p>' + article.AI_NOTICE_STEM
            + '智能体：小爪 feat. DeepSeek<br>运行时：Harness</p>')
    assert article.AI_NOTICE_RE.search(body) is not None


def test_notice_regex_strips_multiline_notice():
    body = ('<p>正文</p>\n<p>' + article.AI_NOTICE_STEM
            + '智能体：小爪<br>运行时：Harness</p>')
    assert article.AI_NOTICE_RE.sub("", body).strip() == "<p>正文</p>"


def test_multiline_notice_is_idempotent():
    body = "<p>正文</p>"
    once = article._apply_ai_notice(body, True, "Harness", "DeepSeek", "小爪")
    twice = article._apply_ai_notice(once, True, "Harness", "DeepSeek", "小爪")
    assert once == twice
    assert twice.count(article.AI_NOTICE_STEM) == 1

def test_update_preserves_full_notice_when_no_details_given(fake_api, tmp_path):
    """回归：只保住「有声明」而丢掉模型/智能体，等于把可追责的声明降级成空话。"""
    old = ("<p>旧</p>\n<p>" + article.AI_NOTICE_STEM
           + "智能体：小爪（キツのめ） feat. DeepSeek V4.1 Flash<br>运行时：Harness</p>")
    fake_api.result_for = {"draft/view": {"content": old}, "draft/addupdate": {"aid": 604}}
    body = tmp_path / "b.html"
    body.write_text("<p>新正文</p>", encoding="utf-8")
    run_cmd(article.draft, title="t", file=body, content=None, category=34, cover=None,
            original=True, ai=False, no_ai=False, ai_client="", ai_model="", ai_agent="", cvid=604)
    sent = [c for c in fake_api.calls if "addupdate" in c[1]][0][2]
    assert "DeepSeek V4.1 Flash" in sent["content"]
    assert "小爪（キツのめ）" in sent["content"]
    assert sent["content"].count(article.AI_NOTICE_STEM) == 1
