from fastapi import APIRouter, Query

from .state import world

router = APIRouter(prefix="/tools")


@router.get("/search_inventory")
def search_inventory(sku: str = Query(...)):
    return world.search_inventory(sku)


@router.get("/get_customer")
def get_customer(id: str = Query(...)):
    return world.get_customer(id)


@router.get("/get_pricing")
def get_pricing(sku: str = Query(...), tier: str = Query(...)):
    return world.get_pricing(sku, tier)


@router.get("/list_orders")
def list_orders(customer_id: str = Query(...)):
    return {"orders": world.list_orders(customer_id)}


@router.post("/place_order")
def place_order(customer_id: str, sku: str, qty: int):
    return world.place_order(customer_id, sku, qty)


@router.post("/cancel_order")
def cancel_order(order_id: str):
    return world.cancel_order(order_id)