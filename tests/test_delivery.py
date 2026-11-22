import pytest

from app.database import SessionLocal
from app.models import OrderDB, ProductDB, UserDB

CUSTOMER = {"email": "buyer@example.com", "password": "buyerpass123"}
STRANGER = {"email": "nosy@example.com", "password": "nosypass123"}
STAFF = {"email": "staff@example.com", "password": "staffpass123"}

ADDRESS = {
    "recipient_name": "Test Buyer",
    "phone": "9999999999",
    "address_line1": "12 Test Street",
    "address_line2": "Near the park",
    "city": "Hyderabad",
    "state": "Telangana",
    "postal_code": "500001",
    "country": "India",
}


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
def customer(client):
    return register_and_login(client, CUSTOMER)


@pytest.fixture(scope="module")
def stranger(client):
    return register_and_login(client, STRANGER)


@pytest.fixture(scope="module")
def staff(client):
    headers = register_and_login(client, STAFF)
    # Promote straight in the test database, so these tests do not depend on
    # the admin account that test_shop.py creates.
    with SessionLocal() as db:
        user = db.query(UserDB).filter(UserDB.email == STAFF["email"]).first()
        user.is_admin = True
        db.commit()
    return headers


def make_product():
    with SessionLocal() as db:
        product = ProductDB(name="Scarf", price=15.0, stock=50)
        db.add(product)
        db.commit()
        db.refresh(product)
        return product.id


def place_order(client, headers):
    client.delete("/cart", headers=headers)
    added = client.post(
        "/cart/items",
        json={"product_id": make_product(), "quantity": 1},
        headers=headers,
    )
    assert added.status_code == 201, added.text
    placed = client.post("/orders", headers=headers)
    assert placed.status_code == 201, placed.text
    return placed.json()["id"]


def set_status(order_id, status):
    with SessionLocal() as db:
        order = db.get(OrderDB, order_id)
        order.status = status
        db.commit()


def paid_order_with_address(client, customer):
    order_id = place_order(client, customer)
    set_status(order_id, "paid")
    saved = client.put(f"/orders/{order_id}/delivery", json=ADDRESS, headers=customer)
    assert saved.status_code == 200, saved.text
    return order_id


SHIP = {
    "carrier": "Test Courier",
    "tracking_number": "TC123456789",
    "tracking_url": "https://courier.example.com/track/TC123456789",
}


def test_new_order_is_awaiting_an_address(client, customer):
    order_id = place_order(client, customer)
    response = client.get(f"/orders/{order_id}/delivery", headers=customer)
    assert response.status_code == 200
    body = response.json()
    assert body["delivery_status"] == "awaiting_address"
    assert body["address"] is None
    assert [e["status"] for e in body["timeline"]] == ["order_placed"]


def test_customer_can_save_and_change_the_address(client, customer):
    order_id = place_order(client, customer)
    saved = client.put(f"/orders/{order_id}/delivery", json=ADDRESS, headers=customer)
    assert saved.status_code == 200
    assert saved.json()["delivery_status"] == "processing"
    assert saved.json()["address"]["city"] == "Hyderabad"

    changed = dict(ADDRESS, city="Chennai")
    again = client.put(f"/orders/{order_id}/delivery", json=changed, headers=customer)
    assert again.json()["address"]["city"] == "Chennai"


def test_incomplete_address_is_rejected(client, customer):
    order_id = place_order(client, customer)
    broken = {"recipient_name": "Only A Name"}
    response = client.put(f"/orders/{order_id}/delivery", json=broken, headers=customer)
    assert response.status_code == 422


def test_other_customers_cannot_see_or_change_delivery(client, customer, stranger):
    order_id = paid_order_with_address(client, customer)
    assert client.get(f"/orders/{order_id}/delivery", headers=stranger).status_code == 404
    assert (
        client.put(
            f"/orders/{order_id}/delivery", json=ADDRESS, headers=stranger
        ).status_code
        == 404
    )


