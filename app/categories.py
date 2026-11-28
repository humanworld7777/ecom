import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session

from .auth import require_admin
from .database import SessionLocal, get_db
from .models import CategoryDB, ProductDB, UserDB

router = APIRouter()

# The shelves a new shop starts with. The admin can change, add or remove them.
DEFAULT_CATEGORIES = [
    ("Clothing", "👕", "Size"),
    ("Groceries", "🛒", "Pack size"),
    ("Accessories", "👜", "Option"),
    ("Home & Kitchen", "🏠", "Option"),
    ("Beauty & Care", "💄", "Size"),
]


# ---------- Schemas ----------
class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    icon: str = Field(default="🛍️", max_length=16)
    option_label: str = Field(default="Size", min_length=1, max_length=20)

    @field_validator("name", "option_label")
    @classmethod
    def not_blank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("This cannot be empty")
        return value

    @field_validator("icon")
    @classmethod
    def tidy_icon(cls, value):
        return value.strip() or "🛍️"


class CategoryOut(BaseModel):
    id: int
    name: str
    slug: str
    icon: str
    option_label: str
    product_count: int


# ---------- Helpers ----------
def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "category"


def unique_slug(db: Session, name: str) -> str:
    base = slugify(name)
    slug, number = base, 1
    while db.query(CategoryDB.id).filter(CategoryDB.slug == slug).first():
        number += 1
        slug = f"{base}-{number}"
    return slug


def name_taken(db: Session, name: str, except_id: int | None = None) -> bool:
    query = db.query(CategoryDB.id).filter(func.lower(CategoryDB.name) == name.lower())
    if except_id is not None:
        query = query.filter(CategoryDB.id != except_id)
    return query.first() is not None


def categories_out(db: Session) -> list[CategoryOut]:
    counts = dict(
        db.query(ProductDB.category_id, func.count(ProductDB.id))
        .filter(ProductDB.category_id.isnot(None))
        .group_by(ProductDB.category_id)
        .all()
    )
    categories = db.query(CategoryDB).order_by(CategoryDB.position, CategoryDB.id).all()
    return [
        CategoryOut(
            id=c.id,
            name=c.name,
            slug=c.slug,
            icon=c.icon,
            option_label=c.option_label,
            product_count=counts.get(c.id, 0),
        )
        for c in categories
    ]


def category_out(db: Session, category_id: int) -> CategoryOut:
    return next(c for c in categories_out(db) if c.id == category_id)


def seed_default_categories() -> None:
    """Give a brand-new shop its first shelves."""
    with SessionLocal() as db:
        if db.query(CategoryDB).count() > 0:
            return
        for position, (name, icon, option_label) in enumerate(DEFAULT_CATEGORIES):
            db.add(
                CategoryDB(
                    name=name,
                    slug=slugify(name),
                    icon=icon,
                    option_label=option_label,
                    position=position,
                )
            )
        db.commit()


# ---------- Routes ----------
@router.get("/categories", response_model=list[CategoryOut], tags=["categories"])
def list_categories(db: Session = Depends(get_db)):
    return categories_out(db)


@router.post(
    "/categories", response_model=CategoryOut, status_code=201, tags=["categories"]
)
def create_category(
    body: CategoryIn,
    db: Session = Depends(get_db),
    admin: UserDB = Depends(require_admin),
):
    if name_taken(db, body.name):
        raise HTTPException(
            status_code=409, detail="A category with this name already exists"
        )
    last = db.query(func.max(CategoryDB.position)).scalar()
    category = CategoryDB(
        name=body.name,
        slug=unique_slug(db, body.name),
        icon=body.icon,
        option_label=body.option_label,
        position=0 if last is None else last + 1,
    )
    db.add(category)
    db.commit()
    return category_out(db, category.id)


@router.put(
    "/categories/{category_id}", response_model=CategoryOut, tags=["categories"]
)
def update_category(
    category_id: int,
    body: CategoryIn,
    db: Session = Depends(get_db),
    admin: UserDB = Depends(require_admin),
):
    category = db.get(CategoryDB, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    if name_taken(db, body.name, except_id=category_id):
        raise HTTPException(
            status_code=409, detail="A category with this name already exists"
        )
    # The web address (slug) stays the same, so links keep working after a rename
    category.name = body.name
    category.icon = body.icon
    category.option_label = body.option_label
    db.commit()
    return category_out(db, category.id)


@router.delete("/categories/{category_id}", status_code=204, tags=["categories"])
def delete_category(
    category_id: int,
    db: Session = Depends(get_db),
    admin: UserDB = Depends(require_admin),
):
    category = db.get(CategoryDB, category_id)
    if category:
        # Its products stay in the shop, just without a category
        db.query(ProductDB).filter(ProductDB.category_id == category_id).update(
            {ProductDB.category_id: None}
        )
        db.delete(category)
        db.commit()
