import hashlib
import hmac
import json
import logging
import os
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .auth import get_current_user
from .database import get_db
from .models import OrderDB, ProductDB, UserDB

logger = logging.getLogger(__name__)

router = APIRouter()

# Stripe requires a Checkout Session to stay open for at least 30 minutes.
CHECKOUT_MINUTES = 35


# ---------- Stripe helpers ----------
def create_checkout_session(order: OrderDB, base_url: str) -> str:
    """Create a hosted Stripe Checkout page for the order and return its URL."""
    secret_key = os.getenv("STRIPE_SECRET_KEY", "")
    if not secret_key:
        raise HTTPException(status_code=503, detail="Payments are not configured")

    import stripe

    currency = os.getenv("STRIPE_CURRENCY", "usd").lower()
    line_items = [
        {
            "quantity": item.quantity,
            "price_data": {
                "currency": currency,
                "unit_amount": int(round(item.unit_price * 100)),
                "product_data": {"name": item.product_name},
            },
        }
        for item in order.items
    ]

    try:
        client = stripe.StripeClient(secret_key)
        session = client.v1.checkout.sessions.create(
            {
                "mode": "payment",
                "client_reference_id": str(order.id),
                "metadata": {"order_id": str(order.id)},
                "line_items": line_items,
                "success_url": f"{base_url}/payment/success?order_id={order.id}",
                "cancel_url": f"{base_url}/payment/cancel?order_id={order.id}",
                "expires_at": int(time.time()) + CHECKOUT_MINUTES * 60,
            }
        )
    except Exception:
        logger.exception("Could not create a Stripe Checkout Session")
        raise HTTPException(status_code=502, detail="Could not start the payment")

    return session.url


def verify_signature(
    payload: bytes, header: str, secret: str, tolerance: int = 300
) -> bool:
    """Check the Stripe-Signature header of a webhook request."""
    try:
        parts = [piece.split("=", 1) for piece in header.split(",")]
        timestamp = next(value for key, value in parts if key == "t")
        signatures = [value for key, value in parts if key == "v1"]
        sent_at = int(timestamp)
    except (ValueError, StopIteration):
        return False

    if not signatures:
        return False
    if abs(time.time() - sent_at) > tolerance:
        return False

    signed_payload = timestamp.encode() + b"." + payload
    expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in signatures)


# ---------- Order updates ----------
def mark_paid(db: Session, order_id: int) -> None:
    order = db.get(OrderDB, order_id)
    if order and order.status == "pending_payment":
        order.status = "paid"
        db.commit()


def cancel_and_restock(db: Session, order_id: int) -> None:
    order = db.get(OrderDB, order_id)
    if not order or order.status != "pending_payment":
        return
    for item in order.items:
        if item.product_id is None:
            continue
        product = (
            db.query(ProductDB)
            .filter(ProductDB.id == item.product_id)
            .with_for_update()
            .first()
        )
        if product:
            product.stock += item.quantity
    order.status = "cancelled"
    db.commit()


# ---------- Routes ----------
@router.post("/orders/{order_id}/pay", tags=["payments"])
def pay_for_order(
    order_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    order = db.get(OrderDB, order_id)
    if not order or order.user_id != user.id:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != "pending_payment":
        raise HTTPException(status_code=409, detail=f"Order is already {order.status}")

    base_url = (os.getenv("PUBLIC_URL") or str(request.base_url)).rstrip("/")
    url = create_checkout_session(order, base_url)
    return {"order_id": order.id, "checkout_url": url}


@router.post("/webhooks/stripe", tags=["payments"])
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook is not configured")

    payload = await request.body()
    header = request.headers.get("stripe-signature", "")
    if not verify_signature(payload, header, secret):
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        event = json.loads(payload)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")

    session = (event.get("data") or {}).get("object") or {}
    reference = session.get("client_reference_id") or (
        session.get("metadata") or {}
    ).get("order_id")
    try:
        order_id = int(reference)
    except (TypeError, ValueError):
        return {"received": True}

    event_type = event.get("type")
    if event_type == "checkout.session.completed":
        if session.get("payment_status") == "paid":
            mark_paid(db, order_id)
    elif event_type == "checkout.session.expired":
        cancel_and_restock(db, order_id)

    return {"received": True}


@router.get("/payment/success", tags=["payments"])
def payment_success(order_id: int | None = None):
    return {
        "message": "Thank you! Your payment is being confirmed.",
        "order_id": order_id,
    }


@router.get("/payment/cancel", tags=["payments"])
def payment_cancel(order_id: int | None = None):
    return {
        "message": "Payment was cancelled. Your order is held for a short time.",
        "order_id": order_id,
    }
