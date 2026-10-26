from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from .database import Base, engine, get_db
from .models import ProductDB

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Ecom API")


class ProductIn(BaseModel):
    name: str
    price: float
    stock: int


class Product(ProductIn):
    id: int
    model_config = ConfigDict(from_attributes=True)


@app.get("/")
def health():
    return {"status": "ok"}


@app.post("/products", response_model=Product, status_code=201)
def create_product(p: ProductIn, db: Session = Depends(get_db)):
    product = ProductDB(**p.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@app.get("/products", response_model=list[Product])
def list_products(db: Session = Depends(get_db)):
    return db.query(ProductDB).all()


@app.get("/products/{product_id}", response_model=Product)
def get_product(product_id: int, db: Session = Depends(get_db)):
    product = db.get(ProductDB, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@app.put("/products/{product_id}", response_model=Product)
def update_product(product_id: int, p: ProductIn, db: Session = Depends(get_db)):
    product = db.get(ProductDB, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    for key, value in p.model_dump().items():
        setattr(product, key, value)
    db.commit()
    db.refresh(product)
    return product


@app.delete("/products/{product_id}", status_code=204)
def delete_product(product_id: int, db: Session = Depends(get_db)):
    product = db.get(ProductDB, product_id)
    if product:
        db.delete(product)
        db.commit()
    
  






