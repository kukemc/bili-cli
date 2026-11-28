"""Extra CA certificates for hosts whose chain the bundled store cannot complete.

Some Bilibili edges (the upos upload hosts in particular) hand out a chain that certifi alone cannot
finish, and on Windows an antivirus HTTPS scanner can re-sign the connection with a root certifi does
not know. libcurl then fails with `curl: (60) ... unable to get local issuer certificate (20)`.

Point `BILI_CLI_CA_BUNDLE` at a PEM with the missing certificates (or drop one at
`<config dir>/cacert-bili.pem`) and bili-cli merges it with the bundled store once, then points libcurl
(`CURL_CA_BUNDLE`) and httpx (`SSL_CERT_FILE`) at the merged file. Verification stays on; only the
trust store grows.
"""

from __future__ import annotations

import os
from pathlib import Path

FILE_NAME = "cacert-bili.pem"
MERGED_NAME = "cacert-merged.pem"


def install_ca_bundle() -> str | None:
    """Set the CA variables to certifi + the extra PEM, if an extra PEM exists. Never override the user."""
    merged = merged_bundle_path()
    current = os.environ.get("CURL_CA_BUNDLE")
    if current and Path(current) != merged:      # the user pointed curl at their own bundle
        return current
    extra = extra_bundle()
    if extra is None:
        return None
    try:
        if _stale(merged, extra):
            merged.parent.mkdir(parents=True, exist_ok=True)
            merged.write_text(merge(certifi_path(), extra), encoding="utf-8")
    except OSError:
        # A read-only state dir must not break every command; libcurl just keeps the default store.
        return None
    os.environ["CURL_CA_BUNDLE"] = str(merged)
    # curl_cffi reads CURL_CA_BUNDLE; httpx (runtime.web_client) only reads SSL_CERT_FILE / SSL_CERT_DIR.
    os.environ.setdefault("SSL_CERT_FILE", str(merged))
    return str(merged)


def _stale(merged: Path, extra: Path) -> bool:
    """Re-merge when the extra PEM *or certifi itself* changed (a certifi upgrade may add or drop roots)."""
    try:
        merged_at = merged.stat().st_mtime
    except OSError:
        return True
    return merged_at < extra.stat().st_mtime or merged_at < certifi_path().stat().st_mtime


def extra_bundle() -> Path | None:
    """The user's extra PEM: BILI_CLI_CA_BUNDLE first, then `<config dir>/cacert-bili.pem`."""
    env = os.environ.get("BILI_CLI_CA_BUNDLE")
    if env:
        path = Path(env).expanduser()
        return path if path.is_file() else None
    path = config_home() / FILE_NAME
    return path if path.is_file() else None


def config_home() -> Path:
    """Same directory config.home() uses, without importing the whole config module at package import."""
    env = os.environ.get("BILI_CLI_HOME")
    if env:
        return Path(env).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home()) / "bili-cli"
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "bili-cli"


def merged_bundle_path() -> Path:
    return config_home() / MERGED_NAME


def certifi_path() -> Path:
    import certifi

    return Path(certifi.where())


def merge(base: Path, extra: Path) -> str:
    """Concatenate two PEM files, dropping duplicated certificates."""
    seen: list[str] = []
    for bundle in (base, extra):
        text = bundle.read_text(encoding="utf-8", errors="replace")
        for block in text.split("-----END CERTIFICATE-----"):
            if "-----BEGIN CERTIFICATE-----" not in block:
                continue
            cert = block.strip() + "\n-----END CERTIFICATE-----\n"
            if cert not in seen:
                seen.append(cert)
    return "".join(seen)
