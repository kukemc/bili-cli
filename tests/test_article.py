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
    out = run_cmd(article.draft, title="标题", file=body, content=None, category=34, cover=None, cvid=None)
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
    out = run_cmd(article.image, path=p)
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
    run_cmd(article.update, cvid=7, file=body, content=None, title=None, cover=None, ai=None)
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
    assert out.endswith("<p>" + article.AI_NOTICE + "</p>")
    assert "<p>正文</p>" in out


def test_ai_notice_removed_when_disabled():
    once = article._apply_ai_notice("<p>正文</p>", True)
    assert article._apply_ai_notice(once, False) == "<p>正文</p>"


def test_ai_notice_is_idempotent():
    """重复开不会堆出两份。"""
    once = article._apply_ai_notice("<p>正文</p>", True)
    twice = article._apply_ai_notice(once, True)
    assert once == twice
    assert twice.count(article.AI_NOTICE) == 1


def test_ai_notice_replaces_stale_tail():
    """正文被重写后再开，旧声明要跟着走，而不是叠在中间。"""
    old = "<p>旧正文</p>\n<p>" + article.AI_NOTICE + "</p>"
    new = article._apply_ai_notice("<p>新正文</p>" + old[len("<p>旧正文</p>"):], True)
    assert new.count(article.AI_NOTICE) == 1
    assert new.rstrip().endswith("</p>")


def test_draft_sends_notice_when_ai_flag_set(fake_api, tmp_path):
    fake_api.result_for = {"draft/addupdate": {"aid": 555}}
    body = tmp_path / "b.html"
    body.write_text("<p>x</p>", encoding="utf-8")
    run_cmd(article.draft, title="t", file=body, content=None, category=34, cover=None, original=True, ai=True, cvid=None)
    sent = [c for c in fake_api.calls if "addupdate" in c[1]][0][2]
    assert article.AI_NOTICE in sent["content"]


def test_draft_omits_notice_by_default(fake_api, tmp_path):
    fake_api.result_for = {"draft/addupdate": {"aid": 556}}
    body = tmp_path / "b.html"
    body.write_text("<p>x</p>", encoding="utf-8")
    run_cmd(article.draft, title="t", file=body, content=None, category=34, cover=None, original=True, ai=False, cvid=None)
    sent = [c for c in fake_api.calls if "addupdate" in c[1]][0][2]
    assert article.AI_NOTICE not in sent["content"]
