"""Extra CA bundle: merge user PEM with certifi, point libcurl and httpx at it."""

import os
import time

import pytest

from bili_cli import ca_bundle

CERT_A = """-----BEGIN CERTIFICATE-----
QUFBQQ==
-----END CERTIFICATE-----
"""
CERT_B = """-----BEGIN CERTIFICATE-----
QkJCQg==
-----END CERTIFICATE-----
"""


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    monkeypatch.delenv("BILI_CLI_CA_BUNDLE", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setenv("BILI_CLI_HOME", str(tmp_path / "state"))

    base = tmp_path / "certifi.pem"
    base.write_text(CERT_A, encoding="utf-8")
    monkeypatch.setattr(ca_bundle, "certifi_path", lambda: base)

    # install_ca_bundle 直接写 os.environ，monkeypatch 管不到，测试结束后要自己恢复
    saved = {key: os.environ.get(key) for key in ("CURL_CA_BUNDLE", "SSL_CERT_FILE")}
    yield tmp_path
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_no_extra_bundle_leaves_env_alone(env):
    assert ca_bundle.install_ca_bundle() is None
    assert "CURL_CA_BUNDLE" not in os.environ
    assert "SSL_CERT_FILE" not in os.environ


def test_explicit_env_wins(env, monkeypatch):
    monkeypatch.setenv("CURL_CA_BUNDLE", "C:/already/here.pem")
    assert ca_bundle.install_ca_bundle() == "C:/already/here.pem"


def test_explicit_ssl_cert_file_is_not_overwritten(env, monkeypatch):
    extra = env / "extra.pem"
    extra.write_text(CERT_B, encoding="utf-8")
    monkeypatch.setenv("BILI_CLI_CA_BUNDLE", str(extra))
    monkeypatch.setenv("SSL_CERT_FILE", "C:/already/here.pem")

    merged = ca_bundle.install_ca_bundle()
    assert merged is not None
    assert os.environ["CURL_CA_BUNDLE"] == merged
    assert os.environ["SSL_CERT_FILE"] == "C:/already/here.pem"


def test_extra_bundle_is_merged_with_certifi(env, monkeypatch):
    extra = env / "extra.pem"
    extra.write_text(CERT_B, encoding="utf-8")
    monkeypatch.setenv("BILI_CLI_CA_BUNDLE", str(extra))

    merged = ca_bundle.install_ca_bundle()
    assert merged is not None
    assert os.environ["CURL_CA_BUNDLE"] == merged
    assert os.environ["SSL_CERT_FILE"] == merged

    text = open(merged, encoding="utf-8").read()
    assert "QUFBQQ==" in text and "QkJCQg==" in text


def test_default_location_uses_the_config_directory(env):
    """不设 BILI_CLI_CA_BUNDLE 时，读 <BILI_CLI_HOME>/cacert-bili.pem。"""
    home = env / "state"
    home.mkdir(parents=True, exist_ok=True)
    (home / ca_bundle.FILE_NAME).write_text(CERT_B, encoding="utf-8")

    merged = ca_bundle.install_ca_bundle()
    assert merged == str(home / ca_bundle.MERGED_NAME)
    assert "QkJCQg==" in open(merged, encoding="utf-8").read()


def test_merge_drops_duplicates(env):
    base = env / "certifi.pem"
    extra = env / "extra.pem"
    base.write_text(CERT_A + CERT_B, encoding="utf-8")
    extra.write_text(CERT_B, encoding="utf-8")
    assert ca_bundle.merge(base, extra).count("BEGIN CERTIFICATE") == 2


def test_repeated_calls_do_not_append_twice(env, monkeypatch):
    extra = env / "extra.pem"
    extra.write_text(CERT_B, encoding="utf-8")
    monkeypatch.setenv("BILI_CLI_CA_BUNDLE", str(extra))

    merged = ca_bundle.install_ca_bundle()
    size_once = len(open(merged, encoding="utf-8").read())
    ca_bundle.install_ca_bundle()
    assert len(open(merged, encoding="utf-8").read()) == size_once


def test_certifi_becoming_newer_triggers_a_remerge(env, monkeypatch):
    """certifi 升级（新增/吊销根证书）后必须重新合并，否则一直用旧的信任库。"""
    extra = env / "extra.pem"
    extra.write_text(CERT_B, encoding="utf-8")
    monkeypatch.setenv("BILI_CLI_CA_BUNDLE", str(extra))

    merged = ca_bundle.install_ca_bundle()
    assert open(merged, encoding="utf-8").read().count("BEGIN CERTIFICATE") == 2

    base = ca_bundle.certifi_path()
    base.write_text(CERT_B, encoding="utf-8")       # 只剩 B（和 extra 重复）→ 重新合并后应只有 1 张
    future = time.time() + 5
    os.utime(base, (future, future))

    ca_bundle.install_ca_bundle()
    assert open(merged, encoding="utf-8").read().count("BEGIN CERTIFICATE") == 1


def test_missing_extra_file_is_ignored(env, monkeypatch):
    monkeypatch.setenv("BILI_CLI_CA_BUNDLE", str(env / "nope.pem"))
    assert ca_bundle.install_ca_bundle() is None
