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


def test_deleted_archive_reports_deletion_not_comment_settings():
    """A deleted archive also carries reply.state=1; the message must name the real cause."""
    from bili_cli.commands.upload import edit_blocker

    reason = edit_blocker({
        "archive": {"aid": 1, "state": -100},
        "reply": {"state": 1},
    })

    assert reason is not None
    assert "删除" in reason
    assert "评论区" not in reason


def test_comment_blocker_still_fires_for_live_archive():
    from bili_cli.commands.upload import edit_blocker

    reason = edit_blocker({"archive": {"aid": 1, "state": 0}, "reply": {"state": 1}})

    assert reason is not None
    assert "评论区" in reason
