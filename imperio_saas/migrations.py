from __future__ import annotations

from sqlalchemy import inspect, text
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
    "segment_tables": False,         # bar
    # Premium
    "reports_export": False,
    "finance_module": False,
    "multi_user": False,
    "white_label": False,
    "theme_custom": False,
}

def _ensure_column(conn, table: str, col: str, sqltype: str, default_sql: str | None = None):
    """Add missing column via ALTER TABLE ADD COLUMN (Postgres-safe)."""
    if default_sql is None:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {sqltype}"))
    else:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {sqltype} DEFAULT {default_sql}"))

def ensure_schema(engine):
    """Create tables and add missing columns for existing databases."""
    Base.metadata.create_all(bind=engine)
    insp = inspect(engine)

    def has_table(t: str) -> bool:
        return t in insp.get_table_names()

    with engine.begin() as conn:
        # stores
        if has_table("stores"):
            cols = {c["name"] for c in insp.get_columns("stores")}
            if "segment" not in cols:
                _ensure_column(conn, "stores", "segment", "VARCHAR(30)", "'deposito'")
            if "plan" not in cols:
                _ensure_column(conn, "stores", "plan", "VARCHAR(30)", "'basic'")
            if "subscription_status" not in cols:
                _ensure_column(conn, "stores", "subscription_status", "VARCHAR(30)", "'trial'")
            if "paid_until" not in cols:
                _ensure_column(conn, "stores", "paid_until", "TIMESTAMP", "NULL")
            if "next_order_seq" not in cols:
                _ensure_column(conn, "stores", "next_order_seq", "INTEGER", "1")
            if "next_sale_seq" not in cols:
                _ensure_column(conn, "stores", "next_sale_seq", "INTEGER", "1")
            if "next_tab_seq" not in cols:
                _ensure_column(conn, "stores", "next_tab_seq", "INTEGER", "1")

        # store_branding (new theme fields)
        if has_table("store_branding"):
            bcols = {c["name"] for c in insp.get_columns("store_branding")}
            if "theme_mode" not in bcols:
                _ensure_column(conn, "store_branding", "theme_mode", "VARCHAR(10)", "'dark'")
            if "bg_color" not in bcols:
                _ensure_column(conn, "store_branding", "bg_color", "VARCHAR(30)", "'#0b0f14'")
            if "whatsapp_support" not in bcols:
                _ensure_column(conn, "store_branding", "whatsapp_support", "VARCHAR(40)", "NULL")
            if "receipt_footer" not in bcols:
                _ensure_column(conn, "store_branding", "receipt_footer", "VARCHAR(200)", "NULL")

        # orders
        if has_table("orders"):
            ocols = {c["name"] for c in insp.get_columns("orders")}
            if "number" not in ocols:
                _ensure_column(conn, "orders", "number", "VARCHAR(20)", "NULL")
            if "converted_sale_id" not in ocols:
                _ensure_column(conn, "orders", "converted_sale_id", "INTEGER", "NULL")

        # sales
        if has_table("sales"):
            scols = {c["name"] for c in insp.get_columns("sales")}
            if "number" not in scols:
                _ensure_column(conn, "sales", "number", "VARCHAR(20)", "NULL")

        # tabs (bar)
        if has_table("tabs"):
            tcols = {c["name"] for c in insp.get_columns("tabs")}
            if "number" not in tcols:
                _ensure_column(conn, "tabs", "number", "VARCHAR(20)", "NULL")

def seed_store_defaults(db: Session):
    """Ensure each store has branding + features rows."""
    stores = db.query(Store).all()
    for s in stores:
        s.ensure_trial()

        if not s.branding:
            s.branding = StoreBranding(
                product_name="IMPÉRIO",
                primary_color="#2f6bff",
                secondary_color="#9a7bff",
                theme_mode="dark",
                bg_color="#0b0f14",
            )
        else:
            # fill new fields if missing
            if not getattr(s.branding, "theme_mode", None):
                s.branding.theme_mode = "dark"
            if not getattr(s.branding, "bg_color", None):
                s.branding.bg_color = "#0b0f14"

        existing = {f.key for f in s.features}
        for k, enabled in DEFAULT_FEATURES.items():
            if k not in existing:
                s.features.append(StoreFeature(key=k, enabled=1 if enabled else 0))

    db.commit()
