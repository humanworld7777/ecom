from collections import defaultdict

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from .auth import require_admin
from .database import get_db
from .models import (
    CartItemDB,
    CategoryDB,
    ProductDB,
    ProductPhotoDB,
    ProductSizeDB,
    ReviewDB,
    UserDB,
)

router = APIRouter()

MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_PHOTOS = 8


# ---------- Schemas ----------
class SizeIn(BaseModel):
    label: str = Field(min_length=1, max_length=20)
    stock: int = Field(ge=0)
    # Leave empty to use the product's own price
    price: float | None = Field(default=None, ge=0)

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
    price: float | None


class CategoryRef(BaseModel):
    id: int
    name: str
    slug: str
    icon: str
    option_label: str


class PhotoOut(BaseModel):
    id: int
    url: str


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    price: float = Field(ge=0)
    # Used only when the product has no sizes
    stock: int = Field(default=0, ge=0)
    category_id: int | None = None
    # Sizes for clothes, pack sizes for groceries, other options elsewhere
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
    description: str
    price: float
    # Total available. For a product with sizes this is the sum of its sizes.
    stock: int
    category: CategoryRef | None
    sizes: list[SizeOut]
    # The main photo, and all photos in order (the main one first)
    image_url: str | None
    images: list[PhotoOut]
    rating_average: float | None
    rating_count: int


# ---------- Helpers ----------
def photo_url(photo_id: int) -> str:
    return f"/photos/{photo_id}"


def main_photo_urls(db: Session, product_ids: list[int]) -> dict[int, str]:
    """The URL of each product's main photo (the one with the lowest position)."""
    rows = (
        db.query(ProductPhotoDB.product_id, ProductPhotoDB.id)
        .filter(ProductPhotoDB.product_id.in_(product_ids))
        .order_by(ProductPhotoDB.product_id, ProductPhotoDB.position, ProductPhotoDB.id)
    )
    urls: dict[int, str] = {}
    for product_id, photo_id in rows:
        urls.setdefault(product_id, photo_url(photo_id))
    return urls


def products_out(db: Session, products: list[ProductDB]) -> list[ProductOut]:
    ids = [p.id for p in products]

    sizes = defaultdict(list)
    for size in (
        db.query(ProductSizeDB)
        .filter(ProductSizeDB.product_id.in_(ids))
        .order_by(ProductSizeDB.id)
    ):
        sizes[size.product_id].append(
            SizeOut(label=size.label, stock=size.stock, price=size.price)
        )

    # Only the ids are read here, not the photos themselves
    photos = defaultdict(list)
    for product_id, photo_id in (
        db.query(ProductPhotoDB.product_id, ProductPhotoDB.id)
        .filter(ProductPhotoDB.product_id.in_(ids))
        .order_by(ProductPhotoDB.position, ProductPhotoDB.id)
    ):
        photos[product_id].append(PhotoOut(id=photo_id, url=photo_url(photo_id)))

    category_ids = {p.category_id for p in products if p.category_id is not None}
    categories = {
        c.id: CategoryRef(
            id=c.id,
            name=c.name,
            slug=c.slug,
            icon=c.icon,
            option_label=c.option_label,
        )
        for c in db.query(CategoryDB).filter(CategoryDB.id.in_(list(category_ids)))
    }

    ratings = {}
    for product_id, total, count in (
        db.query(ReviewDB.product_id, func.sum(ReviewDB.rating), func.count(ReviewDB.id))
        .filter(ReviewDB.product_id.in_(ids))
        .group_by(ReviewDB.product_id)
    ):
        ratings[product_id] = (round(float(total) / count, 1), count)

    result = []
    for product in products:
        product_sizes = sizes.get(product.id, [])
        product_photos = photos.get(product.id, [])
        average, count = ratings.get(product.id, (None, 0))
        total = sum(s.stock for s in product_sizes) if product_sizes else product.stock
        result.append(
            ProductOut(
                id=product.id,
                name=product.name,
                description=product.description or "",
                price=product.price,
                stock=total,
                category=categories.get(product.category_id),
                sizes=product_sizes,
                image_url=product_photos[0].url if product_photos else None,
                images=product_photos,
                rating_average=average,
                rating_count=count,
            )
        )
    return result


