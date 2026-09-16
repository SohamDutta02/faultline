from datetime import datetime, timezone

BASE_TIME = datetime(2026, 1, 15, 9, 0, 0, tzinfo=timezone.utc)

SKUS = [
    {"sku": "A-11", "name": "hex bolt m8", "qty": 240, "base_price": 0.45},
    {"sku": "A-22", "name": "hex bolt m10", "qty": 12, "base_price": 0.62},
    {"sku": "A-31", "name": "flange nut m8", "qty": 180, "base_price": 0.30},
    {"sku": "B-04", "name": "roller bearing 6203", "qty": 44, "base_price": 7.90},
    {"sku": "B-17", "name": "roller bearing 6205", "qty": 8, "base_price": 11.25},
    {"sku": "C-02", "name": "drive belt a38", "qty": 62, "base_price": 14.00},
    {"sku": "C-09", "name": "drive belt b52", "qty": 3, "base_price": 21.50},
    {"sku": "D-40", "name": "hydraulic seal kit", "qty": 27, "base_price": 33.75},
    {"sku": "D-55", "name": "o-ring assortment", "qty": 310, "base_price": 5.10},
    {"sku": "E-08", "name": "coupling sleeve", "qty": 0, "base_price": 18.40},
]

CUSTOMERS = [
    {"id": "CUST-100", "name": "meridian works", "tier": "gold", "credit_limit": 5000.0},
    {"id": "CUST-101", "name": "apex fabrication", "tier": "silver", "credit_limit": 2000.0},
    {"id": "CUST-102", "name": "riverside plant", "tier": "standard", "credit_limit": 750.0},
    {"id": "CUST-103", "name": "northgate assembly", "tier": "gold", "credit_limit": 9000.0},
]

TIER_MULTIPLIER = {"gold": 0.85, "silver": 0.92, "standard": 1.0}

QTY_JITTER = 0.15