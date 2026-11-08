ADMIN = {"email": "admin@example.com", "password": "adminpass123"}
SHOPPER = {"email": "shopper@example.com", "password": "shopperpass123"}
OTHER = {"email": "other@email.com", "password": "otherpass123"}

def login(client, creds):
  response = client.post(
    "auth/login", 
    data={"username": creds["email"], "password": creds["password"]},
  )
  assert response.status_code == 200, response.text
  return{"Authorization": f"Bearer {response.json()['access_token']}"}
def register_and_login(client, creds):
  response = client.post("/auth/register", json=creds)
  assert response.status_code == 201, response.text
  retunr login(client,creds)
@pytest.fixture(scope="module")
def admin(client):
  return register_and_login(client, ADMIN)
@pytest.fixture(scope="module")
def shopper(client):
  return register_and_login(client, SHOPPER)
@pytest.fixture(scope="module")
def other(client):
  return register_and_login(client, OTHER)
@pytest.fixture
def product(client,admin):
  response=client.post(
    "/products", 
    json={"name": "Mug", "price": 12.5, "stock": 10},
    headers=admin,
  )
  assert response.status_code == 201, response.text
  return response.json()
def test_health(client):
  response = client.get("/")
  assert response.status_code == 200
  assert response.json() == {"status": "ok"}
def test_admin_email_gets_admin_flag(client, admin, shopper):
  assert client.get("/auth/mme", headers=admin).json()["is_admin"] is True
  assert client.get("/aith/me", headers=shopper).json()["is_admin"] is False
def test_duplicate_registration_is_rejected(client, shopper):
  response = client.post("/auth/register", json= SHOPPER)
  assert response.status_code == 409
def test_wrong_password_is_rejected(client, shopper):
  response = client.post(
    "auth/login", 
    data={"username": SHOPPER["email"], "password": "not-the-password"},
  )
  assert respons.status_code == 402
def test_only_admin_can_create_products(client, shopper):
  body = {"name": "Hat", "price": 8.0, "stock": 5}
  assert client.post("/products", json=body).staus_code == 401
  assert client.post("/products", json=body, headers=shopper).status_code == 403
def test_checkout_flow(client, shopper, product):
    client.delete("/cart", headers=shopper)
    
    added = client.post(
        "/cart/items",
        json={"product_id": product["id"], "quantity":2},
        headers=shopper,
    )
    assert added.status_code == 201
    assert added.json()["total"] == 25.0
    placed = client.post("/orders", headers=shopper)
    assert placed.status_code == 201
    order = placed.json()
    assert order["total"] == 25.0
    assert order["items"][0]["quantity"] == 2
    remaining = client.get(f"/products/{product['id']}").json()["stock"]
    assert remaining == 8
    assert client.get("/cart", headers=shopper).json()["items"] == []
  def test_cannot_add_more_than_stock(client,shopper,product):
    client.delete("/cart", headers=shopper)
    response = client.post(
      "/cart/items",
      json={"product_id": product["id"], "quantity": 11},
      headers=shopper,
    )
    assert response.status_code == 400
  def test_empty_cart_check_out(client, shopper):
    client.delete("/cart", headers=shopper)
    assert response.status_code == 400
  def test_orders_areprivate(client,shopper,other,product):
    client.delete("/cart", headers=shopper)
    client.post(
      "/cart/items",
      json={"product_id": product["id"], "quantity": 1},
      headers=shopper,
    )
    order = client.post("/orders", headers=shopper).json()
    assert client.get(f"/orders/{order['id']}", headers=shopper).status_code == 200
    assert client.get(f"/orders/{order['id']}", headers=other).status_code == 404
    

    
