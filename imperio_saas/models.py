
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Float, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

def utcnow():
    return datetime.now(timezone.utc)

# =========================
# MULTI-TENANT: Store = Tenant
# =========================
class Store(Base):
    __tablename__ = "stores"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    # SaaS fields (added via migration)
    segment = Column(String(30), default="deposito", nullable=False)   # deposito / delivery / bar
    plan = Column(String(30), default="basic", nullable=False)         # basic / pro / elite
    subscription_status = Column(String(30), default="trial", nullable=False)  # trial / active / past_due / suspended
    paid_until = Column(DateTime(timezone=True), nullable=True)

    users = relationship("User", back_populates="store")
    branding = relationship("StoreBranding", back_populates="store", uselist=False, cascade="all, delete-orphan")
    features = relationship("StoreFeature", back_populates="store", cascade="all, delete-orphan")

    def ensure_trial(self):
        if not self.paid_until:
            self.paid_until = utcnow() + timedelta(days=7)

class StoreBranding(Base):
    __tablename__ = "store_branding"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"), unique=True, index=True, nullable=False)

    product_name = Column(String(80), default="IMPÉRIO", nullable=False)
    logo_url = Column(String(500), nullable=True)
    primary_color = Column(String(30), default="#2f6bff", nullable=False)
    secondary_color = Column(String(30), default="#9a7bff", nullable=False)
    whatsapp_support = Column(String(40), nullable=True)
    receipt_footer = Column(String(200), nullable=True)

    store = relationship("Store", back_populates="branding")

class StoreFeature(Base):
    __tablename__ = "store_features"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"), index=True, nullable=False)
    key = Column(String(80), nullable=False)
    enabled = Column(Integer, default=0, nullable=False)

    __table_args__ = (UniqueConstraint("store_id", "key", name="uq_store_feature"),)

    store = relationship("Store", back_populates="features")

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"), index=True, nullable=False)
    username = Column(String(80), index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(30), default="admin", nullable=False) # admin / manager / cashier / staff
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    store = relationship("Store", back_populates="users")

class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"), index=True, nullable=False)
    name = Column(String(160), nullable=False)
    sku = Column(String(80), nullable=True)
    price = Column(Float, default=0.0, nullable=False)
    stock = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"), index=True, nullable=False)
    name = Column(String(160), nullable=False)
    phone = Column(String(60), nullable=True)
    address = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

class Sale(Base):
    __tablename__ = "sales"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"), index=True, nullable=False)
    customer_name = Column(String(160), nullable=True)
    total = Column(Float, default=0.0, nullable=False)
    status = Column(String(40), default="concluida", nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

class SaleItem(Base):
    __tablename__ = "sale_items"
    id = Column(Integer, primary_key=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), index=True, nullable=False)
    product_name = Column(String(160), nullable=False)
    qty = Column(Integer, default=1, nullable=False)
    price = Column(Float, default=0.0, nullable=False)
    line_total = Column(Float, default=0.0, nullable=False)

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"), index=True, nullable=False)
    customer_name = Column(String(160), nullable=True)
    status = Column(String(40), default="novo", nullable=False)
    total = Column(Float, default=0.0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), index=True, nullable=False)
    product_name = Column(String(160), nullable=False)
    qty = Column(Integer, default=1, nullable=False)
    price = Column(Float, default=0.0, nullable=False)
    line_total = Column(Float, default=0.0, nullable=False)
