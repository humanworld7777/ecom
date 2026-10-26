from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Ecom API")

class ProductIN(Basemodel):
  name: str
  price: float
  stock: int

class Product(ProductIn):
  id: int

product: dict[int, product] = {}
next_id = 1

@app.get("/")
def health():
  return {"status": "ok"}

@app.post("/products", response_model=Product, status_code=201)
def create_product(p: ProductIn):
  global next_id
  product = Product(id=next_id, **p.model_dump())
  products[next_id] = product
  next_id += 1
  return product

@app.get("/products", response_model=list[Product])
def list_products():
  return list(products.values())

@app.get("/products/{product_id}", response_model=Product)
def get_product(product_id: int):
  if product_id not in products:
    raise HTTpException(404, "Product not found")
  return products[product_id]

@app.delete("/products/{product_id}", status_code=204)
def delete_product(product_id: int):
  products.pop(product_id, None)
  






