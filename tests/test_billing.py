"""Billing logic without a database (ADR-007): plans, the paywall rule, Razorpay signatures."""

import hashlib
import hmac

import pytest

from app.config import Settings
from core.billing import BillingState, allowed, plan, verify_payment, verify_webhook

TRIAL = 3600


@pytest.mark.parametrize(
    "state, expected",
    [
        (BillingState(active_seconds=0), "trial"),
        (BillingState(active_seconds=3599), "trial"),
        (BillingState(active_seconds=3600), "expired"),
        (BillingState(active_seconds=9000, subscription_status="active"), "active"),
        (BillingState(active_seconds=9000, subscription_status="authenticated"), "active"),
        (BillingState(active_seconds=9000, subscription_status="pending"), "past_due"),
        (BillingState(active_seconds=9000, subscription_status="halted"), "expired"),
        (BillingState(active_seconds=9000, subscription_status="cancelled"), "expired"),
        (BillingState(active_seconds=10, subscription_status="created"), "trial"),  # checkout not finished
    ],
)
def test_plan(state, expected):
    assert plan(state, TRIAL) == expected
    assert allowed(state, TRIAL) is (expected != "expired")


def _sign(secret: str, message: str) -> str:
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def test_payment_signature_is_payment_id_pipe_subscription_id():
    good = _sign("key_secret", "pay_123|sub_456")
    assert verify_payment("pay_123", "sub_456", good, "key_secret")
    assert not verify_payment("sub_456", "pay_123", good, "key_secret")  # order matters
    assert not verify_payment("pay_123", "sub_456", good, "other_secret")
    assert not verify_payment("pay_123", "sub_456", "", "key_secret")


def test_webhook_signature_covers_the_raw_body():
    body = b'{"event":"subscription.activated"}'
    sig = _sign("whsec", body.decode())
    assert verify_webhook(body, sig, "whsec")
    assert not verify_webhook(body + b" ", sig, "whsec")
    assert not verify_webhook(body, sig, "other")


def test_billing_needs_all_razorpay_settings():
    with pytest.raises(ValueError, match="BILLING_ENABLED=true requires"):
        Settings(billing_enabled=True, razorpay_key_id="rzp_test_x")
    s = Settings(
        billing_enabled=True, razorpay_key_id="rzp_test_x", razorpay_key_secret="s", razorpay_webhook_secret="w",
        razorpay_plan_id="plan_x",
    )  # fmt: skip
    assert "razorpay_key_secret" not in repr(s) and "razorpay_webhook_secret" not in repr(s)
