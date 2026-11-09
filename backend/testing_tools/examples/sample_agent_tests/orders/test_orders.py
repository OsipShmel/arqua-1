import pytest

STATUSES = {"new", "cancelled"}


@pytest.fixture
def order(api):
    """A fresh order for 3 croissants; cancelled afterwards so stock is restored."""
    r = api.post("/orders", json={"items": [{"sku": "croissant", "quantity": 3}]})
    assert r.status_code == 201
    yield r.json()
    api.post(f"/orders/{r.json()['id']}/cancel")


def test_list_products(api):
    r = api.get("/products")
    assert r.status_code == 200
    for product in r.json():
        assert isinstance(product["sku"], str)
        assert isinstance(product["price"], (int, float))
        assert isinstance(product["stock"], int) and product["stock"] >= 0


def test_get_product(api):
    r = api.get("/products/croissant")
    assert r.status_code == 200
    assert r.json()["sku"] == "croissant"


def test_unknown_product(api):
    assert api.get("/products/muffin").status_code == 404


def test_order_total_is_a_number(order):
    assert order["status"] == "new"
    assert isinstance(order["total"], (int, float))
    assert order["total"] == 7.5


def test_negative_quantity_is_rejected(api):
    r = api.post("/orders", json={"items": [{"sku": "croissant", "quantity": -3}]})
    assert r.status_code == 422


def test_out_of_stock(api):
    r = api.post("/orders", json={"items": [{"sku": "cake", "quantity": 1}]})
    assert r.status_code == 409


def test_get_order(api):
    r = api.get("/orders/1")
    assert r.status_code == 200
    assert r.json()["status"] in STATUSES


def test_cancelled_order_status(api, order):
    api.post(f"/orders/{order['id']}/cancel")
    r = api.get(f"/orders/{order['id']}")
    assert r.json()["status"] == "cancelled"


def test_cancel_twice_is_conflict(api, order):
    assert api.post(f"/orders/{order['id']}/cancel").status_code == 200
    assert api.post(f"/orders/{order['id']}/cancel").status_code == 409
