import pytest

from app.database import SessionLocal
from app.models import UserDB

STAFF = {"email": "shelves@example.com", "password": "shelvespass123"}
SHOPPER = {"email": "grocer@example.com", "password": "grocerpass123"}


def register_and_login(client, creds):
    response = client.post("/auth/register", json=creds)
    assert response.status_code == 201, response.text
    response = client.post(
        "/auth/login",
        data={"username": creds["email"], "password": creds["password"]},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture(scope="module")
def admin(client):
    headers = register_and_login(client, STAFF)
    # Promote straight in the test database, so these tests do not depend on
    # the admin account that test_shop.py creates.
    with SessionLocal() as db:
        user = db.query(UserDB).filter(UserDB.email == STAFF["email"]).first()
        user.is_admin = True
        db.commit()
    return headers


@pytest.fixture(scope="module")
def shopper(client):
    return register_and_login(client, SHOPPER)


def make_category(client, admin, name, icon="🧺", option_label="Size"):
    response = client.post(
        "/categories",
        json={"name": name, "icon": icon, "option_label": option_label},
        headers=admin,
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_product(client, admin, **fields):
    body = {"name": "Item", "price": 10.0, "stock": 5}
    body.update(fields)
    response = client.post("/products", json=body, headers=admin)
    assert response.status_code == 201, response.text
    return response.json()


def categories_by_name(client):
    return {c["name"]: c for c in client.get("/categories").json()}


def test_a_new_shop_starts_with_some_categories(client):
    found = categories_by_name(client)
    assert {"Clothing", "Groceries", "Accessories"} <= set(found)
    assert found["Groceries"]["option_label"] == "Pack size"
    assert found["Clothing"]["option_label"] == "Size"


def test_an_admin_can_add_a_category(client, admin, shopper):
    created = make_category(client, admin, "Toys", icon="🧸", option_label="Age")
    assert created["slug"] == "toys"
    assert created["icon"] == "🧸"
    assert created["option_label"] == "Age"
    assert created["product_count"] == 0

    body = {"name": "toys", "icon": "🧸", "option_label": "Age"}
    assert client.post("/categories", json=body, headers=admin).status_code == 409
    assert client.post("/categories", json=body, headers=shopper).status_code == 403
    assert client.post("/categories", json=body).status_code == 401


def test_similar_names_get_different_web_addresses(client, admin):
    first = make_category(client, admin, "Pets & Co")
    second = make_category(client, admin, "Pets Co")
    assert first["slug"] == "pets-co"
    assert second["slug"] == "pets-co-2"


def test_a_category_can_be_renamed_and_keeps_its_web_address(client, admin):
    created = make_category(client, admin, "Stationery")
    changed = client.put(
        f"/categories/{created['id']}",
        json={"name": "Office & School", "icon": "📎", "option_label": "Pack size"},
        headers=admin,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["name"] == "Office & School"
    assert changed.json()["slug"] == "stationery"
    assert changed.json()["option_label"] == "Pack size"


def test_a_category_cannot_take_the_name_of_another(client, admin):
    make_category(client, admin, "Garden")
    other = make_category(client, admin, "Garage")
    response = client.put(
        f"/categories/{other['id']}",
        json={"name": "GARDEN", "icon": "x", "option_label": "Size"},
        headers=admin,
    )
    assert response.status_code == 409


def test_products_can_be_placed_in_a_category(client, admin):
    category = make_category(client, admin, "Bags")
    product = make_product(client, admin, name="Tote", category_id=category["id"])
    assert product["category"]["slug"] == "bags"
    assert product["category"]["option_label"] == "Size"

    plain = make_product(client, admin, name="Loose item")
    assert plain["category"] is None

    in_bags = client.get("/products", params={"category": "bags"}).json()
    assert [p["id"] for p in in_bags] == [product["id"]]
    assert client.get("/products", params={"category": "nothing-here"}).json() == []

    counted = categories_by_name(client)["Bags"]
    assert counted["product_count"] == 1


def test_a_product_cannot_use_a_category_that_does_not_exist(client, admin):
    body = {"name": "Odd", "price": 1.0, "stock": 1, "category_id": 999999}
    assert client.post("/products", json=body, headers=admin).status_code == 400


def test_the_category_of_a_product_can_be_changed(client, admin):
    first = make_category(client, admin, "Candles")
    second = make_category(client, admin, "Soaps")
    product = make_product(client, admin, category_id=first["id"])
    body = {"name": "Item", "price": 10.0, "stock": 5, "category_id": second["id"]}
    updated = client.put(f"/products/{product['id']}", json=body, headers=admin).json()
    assert updated["category"]["slug"] == "soaps"


def test_deleting_a_category_keeps_its_products(client, admin, shopper):
    category = make_category(client, admin, "Seasonal")
    product = make_product(client, admin, category_id=category["id"])

    assert client.delete(f"/categories/{category['id']}", headers=shopper).status_code == 403
    assert client.delete(f"/categories/{category['id']}", headers=admin).status_code == 204

    kept = client.get(f"/products/{product['id']}").json()
    assert kept["category"] is None
    assert "Seasonal" not in categories_by_name(client)


def test_grocery_packs_can_have_their_own_prices(client, admin, shopper):
    client.delete("/cart", headers=shopper)
    groceries = categories_by_name(client)["Groceries"]
    rice = make_product(
        client,
        admin,
        name="Rice",
        price=50.0,
        category_id=groceries["id"],
        stock=0,
        sizes=[
            {"label": "500 g", "stock": 10, "price": 30.0},
            {"label": "1 kg", "stock": 10, "price": 55.0},
            {"label": "2 kg", "stock": 10},
        ],
    )
    assert [s["price"] for s in rice["sizes"]] == [30.0, 55.0, None]

    for pack, quantity in (("1 kg", 2), ("2 kg", 1)):
        added = client.post(
            "/cart/items",
            json={"product_id": rice["id"], "quantity": quantity, "size": pack},
            headers=shopper,
        )
        assert added.status_code == 201, added.text

    cart = client.get("/cart", headers=shopper).json()
    lines = {i["size"]: i for i in cart["items"]}
    assert lines["1 kg"]["price"] == 55.0
    assert lines["1 kg"]["subtotal"] == 110.0
    assert lines["1 kg"]["option_label"] == "Pack size"
    # A pack without its own price costs the same as the product
    assert lines["2 kg"]["price"] == 50.0
    assert cart["total"] == 160.0

    placed = client.post("/orders", headers=shopper)
    assert placed.status_code == 201, placed.text
    order = placed.json()
    assert order["total"] == 160.0
    prices = {i["size"]: i["unit_price"] for i in order["items"]}
    assert prices == {"1 kg": 55.0, "2 kg": 50.0}

    packs = {s["label"]: s["stock"] for s in client.get(f"/products/{rice['id']}").json()["sizes"]}
    assert packs == {"500 g": 10, "1 kg": 8, "2 kg": 9}


def test_cart_option_name_is_size_when_there_is_no_category(client, admin, shopper):
    client.delete("/cart", headers=shopper)
    shirt = make_product(client, admin, name="Plain shirt", sizes=[{"label": "M", "stock": 3}])
    client.post(
        "/cart/items",
        json={"product_id": shirt["id"], "quantity": 1, "size": "M"},
        headers=shopper,
    )
    item = client.get("/cart", headers=shopper).json()["items"][0]
    assert item["option_label"] == "Size"
