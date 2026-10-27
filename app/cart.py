from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from .auth import get_current_user
from .database import get_db
from .models import CartItemDB, OrderDB, OrderItemDB, ProductDB, UserDB

router = APIRouter()


# ---------- Schemas ----------
class CartAdd(BaseModel):
    product_id: int
    quantity: int = Field(default=1, ge=1, le=1000)


class CartUpdate(BaseModel):
    quantity: int = Field(ge=1, le=1000)


class CartItemOut(BaseModel):
    product_id: int
    name: str
    price: float
    quantity: int
    subtotal: float


class CartOut(BaseModel):
    items: list[CartItemOut]
    total: float


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: int | None
    product_name: str
    unit_price: float
    quantity: int
    subtotal: float


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    total: float
    created_at: datetime
    items: list[OrderItemOut]


# ---------- Helpers ----------
def build_cart(db: Session, user: UserDB) -> CartOut:
    rows = (
        db.query(CartItemDB)
        .filter(CartItemDB.user_id == user.id)
        .order_by(CartItemDB.id)
        .all()
    )
    items = [
        CartItemOut(
            product_id=r.product_id,
            name=r.product.name,
            price=r.product.price,
            quantity=r.quantity,
            subtotal=round(r.product.price * r.quantity, 2),
        )
        for r in rows
    ]
    total = round(sum(i.subtotal for i in items), 2)
    return CartOut(items=items, total=total)


def find_cart_item(db: Session, user: UserDB, product_id: int):
    return (
        db.query(CartItemDB)
        .filter(CartItemDB.user_id == user.id, CartItemDB.product_id == product_id)
        .first()
    )


# ---------- Cart ----------
@router.get("/cart", response_model=CartOut, tags=["cart"])
def view_cart(db: Session = Depends(get_db), user: UserDB = Depends(get_current_user)):
    return build_cart(db, user)


@router.post("/cart/items", response_model=CartOut, status_code=201, tags=["cart"])
def add_to_cart(
    body: CartAdd,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    product = db.get(ProductDB, body.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    existing = find_cart_item(db, user, body.product_id)
    new_qty = (existing.quantity if existing else 0) + body.quantity
    if new_qty > product.stock:
        raise HTTPException(
            status_code=400, detail=f"Only {product.stock} in stock"
        )

    if existing:
        existing.quantity = new_qty
    else:
        db.add(
            CartItemDB(
                user_id=user.id, product_id=body.product_id, quantity=body.quantity
            )
        )
    db.commit()
    return build_cart(db, user)


@router.put("/cart/items/{product_id}", response_model=CartOut, tags=["cart"])
def set_cart_quantity(
    product_id: int,
    body: CartUpdate,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    item = find_cart_item(db, user, product_id)
    if not item:
        raise HTTPException(status_code=404, detail="Item not in cart")
    if body.quantity > item.product.stock:
        raise HTTPException(
            status_code=400, detail=f"Only {item.product.stock} in stock"
        )
    item.quantity = body.quantity
    db.commit()
    return build_cart(db, user)


@router.delete("/cart/items/{product_id}", response_model=CartOut, tags=["cart"])
def remove_from_cart(
    product_id: int,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    item = find_cart_item(db, user, product_id)
    if item:
        db.delete(item)
        db.commit()
    return build_cart(db, user)


@router.delete("/cart", response_model=CartOut, tags=["cart"])
def clear_cart(
    db: Session = Depends(get_db), user: UserDB = Depends(get_current_user)
):
    db.query(CartItemDB).filter(CartItemDB.user_id == user.id).delete()
    db.commit()
    return build_cart(db, user)


# ---------- Orders ----------
@router.post("/orders", response_model=OrderOut, status_code=201, tags=["orders"])
def checkout(
    db: Session = Depends(get_db), user: UserDB = Depends(get_current_user)
):
    cart_items = db.query(CartItemDB).filter(CartItemDB.user_id == user.id).all()
    if not cart_items:
        raise HTTPException(status_code=400, detail="Your cart is empty")

    # Lock the product rows so two checkouts can't sell the same last item
    product_ids = sorted(i.product_id for i in cart_items)
    products = {
        p.id: p
        for p in db.query(ProductDB)
        .filter(ProductDB.id.in_(product_ids))
        .order_by(ProductDB.id)
        .with_for_update()
        .all()
    }

    order = OrderDB(user_id=user.id, status="placed", total=0)
    total = 0.0

    for item in cart_items:
        product = products.get(item.product_id)
        if product is None or product.stock < item.quantity:
            db.rollback()
            name = product.name if product else "A product in your cart"
            raise HTTPException(
                status_code=409, detail=f"Not enough stock for: {name}"
            )
        product.stock -= item.quantity
        order.items.append(
            OrderItemDB(
                product_id=product.id,
                product_name=product.name,
                unit_price=product.price,
                quantity=item.quantity,
            )
        )
        total += product.price * item.quantity
        db.delete(item)

    order.total = round(total, 2)
    db.add(order)
    db.commit()
    db.refresh(order)
    return OrderOut.model_validate(order)


@router.get("/orders", response_model=list[OrderOut], tags=["orders"])
def list_orders(
    db: Session = Depends(get_db), user: UserDB = Depends(get_current_user)
):
    orders = (
        db.query(OrderDB)
        .filter(OrderDB.user_id == user.id)
        .order_by(OrderDB.id.desc())
        .all()
    )
    return [OrderOut.model_validate(o) for o in orders]


@router.get("/orders/{order_id}", response_model=OrderOut, tags=["orders"])
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    order = db.get(OrderDB, order_id)
    if not order or order.user_id != user.id:
        raise HTTPException(status_code=404, detail="Order not found")
    return OrderOut.model_validate(order)
