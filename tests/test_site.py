def test_storefront_page_is_served(client):
    response = client.get("/shop")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "ZAPP Store" in response.text


def test_health_route_still_returns_json(client):
    assert client.get("/").json() == {"status": "ok"}
