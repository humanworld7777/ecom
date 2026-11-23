import pytest

from app.database import SessionLocal
from app.models import OrderDB, ProductDB, UserDB

STAFF = {"email": "moderator@example.com", "password": "moderatorpass1"}
BUYER_ONE = {"email": "alice@example.com", "password": "alicepass123"}
BUYER_TWO = {"email": "bob@example.com", "password": "bobpass12345"}
STRANGER = {"email": "carol@example.com", "password": "carolpass123"}


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
def staff(client):
    headers = register_and_login(client, STAFF)
    # Promote straight in the test database, so these tests do not depend on
    # the admin account that test_shop.py creates.
    with SessionLocal() as db:
        user = db.query(UserDB).filter(UserDB.email == STAFF["email"]).first()
        user.is_admin = True
        db.commit()
    return headers


@pytest.fixture(scope="module")
def alice(client):
    return register_and_login(client, BUYER_ONE)


@pytest.fixture(scope="module")
def bob(client):
    return register_and_login(client, BUYER_TWO)


@pytest.fixture(scope="module")
def carol(client):
    return register_and_login(client, STRANGER)


def make_product():
    with SessionLocal() as db:
        product = ProductDB(name="Kettle", price=25.0, stock=100)
        db.add(product)
        db.commit()
        db.refresh(product)
        return product.id


def buy(client, headers, product_id, status="paid"):
    """Place an order for the product and set its status, as a payment would."""
    client.delete("/cart", headers=headers)
    added = client.post(
        "/cart/items", json={"product_id": product_id, "quantity": 1}, headers=headers
    )
    assert added.status_code == 201, added.text
    placed = client.post("/orders", headers=headers)
    assert placed.status_code == 201, placed.text
    order_id = placed.json()["id"]
    with SessionLocal() as db:
        order = db.get(OrderDB, order_id)
        order.status = status
        db.commit()
    return order_id


def review(client, headers, product_id, rating, comment=""):
    return client.put(
        f"/products/{product_id}/reviews",
        json={"rating": rating, "comment": comment},
        headers=headers,
    )


def test_a_new_product_has_no_ratings(client):
    product_id = make_product()
    product = client.get(f"/products/{product_id}").json()
    assert product["rating_average"] is None
    assert product["rating_count"] == 0

    reviews = client.get(f"/products/{product_id}/reviews").json()
    assert reviews["count"] == 0
    assert reviews["average"] is None
    assert reviews["reviews"] == []
    assert reviews["breakdown"] == {"5": 0, "4": 0, "3": 0, "2": 0, "1": 0}


def test_reviewing_an_unknown_product_is_a_404(client):
    assert client.get("/products/999999/reviews").status_code == 404


def test_only_signed_in_customers_can_review(client):
    product_id = make_product()
    response = client.put(
        f"/products/{product_id}/reviews", json={"rating": 5, "comment": ""}
    )
    assert response.status_code == 401


def test_only_buyers_can_review(client, carol):
    product_id = make_product()
    assert review(client, carol, product_id, 5).status_code == 403
    status = client.get(f"/products/{product_id}/reviews/mine", headers=carol).json()
    assert status["can_review"] is False


def test_an_unpaid_order_does_not_count_as_a_purchase(client, alice):
    product_id = make_product()
    buy(client, alice, product_id, status="pending_payment")
    assert review(client, alice, product_id, 5).status_code == 403


def test_a_cancelled_order_does_not_count_either(client, alice):
    product_id = make_product()
    buy(client, alice, product_id, status="cancelled")
    assert review(client, alice, product_id, 5).status_code == 403


def test_a_buyer_can_review_and_change_their_mind(client, alice):
    product_id = make_product()
    buy(client, alice, product_id)

    status = client.get(f"/products/{product_id}/reviews/mine", headers=alice).json()
    assert status == {"can_review": True, "review": None}

    first = review(client, alice, product_id, 5, "  Love it  ")
    assert first.status_code == 200, first.text
    assert first.json()["comment"] == "Love it"

    second = review(client, alice, product_id, 3, "It is fine")
    assert second.status_code == 200
    reviews = client.get(f"/products/{product_id}/reviews").json()
    assert reviews["count"] == 1
    assert reviews["average"] == 3.0
    assert reviews["reviews"][0]["comment"] == "It is fine"

    mine = client.get(f"/products/{product_id}/reviews/mine", headers=alice).json()
    assert mine["review"]["rating"] == 3


def test_ratings_must_be_between_one_and_five(client, alice):
    product_id = make_product()
    buy(client, alice, product_id)
    assert review(client, alice, product_id, 0).status_code == 422
    assert review(client, alice, product_id, 6).status_code == 422


def test_average_and_breakdown_across_customers(client, alice, bob):
    product_id = make_product()
    buy(client, alice, product_id)
    buy(client, bob, product_id, status="delivered")
    review(client, alice, product_id, 5, "Great")
    review(client, bob, product_id, 4)

    reviews = client.get(f"/products/{product_id}/reviews").json()
    assert reviews["count"] == 2
    assert reviews["average"] == 4.5
    assert reviews["breakdown"] == {"5": 1, "4": 1, "3": 0, "2": 0, "1": 0}

    # The product list carries the same numbers
    listed = next(p for p in client.get("/products").json() if p["id"] == product_id)
    assert listed["rating_average"] == 4.5
    assert listed["rating_count"] == 2

    # Only the first letter of the email is shown
    names = {r["reviewer"] for r in reviews["reviews"]}
    assert names == {"a***", "b***"}
    assert all("@" not in name for name in names)


def test_a_customer_can_delete_their_own_review(client, alice):
    product_id = make_product()
    buy(client, alice, product_id)
    review(client, alice, product_id, 2)
    assert client.delete(f"/products/{product_id}/reviews/mine", headers=alice).status_code == 204
    assert client.get(f"/products/{product_id}/reviews").json()["count"] == 0


def test_only_admins_can_delete_other_peoples_reviews(client, alice, bob, staff):
    product_id = make_product()
    buy(client, alice, product_id)
    saved = review(client, alice, product_id, 1, "Not for me").json()

    assert client.delete(f"/reviews/{saved['id']}", headers=bob).status_code == 403
    assert client.delete(f"/reviews/{saved['id']}", headers=staff).status_code == 204
    assert client.get(f"/products/{product_id}/reviews").json()["count"] == 0
    assert client.delete(f"/reviews/{saved['id']}", headers=staff).status_code == 404


def test_deleting_a_product_removes_its_reviews(client, alice, staff):
    product_id = make_product()
    buy(client, alice, product_id)
    review(client, alice, product_id, 4)
    assert client.delete(f"/products/{product_id}", headers=staff).status_code == 204
    assert client.get(f"/products/{product_id}/reviews").status_code == 404
