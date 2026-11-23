import base64
import hashlib
import hmac
import json
import time

import pytest

from app.database import SessionLocal
from app.models import UserDB

WEBHOOK_SECRET = "whsec_products_test_secret"

CATALOG = {"email": "catalog@example.com", "password": "catalogpass123"}
SHOPPER = {"email": "sizes@example.com", "password": "sizespass123"}

# A real 1x1 PNG picture
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


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
    headers = register_and_login(client, CATALOG)
    # Promote straight in the test database, so these tests do not depend on
    # the admin account that test_shop.py creates.
    with SessionLocal() as db:
        user = db.query(UserDB).filter(UserDB.email == CATALOG["email"]).first()
        user.is_admin = True
        db.commit()
    return headers


@pytest.fixture(scope="module")
def shopper(client):
    return register_and_login(client, SHOPPER)


def create_product(client, admin, sizes=None, stock=0):
    body = {"name": "Shirt", "price": 10.0, "stock": stock, "sizes": sizes or []}
    response = client.post("/products", json=body, headers=admin)
    assert response.status_code == 201, response.text
    return response.json()


def add_to_cart(client, headers, product_id, quantity=1, size=None):
    body = {"product_id": product_id, "quantity": quantity}
    if size is not None:
        body["size"] = size
    return client.post("/cart/items", json=body, headers=headers)


def size_stock(client, product_id, label):
    sizes = client.get(f"/products/{product_id}").json()["sizes"]
    return next(s["stock"] for s in sizes if s["label"] == label)


def sign(payload, secret=WEBHOOK_SECRET):
    timestamp = int(time.time())
    signed = str(timestamp).encode() + b"." + payload
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


# ---------- sizes ----------
def test_product_with_sizes_reports_the_total_stock(client, admin):
    product = create_product(
        client, admin, sizes=[{"label": "S", "stock": 3}, {"label": "M", "stock": 4}]
    )
    assert product["stock"] == 7
    assert [s["label"] for s in product["sizes"]] == ["S", "M"]
    assert product["image_url"] is None


def test_product_without_sizes_still_works(client, admin):
    product = create_product(client, admin, stock=5)
    assert product["stock"] == 5
    assert product["sizes"] == []


def test_duplicate_sizes_are_rejected(client, admin):
    body = {
        "name": "Shirt",
        "price": 10.0,
        "sizes": [{"label": "M", "stock": 1}, {"label": "m", "stock": 2}],
    }
    assert client.post("/products", json=body, headers=admin).status_code == 422


def test_sizes_can_be_changed_later(client, admin):
    product = create_product(client, admin, sizes=[{"label": "S", "stock": 1}])
    body = {
        "name": "Shirt",
        "price": 12.0,
        "sizes": [{"label": "L", "stock": 9}],
    }
    updated = client.put(f"/products/{product['id']}", json=body, headers=admin).json()
    assert [s["label"] for s in updated["sizes"]] == ["L"]
    assert updated["stock"] == 9


def test_cart_needs_a_valid_size(client, admin, shopper):
    client.delete("/cart", headers=shopper)
    product = create_product(client, admin, sizes=[{"label": "S", "stock": 3}])
    assert add_to_cart(client, shopper, product["id"]).status_code == 400
    assert add_to_cart(client, shopper, product["id"], size="XL").status_code == 400

    # The size name is matched without caring about capital letters
    added = add_to_cart(client, shopper, product["id"], size="s")
    assert added.status_code == 201
    assert added.json()["items"][0]["size"] == "S"


def test_cannot_add_more_than_the_stock_of_a_size(client, admin, shopper):
    client.delete("/cart", headers=shopper)
    product = create_product(
        client, admin, sizes=[{"label": "S", "stock": 2}, {"label": "M", "stock": 50}]
    )
    assert add_to_cart(client, shopper, product["id"], quantity=3, size="S").status_code == 400


