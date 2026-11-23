from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from .auth import get_current_user
from .database import get_db
from .delivery import router as delivery_router
from .models import (
    CartItemDB,
    OrderDB,
    OrderItemDB,
    ProductDB,
    ProductSizeDB,
    UserDB,
)
from .payments import router as payments_router
from .products import main_photo_urls
from .products import router as products_router
from .reviews import router as reviews_router
from .site import router as site_router

router = APIRouter()
router.include_router(products_router)
router.include_router(reviews_router)
router.include_router(payments_router)
router.include_router(delivery_router)
router.include_router(site_router)


# ---------- Schemas ----------
class CartAdd(BaseModel):
    product_id: int
    quantity: int = Field(default=1, ge=1, le=1000)
    size: str = Field(default="", max_length=20)


class CartUpdate(BaseModel):
    quantity: int = Field(ge=1, le=1000)


class CartItemOut(BaseModel):
    product_id: int
    name: str
    size: str
    price: float
    quantity: int
    subtotal: float
    image_url: str | None


class CartOut(BaseModel):
    items: list[CartItemOut]
    total: float


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: int | None
    product_name: str
    size: str
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
    photos = main_photo_urls(db, [r.product_id for r in rows])
    items = [
        CartItemOut(
            product_id=r.product_id,
            name=r.product.name,
            size=r.size,
            price=r.product.price,
            quantity=r.quantity,
            subtotal=round(r.product.price * r.quantity, 2),
            image_url=photos.get(r.product_id),
        )
        for r in rows
    ]
    total = round(sum(i.subtotal for i in items), 2)
    return CartOut(items=items, total=total)


def find_cart_item(db: Session, user: UserDB, product_id: int, size: str):
    return (
        db.query(CartItemDB)
        .filter(
            CartItemDB.user_id == user.id,
            CartItemDB.product_id == product_id,
            CartItemDB.size == size,
        )
        .first()
    )


def resolve_size(db: Session, product: ProductDB, size: str) -> tuple[str, int]:
    """Return the exact size name and how many are in stock.

    Products with sizes need a valid size; products without sizes must not have one.
    """
    size = (size or "").strip()
    sizes = db.query(ProductSizeDB).filter(ProductSizeDB.product_id == product.id).all()
    if sizes:
        if not size:
            raise HTTPException(status_code=400, detail="Please choose a size")
        match = next((s for s in sizes if s.label.lower() == size.lower()), None)
        if match is None:
            raise HTTPException(status_code=400, detail=f"Size {size} is not available")
        return match.label, match.stock
    if size:
        raise HTTPException(status_code=400, detail="This product has no sizes")
    return "", product.stock


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

    size, in_stock = resolve_size(db, product, body.size)
    existing = find_cart_item(db, user, body.product_id, size)
    new_qty = (existing.quantity if existing else 0) + body.quantity
    if new_qty > in_stock:
        raise HTTPException(status_code=400, detail=f"Only {in_stock} in stock")

    if existing:
        existing.quantity = new_qty
    else:
        db.add(
            CartItemDB(
                user_id=user.id,
                product_id=body.product_id,
                size=size,
                quantity=body.quantity,
            )
        )
    db.commit()
    return build_cart(db, user)


@router.put("/cart/items/{product_id}", response_model=CartOut, tags=["cart"])
def set_cart_quantity(
    product_id: int,
    body: CartUpdate,
    size: str = Query(default=""),
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    item = find_cart_item(db, user, product_id, size)
    if not item:
        raise HTTPException(status_code=404, detail="Item not in cart")
    _, in_stock = resolve_size(db, item.product, item.size)
    if body.quantity > in_stock:
        raise HTTPException(status_code=400, detail=f"Only {in_stock} in stock")
    item.quantity = body.quantity
    db.commit()
    return build_cart(db, user)


@router.delete("/cart/items/{product_id}", response_model=CartOut, tags=["cart"])
def remove_from_cart(
    product_id: int,
    size: str = Query(default=""),
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    item = find_cart_item(db, user, product_id, size)
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

    # Lock the product and size rows so two checkouts can't sell the same last item
    product_ids = sorted({i.product_id for i in cart_items})
    products = {
        p.id: p
        for p in db.query(ProductDB)
        .filter(ProductDB.id.in_(product_ids))
        .order_by(ProductDB.id)
        .with_for_update()
        .all()
    }
    sizes: dict[int, list[ProductSizeDB]] = {}
    for size_row in (
        db.query(ProductSizeDB)
        .filter(ProductSizeDB.product_id.in_(product_ids))
        .order_by(ProductSizeDB.id)
        .with_for_update()
        .all()
    ):
        sizes.setdefault(size_row.product_id, []).append(size_row)

    # The stock is held for the customer until the order is paid or the
    # payment page expires (see app/payments.py).
    order = OrderDB(user_id=user.id, status="pending_payment", total=0)
    total = 0.0

    for item in cart_items:
        product = products.get(item.product_id)
        if product is None:
            db.rollback()
            raise HTTPException(
                status_code=409, detail="A product in your cart is no longer available"
            )

        product_sizes = sizes.get(product.id, [])
        size_row = None
        if product_sizes:
            size_row = next((s for s in product_sizes if s.label == item.size), None)
            if size_row is None:
                db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail=f"Please choose an available size for: {product.name}",
                )
            in_stock = size_row.stock
        elif item.size:
            db.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"{product.name} is no longer sold in size {item.size}",
            )
        else:
            in_stock = product.stock

        if in_stock < item.quantity:
            db.rollback()
            raise HTTPException(
                status_code=409, detail=f"Not enough stock for: {product.name}"
            )

        if size_row is not None:
            size_row.stock -= item.quantity
        else:
            product.stock -= item.quantity

        order.items.append(
            OrderItemDB(
                product_id=product.id,
                product_name=product.name,
                size=item.size,
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
