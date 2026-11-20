from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from .auth import get_current_user, require_admin
from .database import get_db
from .models import DeliveryDB, OrderDB, TrackingEventDB, UserDB, utcnow

router = APIRouter()

# The order in which a parcel moves. It can stay at a step or move forward.
STEP_ORDER = {"shipped": 0, "in_transit": 1, "out_for_delivery": 2, "delivered": 3}


# ---------- Schemas ----------
class AddressIn(BaseModel):
    recipient_name: str = Field(min_length=1, max_length=100)
    phone: str = Field(min_length=7, max_length=30)
    address_line1: str = Field(min_length=1, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=1, max_length=100)
    postal_code: str = Field(min_length=3, max_length=20)
    country: str = Field(min_length=2, max_length=100)


class AddressOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    recipient_name: str
    phone: str
    address_line1: str
    address_line2: str | None
    city: str
    state: str
    postal_code: str
    country: str


class TrackingEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str
    location: str | None
    note: str | None
    created_at: datetime


class DeliveryOut(BaseModel):
    order_id: int
    order_status: str
    delivery_status: str
    address: AddressOut | None
    carrier: str | None
    tracking_number: str | None
    tracking_url: str | None
    shipped_at: datetime | None
    delivered_at: datetime | None
    timeline: list[TrackingEventOut]


class ShipIn(BaseModel):
    carrier: str = Field(min_length=1, max_length=100)
    tracking_number: str = Field(min_length=3, max_length=100)
    tracking_url: str | None = Field(default=None, max_length=500)
    location: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=300)

    @field_validator("tracking_url")
    @classmethod
    def check_url(cls, value):
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("tracking_url must start with http:// or https://")
        return value


class TrackingIn(BaseModel):
    status: Literal["in_transit", "out_for_delivery", "delivered"]
    location: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=300)


class AdminOrderOut(BaseModel):
    id: int
    user_email: str | None
    status: str
    delivery_status: str
    total: float
    created_at: datetime


# ---------- Helpers ----------
def load_order(db: Session, order_id: int, user: UserDB, allow_admin: bool = False):
    order = db.get(OrderDB, order_id)
    is_owner = order is not None and order.user_id == user.id
    if not order or not (is_owner or (allow_admin and user.is_admin)):
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def find_delivery(db: Session, order_id: int):
    return db.query(DeliveryDB).filter(DeliveryDB.order_id == order_id).first()


def build_delivery(db: Session, order: OrderDB) -> DeliveryOut:
    delivery = find_delivery(db, order.id)
    events = (
        db.query(TrackingEventDB)
        .filter(TrackingEventDB.order_id == order.id)
        .order_by(TrackingEventDB.id)
        .all()
    )
    timeline = [
        TrackingEventOut(
            status="order_placed",
            location=None,
            note="Order placed",
            created_at=order.created_at,
        )
    ]
    timeline += [TrackingEventOut.model_validate(e) for e in events]

    if delivery is None:
        return DeliveryOut(
            order_id=order.id,
            order_status=order.status,
            delivery_status="awaiting_address",
            address=None,
            carrier=None,
            tracking_number=None,
            tracking_url=None,
            shipped_at=None,
            delivered_at=None,
            timeline=timeline,
        )

    return DeliveryOut(
        order_id=order.id,
        order_status=order.status,
        delivery_status=delivery.status,
        address=AddressOut.model_validate(delivery),
        carrier=delivery.carrier,
        tracking_number=delivery.tracking_number,
        tracking_url=delivery.tracking_url,
        shipped_at=delivery.shipped_at,
        delivered_at=delivery.delivered_at,
        timeline=timeline,
    )


