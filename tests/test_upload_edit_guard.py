"""`upload edit` must refuse a bvid that is not one of the user's own archives."""

import pytest

from bili_cli.commands.upload import require_own_archive
from bili_cli.errors import BiliError


def test_rejects_view_without_archive():
    """B station answers 200 for someone else's bvid, with an empty archive."""
    with pytest.raises(BiliError) as exc:
        require_own_archive({"archive": {}}, "BV1w2Hw6KE1j")
    assert exc.value.code == "not_found"
    assert "BV1w2Hw6KE1j" in str(exc.value)


def test_rejects_missing_archive_key():
    with pytest.raises(BiliError) as exc:
        require_own_archive({}, "BV1xxx")
    assert exc.value.code == "not_found"


def test_rejects_zero_aid():
    """aid 0 is what build_edit would have sent to the edit endpoint."""
    with pytest.raises(BiliError):
        require_own_archive({"archive": {"aid": 0, "title": "x"}}, "BV1xxx")


def test_accepts_own_archive():
    require_own_archive({"archive": {"aid": 117557465975506, "title": "mine"}}, "BV1JGxQ6jERt")
