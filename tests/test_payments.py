import hashlib
import hmac
import json
import time

import pytest

from app import payments
from app.database import SessionLocal
from app.models import ProductDB

WEBHOOK_SECRET = "whsec_test_secret_for_tests_only"

PAYER = {"email": "payer@example.com", "password": "payerpass123"}
STRANGER = {"email": "stranger@example.com", "password": "strangerpass123"}


def sign(payload, secret=WEBHOOK_SECRET, timestamp=None):
    timestamp = timestamp or int(time.time())
    signed = str(timestamp).encode() + b"." + payload
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def register_and_login(client, creds):
    response = client.post("/auth/register", json=creds)
    assert response.status_code == 201, response.text
    response = client.post(
        "/auth/login",
        data={"username": creds["email"], "password": creds["password"]},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture(scope="module")
def payer(client):
    return register_and_login(client, PAYER)


@pytest.fixture(scope="module")
def stranger(client):
    return register_and_login(client, STRANGER)


@pytest.fixture
def webhook_secret(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)


def make_product(stock=10):
    # Insert straight into the test database, so these tests do not depend
    # on the admin account that test_shop.py creates.
    with SessionLocal() as db:
        product = ProductDB(name="Lamp", price=20.0, stock=stock)
        db.add(product)
        db.commit()
        db.refresh(product)
        return product.id


def place_order(client, headers, product_id, quantity=2):
    client.delete("/cart", headers=headers)
    added = client.post(
        "/cart/items",
        json={"product_id": product_id, "quantity": quantity},
        headers=headers,
    )
    assert added.status_code == 201, added.text
    placed = client.post("/orders", headers=headers)
    assert placed.status_code == 201, placed.text
    return placed.json()


def send_event(client, event_type, order_id, secret=WEBHOOK_SECRET):
    event = {
        "id": "evt_test",
        "type": event_type,
        "data": {
            "object": {
                "client_reference_id": str(order_id),
                "payment_status": "paid",
            }
        },
    }
    payload = json.dumps(event).encode()
    return client.post(
        "/webhooks/stripe",
        content=payload,
        headers={
            "stripe-signature": sign(payload, secret),
            "content-type": "application/json",
        },
    )


def order_status(client, headers, order_id):
    return client.get(f"/orders/{order_id}", headers=headers).json()["status"]


def stock_of(client, product_id):
    return client.get(f"/products/{product_id}").json()["stock"]


def test_signature_accepts_valid_header():
    payload = b'{"hello": "world"}'
    assert payments.verify_signature(payload, sign(payload), WEBHOOK_SECRET)


def test_signature_rejects_wrong_secret_old_timestamp_and_junk():
    payload = b'{"hello": "world"}'
    assert not payments.verify_signature(payload, sign(payload, "other"), WEBHOOK_SECRET)
    old = int(time.time()) - 3600
    assert not payments.verify_signature(
        payload, sign(payload, timestamp=old), WEBHOOK_SECRET
    )
    assert not payments.verify_signature(payload, "", WEBHOOK_SECRET)
    assert not payments.verify_signature(payload, "garbage", WEBHOOK_SECRET)


def test_new_order_waits_for_payment(client, payer):
    order = place_order(client, payer, make_product())
    assert order["status"] == "pending_payment"


def test_pay_returns_checkout_link(client, payer, monkeypatch):
    monkeypatch.setattr(
        payments,
        "create_checkout_session",
        lambda order, base_url: "https://checkout.stripe.test/session",
    )
    order = place_order(client, payer, make_product())
    response = client.post(f"/orders/{order['id']}/pay", headers=payer)
    assert response.status_code == 200
    assert response.json()["checkout_url"] == "https://checkout.stripe.test/session"


def test_other_user_cannot_pay_for_an_order(client, payer, stranger):
    order = place_order(client, payer, make_product())
    response = client.post(f"/orders/{order['id']}/pay", headers=stranger)
    assert response.status_code == 404


def test_webhook_marks_order_paid(client, payer, webhook_secret):
    order = place_order(client, payer, make_product())
    response = send_event(client, "checkout.session.completed", order["id"])
    assert response.status_code == 200
    assert order_status(client, payer, order["id"]) == "paid"

    # A paid order cannot be paid for again
    again = client.post(f"/orders/{order['id']}/pay", headers=payer)
    assert again.status_code == 409


def test_expired_payment_cancels_order_and_restores_stock(
    client, payer, webhook_secret
):
    product_id = make_product(stock=10)
    order = place_order(client, payer, product_id, quantity=2)
    assert stock_of(client, product_id) == 8

    response = send_event(client, "checkout.session.expired", order["id"])
    assert response.status_code == 200
    assert order_status(client, payer, order["id"]) == "cancelled"
    assert stock_of(client, product_id) == 10


def test_late_expiry_does_not_cancel_a_paid_order(client, payer, webhook_secret):
    product_id = make_product(stock=10)
    order = place_order(client, payer, product_id, quantity=2)
    send_event(client, "checkout.session.completed", order["id"])
    send_event(client, "checkout.session.expired", order["id"])
    assert order_status(client, payer, order["id"]) == "paid"
    assert stock_of(client, product_id) == 8


def test_webhook_rejects_bad_signature(client, payer, webhook_secret):
    order = place_order(client, payer, make_product())
    response = send_event(
        client, "checkout.session.completed", order["id"], secret="wrong-secret"
    )
    assert response.status_code == 400
    assert order_status(client, payer, order["id"]) == "pending_payment"


def test_webhook_needs_a_configured_secret(client, payer, monkeypatch):
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    response = send_event(client, "checkout.session.completed", 1)
    assert response.status_code == 503