def test_only_admins_can_ship(client, customer):
    order_id = paid_order_with_address(client, customer)
    response = client.post(
        f"/admin/orders/{order_id}/ship", json=SHIP, headers=customer
    )
    assert response.status_code == 403


def test_unpaid_order_cannot_be_shipped(client, customer, staff):
    order_id = place_order(client, customer)
    client.put(f"/orders/{order_id}/delivery", json=ADDRESS, headers=customer)
    response = client.post(f"/admin/orders/{order_id}/ship", json=SHIP, headers=staff)
    assert response.status_code == 409


def test_order_without_an_address_cannot_be_shipped(client, customer, staff):
    order_id = place_order(client, customer)
    set_status(order_id, "paid")
    response = client.post(f"/admin/orders/{order_id}/ship", json=SHIP, headers=staff)
    assert response.status_code == 409


def test_full_delivery_journey(client, customer, staff):
    order_id = paid_order_with_address(client, customer)

    shipped = client.post(
        f"/admin/orders/{order_id}/ship", json=SHIP, headers=staff
    )
    assert shipped.status_code == 200, shipped.text
    assert shipped.json()["delivery_status"] == "shipped"

    # The customer sees the tracking details
    view = client.get(f"/orders/{order_id}/delivery", headers=customer).json()
    assert view["order_status"] == "shipped"
    assert view["carrier"] == "Test Courier"
    assert view["tracking_number"] == "TC123456789"

    # The address is locked once the parcel is on its way
    locked = client.put(f"/orders/{order_id}/delivery", json=ADDRESS, headers=customer)
    assert locked.status_code == 409

    for step, place in [
        ("in_transit", "Hyderabad hub"),
        ("out_for_delivery", "Local depot"),
        ("delivered", None),
    ]:
        response = client.post(
            f"/admin/orders/{order_id}/tracking",
            json={"status": step, "location": place},
            headers=staff,
        )
        assert response.status_code == 200, response.text

    final = client.get(f"/orders/{order_id}/delivery", headers=customer).json()
    assert final["order_status"] == "delivered"
    assert final["delivery_status"] == "delivered"
    assert final["delivered_at"] is not None
    assert [e["status"] for e in final["timeline"]] == [
        "order_placed",
        "shipped",
        "in_transit",
        "out_for_delivery",
        "delivered",
    ]

    # Nothing can be added after delivery
    late = client.post(
        f"/admin/orders/{order_id}/tracking",
        json={"status": "in_transit"},
        headers=staff,
    )
    assert late.status_code == 409


def test_tracking_cannot_go_backwards(client, customer, staff):
    order_id = paid_order_with_address(client, customer)
    client.post(f"/admin/orders/{order_id}/ship", json=SHIP, headers=staff)
    client.post(
        f"/admin/orders/{order_id}/tracking",
        json={"status": "out_for_delivery"},
        headers=staff,
    )
    response = client.post(
        f"/admin/orders/{order_id}/tracking",
        json={"status": "in_transit"},
        headers=staff,
    )
    assert response.status_code == 409


def test_tracking_needs_a_shipped_order(client, customer, staff):
    order_id = paid_order_with_address(client, customer)
    response = client.post(
        f"/admin/orders/{order_id}/tracking",
        json={"status": "in_transit"},
        headers=staff,
    )
    assert response.status_code == 409


def test_admin_can_list_orders_by_status(client, customer, staff):
    order_id = paid_order_with_address(client, customer)
    client.post(f"/admin/orders/{order_id}/ship", json=SHIP, headers=staff)

    listed = client.get("/admin/orders", params={"status": "shipped"}, headers=staff)
    assert listed.status_code == 200
    ids = [o["id"] for o in listed.json()]
    assert order_id in ids
    assert all(o["status"] == "shipped" for o in listed.json())

    assert client.get("/admin/orders", headers=customer).status_code == 403