def test_a_product_without_sizes_rejects_a_size(client, admin, shopper):
    client.delete("/cart", headers=shopper)
    product = create_product(client, admin, stock=5)
    assert add_to_cart(client, shopper, product["id"], size="M").status_code == 400


def test_each_size_is_its_own_cart_line(client, admin, shopper):
    client.delete("/cart", headers=shopper)
    product = create_product(
        client, admin, sizes=[{"label": "S", "stock": 5}, {"label": "M", "stock": 5}]
    )
    add_to_cart(client, shopper, product["id"], size="S")
    add_to_cart(client, shopper, product["id"], size="M")
    cart = add_to_cart(client, shopper, product["id"], size="S").json()
    lines = {(i["size"], i["quantity"]) for i in cart["items"]}
    assert lines == {("S", 2), ("M", 1)}

    # Changing one size does not touch the other
    changed = client.put(
        f"/cart/items/{product['id']}",
        params={"size": "M"},
        json={"quantity": 4},
        headers=shopper,
    )
    assert changed.status_code == 200
    lines = {(i["size"], i["quantity"]) for i in changed.json()["items"]}
    assert lines == {("S", 2), ("M", 4)}


def test_checkout_takes_stock_from_the_chosen_size(client, admin, shopper):
    client.delete("/cart", headers=shopper)
    product = create_product(
        client, admin, sizes=[{"label": "S", "stock": 5}, {"label": "M", "stock": 5}]
    )
    add_to_cart(client, shopper, product["id"], quantity=2, size="S")
    placed = client.post("/orders", headers=shopper)
    assert placed.status_code == 201, placed.text
    assert placed.json()["items"][0]["size"] == "S"
    assert size_stock(client, product["id"], "S") == 3
    assert size_stock(client, product["id"], "M") == 5