def replace_sizes(db: Session, product_id: int, sizes: list[SizeIn]) -> None:
    db.query(ProductSizeDB).filter(ProductSizeDB.product_id == product_id).delete()
    for size in sizes:
        db.add(
            ProductSizeDB(
                product_id=product_id,
                label=size.label,
                stock=size.stock,
                price=size.price,
            )
        )


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


def check_category(db: Session, category_id: int | None) -> None:
    if category_id is not None and not db.get(CategoryDB, category_id):
        raise HTTPException(status_code=400, detail="That category does not exist")


def get_photo_or_404(db: Session, product_id: int, photo_id: int) -> ProductPhotoDB:
    photo = db.get(ProductPhotoDB, photo_id)
    if not photo or photo.product_id != product_id:
        raise HTTPException(status_code=404, detail="Photo not found")
    return photo


# ---------- Products ----------
@router.get("/products", response_model=list[ProductOut], tags=["products"])
def list_products(category: str | None = None, db: Session = Depends(get_db)):
    query = db.query(ProductDB)
    if category:
        query = query.join(CategoryDB, CategoryDB.id == ProductDB.category_id).filter(
            CategoryDB.slug == category
        )
    return products_out(db, query.order_by(ProductDB.id).all())


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
    check_category(db, body.category_id)
    product = ProductDB(
        name=body.name.strip(),
        description=body.description.strip(),
        category_id=body.category_id,
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
    check_category(db, body.category_id)
    product.name = body.name.strip()
    product.description = body.description.strip()
    product.category_id = body.category_id
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
        for model in (CartItemDB, ProductSizeDB, ProductPhotoDB, ReviewDB):
            db.query(model).filter(model.product_id == product_id).delete()
        db.delete(product)
        db.commit()


# ---------- Photos ----------
@router.get("/photos/{photo_id}", tags=["products"])
def get_photo(photo_id: int, db: Session = Depends(get_db)):
    photo = db.get(ProductPhotoDB, photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")
    return Response(
        content=bytes(photo.data),
        media_type=photo.content_type,
        headers={
            # A photo never changes: a new upload gets a new id
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/products/{product_id}/photos",
    response_model=ProductOut,
    status_code=201,
    tags=["products"],
)
async def add_photo(
    product_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin: UserDB = Depends(require_admin),
):
    product = get_product_or_404(db, product_id)

    count = (
        db.query(func.count(ProductPhotoDB.id))
        .filter(ProductPhotoDB.product_id == product_id)
        .scalar()
    )
    if count >= MAX_PHOTOS:
        raise HTTPException(
            status_code=400, detail=f"A product can have at most {MAX_PHOTOS} photos"
        )

    data = await file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="The photo is larger than 2 MB")
    content_type = sniff_image_type(data)
    if content_type is None:
        raise HTTPException(
            status_code=400, detail="Please upload a JPEG, PNG or WebP photo"
        )

    last = (
        db.query(func.max(ProductPhotoDB.position))
        .filter(ProductPhotoDB.product_id == product_id)
        .scalar()
    )
    db.add(
        ProductPhotoDB(
            product_id=product_id,
            content_type=content_type,
            data=data,
            position=0 if last is None else last + 1,
        )
    )
    db.commit()
    return products_out(db, [product])[0]


@router.delete(
    "/products/{product_id}/photos/{photo_id}",
    response_model=ProductOut,
    tags=["products"],
)
def delete_photo(
    product_id: int,
    photo_id: int,
    db: Session = Depends(get_db),
    admin: UserDB = Depends(require_admin),
):
    product = get_product_or_404(db, product_id)
    photo = get_photo_or_404(db, product_id, photo_id)
    db.delete(photo)
    db.commit()
    return products_out(db, [product])[0]


@router.post(
    "/products/{product_id}/photos/{photo_id}/main",
    response_model=ProductOut,
    tags=["products"],
)
def make_main_photo(
    product_id: int,
    photo_id: int,
    db: Session = Depends(get_db),
    admin: UserDB = Depends(require_admin),
):
    product = get_product_or_404(db, product_id)
    photo = get_photo_or_404(db, product_id, photo_id)
    lowest = (
        db.query(func.min(ProductPhotoDB.position))
        .filter(ProductPhotoDB.product_id == product_id)
        .scalar()
    )
    photo.position = (lowest if lowest is not None else 0) - 1
    db.commit()
    return products_out(db, [product])[0]
