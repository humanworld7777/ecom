from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from .auth import get_current_user, require_admin
from .database import get_db
from .models import OrderDB, OrderItemDB, ProductDB, ReviewDB, UserDB, utcnow

router = APIRouter()

# Orders that count as a real purchase
PURCHASED = ("paid", "shipped", "delivered")


# ---------- Schemas ----------
class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=1000)


class ReviewOut(BaseModel):
    id: int
    rating: int
    comment: str
    reviewer: str
    created_at: datetime


class ReviewsOut(BaseModel):
    average: float | None
    count: int
    # How many reviews gave 5, 4, 3, 2 and 1 stars
    breakdown: dict[str, int]
    reviews: list[ReviewOut]


class MyReviewOut(BaseModel):
    can_review: bool
    review: ReviewOut | None


# ---------- Helpers ----------
def reviewer_name(email: str) -> str:
    """Show only the first letter of the email, e.g. j***"""
    local = email.split("@")[0]
    return (local[:1] or "?") + "***"


def review_out(review: ReviewDB, email: str) -> ReviewOut:
    return ReviewOut(
        id=review.id,
        rating=review.rating,
        comment=review.comment or "",
        reviewer=reviewer_name(email),
        created_at=review.created_at,
    )


def has_purchased(db: Session, user_id: int, product_id: int) -> bool:
    row = (
        db.query(OrderItemDB.id)
        .join(OrderDB, OrderDB.id == OrderItemDB.order_id)
        .filter(
            OrderDB.user_id == user_id,
            OrderDB.status.in_(PURCHASED),
            OrderItemDB.product_id == product_id,
        )
        .first()
    )
    return row is not None


def require_product(db: Session, product_id: int) -> ProductDB:
    product = db.get(ProductDB, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


def find_review(db: Session, product_id: int, user_id: int):
    return (
        db.query(ReviewDB)
        .filter(ReviewDB.product_id == product_id, ReviewDB.user_id == user_id)
        .first()
    )


# ---------- Routes ----------
@router.get("/products/{product_id}/reviews", response_model=ReviewsOut, tags=["reviews"])
def list_reviews(product_id: int, db: Session = Depends(get_db)):
    require_product(db, product_id)

    counts = {str(n): 0 for n in (5, 4, 3, 2, 1)}
    for rating, count in (
        db.query(ReviewDB.rating, func.count(ReviewDB.id))
        .filter(ReviewDB.product_id == product_id)
        .group_by(ReviewDB.rating)
    ):
        counts[str(rating)] = count
    total = sum(counts.values())
    average = (
        round(sum(int(star) * n for star, n in counts.items()) / total, 1)
        if total
        else None
    )

    rows = (
        db.query(ReviewDB, UserDB.email)
        .join(UserDB, UserDB.id == ReviewDB.user_id)
        .filter(ReviewDB.product_id == product_id)
        .order_by(ReviewDB.id.desc())
        .limit(100)
        .all()
    )
    return ReviewsOut(
        average=average,
        count=total,
        breakdown=counts,
        reviews=[review_out(review, email) for review, email in rows],
    )


@router.get(
    "/products/{product_id}/reviews/mine", response_model=MyReviewOut, tags=["reviews"]
)
def my_review(
    product_id: int,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    require_product(db, product_id)
    review = find_review(db, product_id, user.id)
    return MyReviewOut(
        can_review=has_purchased(db, user.id, product_id),
        review=review_out(review, user.email) if review else None,
    )


@router.put("/products/{product_id}/reviews", response_model=ReviewOut, tags=["reviews"])
def save_review(
    product_id: int,
    body: ReviewIn,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    require_product(db, product_id)
    if not has_purchased(db, user.id, product_id):
        raise HTTPException(
            status_code=403,
            detail="Only customers who bought this product can review it",
        )

    review = find_review(db, product_id, user.id)
    if review:
        review.rating = body.rating
        review.comment = body.comment.strip()
        review.updated_at = utcnow()
    else:
        review = ReviewDB(
            product_id=product_id,
            user_id=user.id,
            rating=body.rating,
            comment=body.comment.strip(),
        )
        db.add(review)
    db.commit()
    db.refresh(review)
    return review_out(review, user.email)


@router.delete(
    "/products/{product_id}/reviews/mine", status_code=204, tags=["reviews"]
)
def delete_my_review(
    product_id: int,
    db: Session = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    review = find_review(db, product_id, user.id)
    if review:
        db.delete(review)
        db.commit()


@router.delete("/reviews/{review_id}", status_code=204, tags=["reviews"])
def delete_any_review(
    review_id: int,
    db: Session = Depends(get_db),
    admin: UserDB = Depends(require_admin),
):
    review = db.get(ReviewDB, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    db.delete(review)
    db.commit()
