from collections import defaultdict

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from .auth import require_admin
from .database import get_db
from .models import (
    CartItemDB,
    ProductDB,
    ProductImageDB,
    ProductSizeDB,
    UserDB,
    utcnow,
)

router = APIRouter()

MAX_IMAGE_BYTES = 2 * 1024 * 1024


# ---------- Schemas ----------
class SizeIn(BaseModel):
    label: str = Field(min_length=1, max_length=20)
    stock: int = Field(ge=0)

    @field_validator("label")
    @classmethod
    def strip_label(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("A size name cannot be empty")
        return value


class SizeOut(BaseModel):
    label: str
    stock: int


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    price: float = Field(ge=0)
    # Used only when the product has no sizes
    stock: int = Field(default=0, ge=0)
    sizes: list[SizeIn] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def sizes_must_be_unique(self):
        labels = [size.label.lower() for size in self.sizes]
        if len(labels) != len(set(labels)):
            raise ValueError("Each size can only be listed once")
        return self


class ProductOut(BaseModel):
    id: int
    name: str
    price: float
    # Total available. For a product with sizes this is the sum of its sizes.
    stock: int
    sizes: list[SizeOut]
    image_url: str | None


# ---------- Helpers ----------
def image_url(product_id: int, stamp) -> str | None:
    if stamp is None:
        return None
    return f"/products/{product_id}/image?v={int(stamp.timestamp())}"


def products_out(db: Session, products: list[ProductDB]) -> list[ProductOut]:
    ids = [p.id for p in products]

    sizes = defaultdict(list)
    for size in (
        db.query(ProductSizeDB)
        .filter(ProductSizeDB.product_id.in_(ids))
        .order_by(ProductSizeDB.id)
    ):
        sizes[size.product_id].append(SizeOut(label=size.label, stock=size.stock))

    # Only the timestamps are read here, not the pictures themselves
    stamps = {
        row.product_id: row.updated_at
        for row in db.query(ProductImageDB.product_id, ProductImageDB.updated_at).filter(
            ProductImageDB.product_id.in_(ids)
        )
    }

    result = []
    for product in products:
        product_sizes = sizes.get(product.id, [])
        total = sum(s.stock for s in product_sizes) if product_sizes else product.stock
        result.append(
            ProductOut(
                id=product.id,
                name=product.name,
                price=product.price,
                stock=total,
                sizes=product_sizes,
                image_url=image_url(product.id, stamps.get(product.id)),
            )
        )
    return result


def replace_sizes(db: Session, product_id: int, sizes: list[SizeIn]) -> None:
    db.query(ProductSizeDB).filter(ProductSizeDB.product_id == product_id).delete()
    for size in sizes:
        db.add(ProductSizeDB(product_id=product_id, label=size.label, stock=size.stock))


def sniff_image_type(data: bytes) -> str | None:
    """Work out the real image type from the first bytes of the file."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def get_product_or_404(db: Session, product_id: int) -> ProductDB:
    product = db.get(ProductDB, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


# ---------- Products ----------
@router.get("/products", response_model=list[ProductOut], tags=["products"])
def list_products(db: Session = Depends(get_db)):
    products = db.query(ProductDB).order_by(ProductDB.id).all()
    return products_out(db, products)


@router.get("/products/{product_id}", response_model=ProductOut, tags=["products"])
def get_product(product_id: int, db: Session = Depends(get_db)):
    product = get_product_or_404(db, product_id)
    return products_out(db, [product])[0]


@router.post(
    "/products", response_model=ProductOut, status_code=201, tags=["products"]
)
def create_product(
    body: ProductIn,
    db: Session = Depends(get_db),
    admin: UserDB = Depends(require_admin),
):
    product = ProductDB(
        name=body.name.strip(),
        price=body.price,
        stock=sum(s.stock for s in body.sizes) if body.sizes else body.stock,
    )
    db.add(product)
    db.flush()
    replace_sizes(db, product.id, body.sizes)
    db.commit()
    db.refresh(product)
    return products_out(db, [product])[0]


@router.put("/products/{product_id}", response_model=ProductOut, tags=["products"])
def update_product(
    product_id: int,
    body: ProductIn,
    db: Session = Depends(get_db),
    admin: UserDB = Depends(require_admin),
):
    product = get_product_or_404(db, product_id)
    product.name = body.name.strip()
    product.price = body.price
    product.stock = sum(s.stock for s in body.sizes) if body.sizes else body.stock
    replace_sizes(db, product.id, body.sizes)
    db.commit()
    db.refresh(product)
    return products_out(db, [product])[0]


@router.delete("/products/{product_id}", status_code=204, tags=["products"])
def delete_product(
    product_id: int,
    db: Session = Depends(get_db),
    admin: UserDB = Depends(require_admin),
):
    product = db.get(ProductDB, product_id)
    if product:
        db.query(CartItemDB).filter(CartItemDB.product_id == product_id).delete()
        db.query(ProductSizeDB).filter(ProductSizeDB.product_id == product_id).delete()
        db.query(ProductImageDB).filter(
            ProductImageDB.product_id == product_id
        ).delete()
        db.delete(product)
        db.commit()


# ---------- Product photo ----------
@router.get("/products/{product_id}/image", tags=["products"])
def product_image(product_id: int, db: Session = Depends(get_db)):
    image = db.get(ProductImageDB, product_id)
    if not image:
        raise HTTPException(status_code=404, detail="This product has no photo")
    return Response(
        content=bytes(image.data),
        media_type=image.content_type,
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/products/{product_id}/image", response_model=ProductOut, tags=["products"]
)
async def upload_product_image(
    product_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin: UserDB = Depends(require_admin),
):
    product = get_product_or_404(db, product_id)

    data = await file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="The photo is larger than 2 MB")
    content_type = sniff_image_type(data)
    if content_type is None:
        raise HTTPException(
            status_code=400, detail="Please upload a JPEG, PNG or WebP photo"
        )

    image = db.get(ProductImageDB, product_id)
    if image:
        image.content_type = content_type
        image.data = data
        image.updated_at = utcnow()
    else:
        db.add(
            ProductImageDB(product_id=product_id, content_type=content_type, data=data)
        )
    db.commit()
    return products_out(db, [product])[0]


@router.delete(
    "/products/{product_id}/image", response_model=ProductOut, tags=["products"]
)
def delete_product_image(
    product_id: int,
    db: Session = Depends(get_db),
    admin: UserDB = Depends(require_admin),
):
    product = get_product_or_404(db, product_id)
    db.query(ProductImageDB).filter(ProductImageDB.product_id == product_id).delete()
    db.commit()
    return products_out(db, [product])[0]