# ---------- Customer routes ----------
@router.put("/orders/{order_id}/delivery", response_model=DeliveryOut, tags=["delivery"])
def set_delivery_address(
    order_id: int,
    body: AddressIn,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    order = load_order(db, order_id, user)
    if order.status not in ("pending_payment", "paid"):
        raise HTTPException(
            status_code=409,
            detail=f"The address can no longer be changed: order is {order.status}",
        )

    delivery = find_delivery(db, order.id)
    if delivery is None:
        db.add(DeliveryDB(order_id=order.id, status="processing", **body.model_dump()))
    else:
        for key, value in body.model_dump().items():
            setattr(delivery, key, value)
    db.commit()
    return build_delivery(db, order)


@router.get("/orders/{order_id}/delivery", response_model=DeliveryOut, tags=["delivery"])
def view_delivery(
    order_id: int,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    order = load_order(db, order_id, user, allow_admin=True)
    return build_delivery(db, order)


# ---------- Admin routes ----------
@router.get("/admin/orders", response_model=list[AdminOrderOut], tags=["admin"])
def admin_list_orders(
    status: str | None = None,
    db: Session = Depends(get_db),
    admin: UserDB = Depends(require_admin),
):
    query = db.query(OrderDB)
    if status:
        query = query.filter(OrderDB.status == status)
    orders = query.order_by(OrderDB.id.desc()).limit(200).all()

    order_ids = [o.id for o in orders]
    user_ids = list({o.user_id for o in orders})
    delivery_status = {
        d.order_id: d.status
        for d in db.query(DeliveryDB).filter(DeliveryDB.order_id.in_(order_ids))
    }
    emails = {
        u.id: u.email for u in db.query(UserDB).filter(UserDB.id.in_(user_ids))
    }

    return [
        AdminOrderOut(
            id=o.id,
            user_email=emails.get(o.user_id),
            status=o.status,
            delivery_status=delivery_status.get(o.id, "awaiting_address"),
            total=o.total,
            created_at=o.created_at,
        )
        for o in orders
    ]


@router.post("/admin/orders/{order_id}/ship", response_model=DeliveryOut, tags=["admin"])
def ship_order(
    order_id: int,
    body: ShipIn,
    db: Session = Depends(get_db),
    admin: UserDB = Depends(require_admin),
):
    order = db.get(OrderDB, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status != "paid":
        raise HTTPException(
            status_code=409,
            detail=f"Order is {order.status}; only paid orders can be shipped",
        )

    delivery = find_delivery(db, order.id)
    if delivery is None:
        raise HTTPException(
            status_code=409, detail="The customer has not given a delivery address yet"
        )
    if delivery.status != "processing":
        raise HTTPException(status_code=409, detail="Order has already been shipped")

    delivery.status = "shipped"
    delivery.carrier = body.carrier
    delivery.tracking_number = body.tracking_number
    delivery.tracking_url = body.tracking_url
    delivery.shipped_at = utcnow()
    order.status = "shipped"
    db.add(
        TrackingEventDB(
            order_id=order.id,
            status="shipped",
            location=body.location,
            note=body.note or f"Shipped with {body.carrier}",
        )
    )
    db.commit()
    return build_delivery(db, order)


@router.post(
    "/admin/orders/{order_id}/tracking", response_model=DeliveryOut, tags=["admin"]
)
def add_tracking_event(
    order_id: int,
    body: TrackingIn,
    db: Session = Depends(get_db),
    admin: UserDB = Depends(require_admin),
):
    order = db.get(OrderDB, order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    delivery = find_delivery(db, order.id)
    if delivery is None or delivery.status == "processing":
        raise HTTPException(status_code=409, detail="Order has not been shipped yet")
    if delivery.status == "delivered":
        raise HTTPException(status_code=409, detail="Order is already delivered")
    if STEP_ORDER[body.status] < STEP_ORDER[delivery.status]:
        raise HTTPException(status_code=409, detail="Tracking cannot go backwards")

    delivery.status = body.status
    if body.status == "delivered":
        delivery.delivered_at = utcnow()
        order.status = "delivered"
    db.add(
        TrackingEventDB(
            order_id=order.id,
            status=body.status,
            location=body.location,
            note=body.note,
        )
    )
    db.commit()
    return build_delivery(db, order)
