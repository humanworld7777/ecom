import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from .auth import (
    create_access_token,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)
from .database import Base, engine, get_db
from .models import ProductDB, UserDB

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Ecom API")


# ---------- Schemas ----------
class ProductIn(BaseModel):
    name: str
    price: float
    stock: int


class Product(ProductIn):
    id: int
    model_config = ConfigDict(from_attributes=True)


class UserIn(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=72)


class UserOut(BaseModel):
    id: int
    email: str
    is_admin: bool
    model_config = ConfigDict(from_attributes=True)


class Token(BaseModel):
    access_token: str
    token_type: str


# ---------- Health ----------
@app.get("/")
def health():
    return {"status": "ok"}


# ---------- Auth ----------
@app.post("/auth/register", response_model=UserOut, status_code=201)
def register(u: UserIn, db: Session = Depends(get_db)):
    email = u.email.strip().lower()
    if "@" not in email or "." not in email:
        raise HTTPException(status_code=422, detail="Invalid email address")
    if db.query(UserDB).filter(UserDB.email == email).first():
        raise HTTPException(status_code=409, detail="Email already registered")

    admin_email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    user = UserDB(
        email=email,
        hashed_password=hash_password(u.password),
        is_admin=bool(admin_email) and email == admin_email,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.post("/auth/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    # The form's "username" field holds the email
    email = form.username.strip().lower()
    user = db.query(UserDB).filter(UserDB.email == email).first()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    return {"access_token": create_access_token(user.id), "token_type": "bearer"}


@app.get("/auth/me", response_model=UserOut)
def me(user: UserDB = Depends(get_current_user)):
    return user


# ---------- Products ----------
@app.get("/products", response_model=list[Product])
def list_products(db: Session = Depends(get_db)):
    return db.query(ProductDB).all()


@app.get("/products/{product_id}", response_model=Product)
def get_product(product_id: int, db: Session = Depends(get_db)):
    product = db.get(ProductDB, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@app.post("/products", response_model=Product, status_code=201)
def create_product(
    p: ProductIn,
    db: Session = Depends(get_db),
    admin: UserDB = Depends(require_admin),
):
    product = ProductDB(**p.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@app.put("/products/{product_id}", response_model=Product)
def update_product(
    product_id: int,
    p: ProductIn,
    db: Session = Depends(get_db),
    admin: UserDB = Depends(require_admin),
):
    product = db.get(ProductDB, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    for key, value in p.model_dump().items():
        setattr(product, key, value)
    db.commit()
    db.refresh(product)
    return product


@app.delete("/products/{product_id}", status_code=204)
def delete_product(
    product_id: int,
    db: Session = Depends(get_db),
    admin: UserDB = Depends(require_admin),
):
    product = db.get(ProductDB, product_id)
    if product:
        db.delete(product)
        db.commit()









