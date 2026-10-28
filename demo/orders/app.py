"""Demo Orders: openapi.yaml is the contract; defects O1-O4 (demo/README.md) are on purpose."""

from pathlib import Path as FilePath
from typing import Any

from fastapi import HTTPException, Path, Request
from fastapi.responses import JSONResponse

from common import contract_app

app = contract_app(FilePath(__file__).with_name("openapi.yaml"))

PRODUCTS: dict[str, dict[str, Any]] = {
    "croissant": {"sku": "croissant", "name": "Croissant", "price": 2.5, "stock": 50},
    "baguette": {"sku": "baguette", "name": "Baguette", "price": 3.0, "stock": 20},
    "cake": {"sku": "cake", "name": "Cake", "price": 25.0, "stock": 0},
}
ORDERS: dict[int, dict[str, Any]] = {
    1: {"id": 1, "status": "new", "items": [{"sku": "croissant", "quantity": 2}], "total": 5.0},
    2: {"id": 2, "status": "new", "items": [{"sku": "baguette", "quantity": 1}], "total": 3.0},
}
SKU = r"^[a-z-]{1,32}$"


@app.get("/products")
def list_products() -> list[dict[str, Any]]:
    return list(PRODUCTS.values())


@app.get("/products/{sku}")
def get_product(sku: str = Path(pattern=SKU)) -> dict[str, Any]:
    product = PRODUCTS.get(sku)
    if product is None:
        raise HTTPException(404, "product not found")
    return product


@app.post("/orders", status_code=201)
async def create_order(request: Request) -> Any:
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"detail": "body must be JSON"}, 422)
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list) or not 1 <= len(items) <= 10:
        return JSONResponse({"detail": "items must hold 1..10 entries"}, 422)
    for item in items:
        if not (
            isinstance(item, dict)
            and isinstance(item.get("sku"), str)
            and type(item.get("quantity")) is int
        ):
            return JSONResponse({"detail": "each item needs sku and integer quantity"}, 422)
        if item["quantity"] > 100:
            return JSONResponse({"detail": "quantity is at most 100"}, 422)
        # O1: quantity must be >= 1, but zero and negative quantities are accepted
    for item in items:
        product = PRODUCTS.get(item["sku"])
        if product is None:
            return JSONResponse({"detail": f"unknown product {item['sku']}"}, 404)
        if product["stock"] < item["quantity"]:
            return JSONResponse({"detail": f"not enough {item['sku']}"}, 409)
    total = 0.0
    for item in items:
        PRODUCTS[item["sku"]]["stock"] -= item["quantity"]
        total += PRODUCTS[item["sku"]]["price"] * item["quantity"]
    order_id = max(ORDERS, default=0) + 1
    ORDERS[order_id] = {
        "id": order_id,
        "status": "new",
        "items": [{"sku": i["sku"], "quantity": i["quantity"]} for i in items],
        # O2: totals with cents are rendered as a string ("7.50"), the contract says number
        "total": total if total == int(total) else f"{total:.2f}",
    }
    return ORDERS[order_id]


@app.get("/orders/{orderId}")
def get_order(orderId: int = Path(ge=1)) -> dict[str, Any]:  # noqa: N803 - contract name
    order = ORDERS.get(orderId)
    if order is None:
        raise HTTPException(404, "order not found")
    if order["status"] == "cancelled":
        # O4: cancelled orders are reported as "canceled", which is not in the enum
        return order | {"status": "canceled"}
    return order


@app.post("/orders/{orderId}/cancel")
def cancel_order(orderId: int = Path(ge=1)) -> Any:  # noqa: N803
    order = ORDERS.get(orderId)
    if order is None:
        raise HTTPException(404, "order not found")
    if order["status"] == "cancelled":
        # O3: cancelling twice must be 409; the service crashes instead
        return JSONResponse({"detail": "internal error"}, 500)
    for item in order["items"]:
        PRODUCTS[item["sku"]]["stock"] += item["quantity"]
    order["status"] = "cancelled"
    return order
