
from __future__ import annotations

from sqlalchemy import text, inspect
from sqlalchemy.orm import Session

from .models import Base, Store, StoreBranding, StoreFeature

DEFAULT_FEATURES = {
    # Core
    "core_products": True,
    "core_sales": True,
    "core_customers": True,
    "core_dashboard": True,
    # Segments
    "segment_orders": True,          # delivery / depósito
    "segment_tables": False,         # bar (placeholder)
    # Premium
    "reports_export": False,
    "finance_module": False,
    "multi_user": False,
    "white_label": False,
}

def ensure_schema(engine):
    """
    Create tables and add missing SaaS columns for existing DBs.
    """
    Base.metadata.create_all(bind=engine)

    insp = inspect(engine)
    if "stores" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("stores")}
        # Add columns if missing (SQLite/Postgres compatible)
        # Using ALTER TABLE ADD COLUMN is safe for SQLite/Postgres.
        add_cols = []
        if "segment" not in cols:
            add_cols.append(("segment", "VARCHAR(30)", "'deposito'"))
        if "plan" not in cols:
            add_cols.append(("plan", "VARCHAR(30)", "'basic'"))
        if "subscription_status" not in cols:
            add_cols.append(("subscription_status", "VARCHAR(30)", "'trial'"))
        if "paid_until" not in cols:
            add_cols.append(("paid_until", "TIMESTAMP", "NULL"))

        with engine.begin() as conn:
            for name, sqltype, default in add_cols:
                conn.execute(text(f"ALTER TABLE stores ADD COLUMN {name} {sqltype} DEFAULT {default}"))

def seed_store_defaults(db: Session):
    """
    Ensure each store has branding and default features rows.
    """
    stores = db.query(Store).all()
    for s in stores:
        s.ensure_trial()

        if not s.branding:
            s.branding = StoreBranding(
                product_name="IMPÉRIO",
                primary_color="#2f6bff",
                secondary_color="#9a7bff",
            )

        existing = {f.key for f in s.features}
        for k, enabled in DEFAULT_FEATURES.items():
            if k not in existing:
                s.features.append(StoreFeature(key=k, enabled=1 if enabled else 0))

    db.commit()