def test_expired_payment_returns_stock_to_the_size(client, admin, shopper, monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    client.delete("/cart", headers=shopper)
    product = create_product(client, admin, sizes=[{"label": "L", "stock": 6}])
    add_to_cart(client, shopper, product["id"], quantity=2, size="L")
    order = client.post("/orders", headers=shopper).json()
    assert size_stock(client, product["id"], "L") == 4

    event = {
        "id": "evt_size_test",
        "type": "checkout.session.expired",
        "data": {"object": {"client_reference_id": str(order["id"])}},
    }
    payload = json.dumps(event).encode()
    response = client.post(
        "/webhooks/stripe",
        content=payload,
        headers={
            "stripe-signature": sign(payload),
            "content-type": "application/json",
        },
    )
    assert response.status_code == 200, response.text
    assert size_stock(client, product["id"], "L") == 6


# ---------- description ----------
def test_description_is_saved_and_trimmed(client, admin):
    body = {"name": "Shirt", "description": "  Soft cotton  ", "price": 10.0, "stock": 1}
    created = client.post("/products", json=body, headers=admin).json()
    assert created["description"] == "Soft cotton"
    body["description"] = "Now with pockets"
    updated = client.put(f"/products/{created['id']}", json=body, headers=admin).json()
    assert updated["description"] == "Now with pockets"


# ---------- photos ----------
def upload(client, headers, product_id, data=PNG, name="photo.png", kind="image/png"):
    return client.post(
        f"/products/{product_id}/photos",
        files={"file": (name, data, kind)},
        headers=headers,
    )


def test_a_new_product_has_no_photos(client, admin):
    product = create_product(client, admin, stock=1)
    assert product["image_url"] is None
    assert product["images"] == []


def test_photo_can_be_uploaded_and_shown(client, admin):
    product = create_product(client, admin, stock=1)
    response = upload(client, admin, product["id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["images"]) == 1
    assert body["image_url"] == body["images"][0]["url"]

    listed = client.get("/products").json()
    mine = next(p for p in listed if p["id"] == product["id"])
    assert mine["image_url"] == body["image_url"]

    photo = client.get(body["image_url"])
    assert photo.status_code == 200
    assert photo.headers["content-type"] == "image/png"
    assert photo.content == PNG
    assert "immutable" in photo.headers["cache-control"]


def test_several_photos_keep_their_order(client, admin):
    product = create_product(client, admin, stock=1)
    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 50
    first = upload(client, admin, product["id"]).json()
    second = upload(client, admin, product["id"], jpeg, "p.jpg", "image/jpeg").json()
    ids = [image["id"] for image in second["images"]]
    assert len(ids) == 2
    assert second["image_url"] == first["image_url"]

    types = [client.get(image["url"]).headers["content-type"] for image in second["images"]]
    assert types == ["image/png", "image/jpeg"]


def test_a_photo_can_be_made_the_main_one(client, admin):
    product = create_product(client, admin, stock=1)
    upload(client, admin, product["id"])
    two = upload(client, admin, product["id"]).json()
    first_id, second_id = [image["id"] for image in two["images"]]

    changed = client.post(
        f"/products/{product['id']}/photos/{second_id}/main", headers=admin
    )
    assert changed.status_code == 200, changed.text
    assert [image["id"] for image in changed.json()["images"]] == [second_id, first_id]
    assert changed.json()["image_url"] == f"/photos/{second_id}"


def test_photo_can_be_deleted(client, admin):
    product = create_product(client, admin, stock=1)
    photo = upload(client, admin, product["id"]).json()["images"][0]
    removed = client.delete(
        f"/products/{product['id']}/photos/{photo['id']}", headers=admin
    )
    assert removed.status_code == 200
    assert removed.json()["images"] == []
    assert removed.json()["image_url"] is None
    assert client.get(photo["url"]).status_code == 404


def test_a_photo_of_another_product_cannot_be_touched(client, admin):
    one = create_product(client, admin, stock=1)
    two = create_product(client, admin, stock=1)
    photo = upload(client, admin, one["id"]).json()["images"][0]
    wrong = client.delete(f"/products/{two['id']}/photos/{photo['id']}", headers=admin)
    assert wrong.status_code == 404


def test_a_product_can_have_at_most_eight_photos(client, admin):
    product = create_product(client, admin, stock=1)
    for _ in range(8):
        assert upload(client, admin, product["id"]).status_code == 201
    assert upload(client, admin, product["id"]).status_code == 400


def test_only_real_pictures_are_accepted(client, admin):
    product = create_product(client, admin, stock=1)
    # A text file pretending to be a picture
    fake = upload(client, admin, product["id"], b"just some text", "x.png", "image/png")
    assert fake.status_code == 400
    # An SVG could carry scripts, so it is not allowed either
    svg = upload(client, admin, product["id"], b"<svg></svg>", "x.svg", "image/svg+xml")
    assert svg.status_code == 400


def test_large_pictures_are_rejected(client, admin):
    product = create_product(client, admin, stock=1)
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (2 * 1024 * 1024)
    assert upload(client, admin, product["id"], big).status_code == 413


def test_only_admins_can_change_photos(client, admin, shopper):
    product = create_product(client, admin, stock=1)
    photo = upload(client, admin, product["id"]).json()["images"][0]
    assert upload(client, shopper, product["id"]).status_code == 403
    assert (
        client.delete(
            f"/products/{product['id']}/photos/{photo['id']}", headers=shopper
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/products/{product['id']}/photos/{photo['id']}/main", headers=shopper
        ).status_code
        == 403
    )


def test_deleting_a_product_removes_its_photos_and_sizes(client, admin):
    product = create_product(client, admin, sizes=[{"label": "S", "stock": 1}])
    photo = upload(client, admin, product["id"]).json()["images"][0]
    assert client.delete(f"/products/{product['id']}", headers=admin).status_code == 204
    assert client.get(f"/products/{product['id']}").status_code == 404
    assert client.get(photo["url"]).status_code == 404
