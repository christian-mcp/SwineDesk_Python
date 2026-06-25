"""Unit tests for Beta Test Mode broker-gated outbound approvals.

Hermetic: no Docker, no Twilio, no backend. send_sms_raw is stubbed with a
recorder so we can assert exactly who would have been texted.

Run: ./venv/bin/pytest test_beta_approvals.py -q
"""

from __future__ import annotations

import asyncio

import pytest

from swinedesk import beta_approvals, notifications
from swinedesk.settings import settings

BROKER_SMS = "+15550000001"    # phone that resolves to the broker on inbound
BROKER_ALERT = "+15550000099"  # where held drafts are texted for review
SELLER = "+15551112222"        # an "other user"


def _run(coro):
    return asyncio.run(coro)


class _Recorder:
    """Stand-in for notifications.send_sms_raw that records every send."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, to_phone: str, message: str) -> dict[str, object]:
        self.calls.append((to_phone, message))
        return {"success": True, "to_phone": to_phone}


def _fresh_store(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(beta_approvals, "_items", {})
    monkeypatch.setattr(beta_approvals, "_seq", 0)
    monkeypatch.setattr(beta_approvals, "_loaded", False)
    monkeypatch.setattr(beta_approvals, "_warned_no_channel", False)
    monkeypatch.setattr(settings, "beta_approval_store_path", tmp_path / "beta.json")
    monkeypatch.setattr(settings, "session_store_path", tmp_path / "sessions.json")


@pytest.fixture
def beta_on(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "beta_test_mode", True)
    monkeypatch.setattr(settings, "broker_sms_phones", BROKER_SMS)
    monkeypatch.setattr(settings, "broker_alert_phone", BROKER_ALERT)
    monkeypatch.setattr(settings, "partner_phone", "")
    _fresh_store(monkeypatch, tmp_path)
    rec = _Recorder()
    monkeypatch.setattr(beta_approvals, "send_sms_raw", rec)
    monkeypatch.setattr(notifications, "send_sms_raw", rec)
    return rec


def test_should_intercept_matrix(beta_on):
    assert beta_approvals.should_intercept(SELLER) is True
    assert beta_approvals.should_intercept(BROKER_SMS) is False  # broker is exempt
    assert beta_approvals.should_intercept("") is False
    assert beta_approvals.should_intercept(None) is False


def test_beta_off_passes_through(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "beta_test_mode", False)
    monkeypatch.setattr(settings, "broker_sms_phones", BROKER_SMS)
    _fresh_store(monkeypatch, tmp_path)
    rec = _Recorder()
    monkeypatch.setattr(beta_approvals, "send_sms_raw", rec)
    monkeypatch.setattr(notifications, "send_sms_raw", rec)

    res = _run(notifications.send_sms_notification(SELLER, "hi"))
    assert res.get("success") is True
    assert (SELLER, "hi") in rec.calls           # sent straight to the user
    assert _run(beta_approvals.list_pending()) == []  # nothing queued


def test_no_broker_channel_fails_open(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "beta_test_mode", True)
    monkeypatch.setattr(settings, "broker_sms_phones", "")
    monkeypatch.setattr(settings, "broker_alert_phone", "")
    monkeypatch.setattr(settings, "partner_phone", "")
    _fresh_store(monkeypatch, tmp_path)
    assert beta_approvals.should_intercept(SELLER) is False


def test_gate_queues_and_notifies_broker(beta_on):
    res = _run(notifications.send_sms_notification(SELLER, "your deal is matched"))

    assert res["queued_for_approval"] is True
    pending = _run(beta_approvals.list_pending())
    assert len(pending) == 1
    assert pending[0]["to_phone"] == SELLER
    assert pending[0]["message"] == "your deal is matched"

    # Broker was texted the draft; the seller was NOT sent anything.
    assert len(beta_on.calls) == 1
    assert beta_on.calls[0][0] == BROKER_ALERT
    assert "your deal is matched" in beta_on.calls[0][1]
    assert all(to != SELLER for to, _ in beta_on.calls)


def test_broker_recipient_not_gated(beta_on):
    res = _run(notifications.send_sms_notification(BROKER_SMS, "broker note"))
    assert res.get("success") is True
    assert (BROKER_SMS, "broker note") in beta_on.calls
    assert _run(beta_approvals.list_pending()) == []


def test_approve_sends_draft(beta_on):
    _run(notifications.send_sms_notification(SELLER, "draft text"))
    beta_on.calls.clear()

    out = _run(beta_approvals.resolve_approval("1", decision="approve"))
    assert out["delivered"] is True
    assert out["revised"] is False
    assert (SELLER, "draft text") in beta_on.calls
    assert _run(beta_approvals.list_pending()) == []


def test_revise_sends_replacement(beta_on):
    _run(notifications.send_sms_notification(SELLER, "draft text"))
    beta_on.calls.clear()

    out = _run(
        beta_approvals.resolve_approval("1", decision="revise", message="better wording")
    )
    assert out["revised"] is True
    assert (SELLER, "better wording") in beta_on.calls
    assert (SELLER, "draft text") not in beta_on.calls
    assert _run(beta_approvals.list_pending()) == []


def test_replacement_text_wins_over_approve(beta_on):
    _run(notifications.send_sms_notification(SELLER, "draft text"))
    beta_on.calls.clear()

    # Broker said "approve" but also dictated new words — the words win.
    out = _run(
        beta_approvals.resolve_approval("1", decision="approve", message="override")
    )
    assert out["revised"] is True
    assert (SELLER, "override") in beta_on.calls


def test_skip_drops_without_sending(beta_on):
    _run(notifications.send_sms_notification(SELLER, "draft text"))
    beta_on.calls.clear()

    out = _run(beta_approvals.resolve_approval("1", decision="skip"))
    assert "Skipped" in out["result"]
    assert beta_on.calls == []  # nothing sent to anyone
    assert _run(beta_approvals.list_pending()) == []


def test_resolve_unknown_id_errors(beta_on):
    out = _run(beta_approvals.resolve_approval("99", decision="approve"))
    assert "error" in out
    assert beta_on.calls == []


def test_resolved_drafts_leave_pending_list(beta_on):
    _run(notifications.send_sms_notification(SELLER, "one"))
    _run(notifications.send_sms_notification(SELLER, "two"))
    assert len(_run(beta_approvals.list_pending())) == 2

    _run(beta_approvals.resolve_approval("1", decision="approve"))
    remaining = _run(beta_approvals.list_pending())
    assert [r["id"] for r in remaining] == ["2"]
