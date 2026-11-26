import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from .auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from .cart import router as cart_router
from .categories import seed_default_categories
from .database import Base, engine, get_db
from .migrate import run_migrations
from .models import UserDB

Base.metadata.create_all(bind=engine)
run_migrations(engine)
seed_default_categories()

app = FastAPI(title="ZAPP Store API")
app.include_router(cart_router)


# ---------- Schemas ----------
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

    # Promote to admin if this email matches ADMIN_EMAIL
    admin_email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    if admin_email and email == admin_email and not user.is_admin:
        user.is_admin = True
        db.commit()

    return {"access_token": create_access_token(user.id), "token_type": "bearer"}


@app.get("/auth/me", response_model=UserOut)
def me(user: UserDB = Depends(get_current_user)):
    return user
