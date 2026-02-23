from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, ForeignKey, Float,
    Text, func
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship

from passlib.context import CryptContext
from starlette.templating import Jinja2Templates

# =========================
# APP / PATHS
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = FastAPI()

# Static
if not os.path.isdir(STATIC_DIR):
    os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# =========================
# DB
# =========================
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./loja.db")

# Render Postgres sometimes uses "postgres://". SQLAlchemy expects "postgresql://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

Base = declarative_base()

# =========================
# AUTH / SECURITY
# =========================
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def utcnow():
    return datetime.now(timezone.utc)

# =========================
# MODELS
# =========================
class Store(Base):
    __tablename__ = "stores"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), unique=True, index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    users = relationship("User", back_populates="store")

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    store_id = Column(Integer, ForeignKey("stores.id"), index=True, nullable=False)
    username = Column(String(80), index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(30), default="admin", nullable=False)
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
    status = Column(String(40), default="concluida", nullable=False)  # concluida / pendente
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
    status = Column(String(40), default="novo", nullable=False)  # novo/separando/saiu/entregue/cancelado
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

# =========================
# DB HELPERS
# =========================
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def init_db():
    Base.metadata.create_all(bind=engine)

def hash_password(pw: str) -> str:
    return pwd_context.hash(pw)

def verify_password(pw: str, pw_hash: str) -> bool:
    return pwd_context.verify(pw, pw_hash)

def get_store_by_name(db: Session, store_name: str) -> Optional[Store]:
    return db.query(Store).filter(func.lower(Store.name) == store_name.strip().lower()).first()

def get_user(db: Session, store_id: int, username: str) -> Optional[User]:
    return db.query(User).filter(User.store_id == store_id, func.lower(User.username) == username.strip().lower()).first()

# =========================
# SIMPLE ERROR LOGGER
# =========================
LAST_ERROR = None

@app.middleware("http")
async def log_exceptions(request: Request, call_next):
    global LAST_ERROR
    try:
        return await call_next(request)
    except Exception as e:
        import traceback
        LAST_ERROR = f"URL: {request.url}\n\n{traceback.format_exc()}"
        raise

@app.get("/debug/last_error", response_class=HTMLResponse)
def debug_last_error():
    global LAST_ERROR
    if not LAST_ERROR:
        return "<pre>Sem erros registrados.</pre>"
    return "<pre>" + LAST_ERROR.replace("<", "&lt;") + "</pre>"

# =========================
# AUTH DEPENDENCY
# =========================
class SimpleUser:
    def __init__(self, id: int, store_id: int, username: str, store_name: str):
        self.id = id
        self.store_id = store_id
        self.username = username
        self.store_name = store_name

def require_auth(request: Request, db: Session = Depends(get_db)) -> SimpleUser:
    user_id = request.cookies.get("user_id")
    store_id = request.cookies.get("store_id")
    if not user_id or not store_id:
        raise HTTPException(status_code=401)

    u = db.query(User).filter(User.id == int(user_id), User.store_id == int(store_id)).first()
    if not u:
        raise HTTPException(status_code=401)

    s = db.query(Store).filter(Store.id == u.store_id).first()
    return SimpleUser(id=u.id, store_id=u.store_id, username=u.username, store_name=(s.name if s else ""))

def redirect_login():
    return RedirectResponse(url="/login", status_code=302)

@app.exception_handler(HTTPException)
def auth_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        return redirect_login()
    return HTMLResponse(f"Erro: {exc.detail}", status_code=exc.status_code)

# =========================
# ROUTES
# =========================
@app.on_event("startup")
def _startup():
    init_db()

@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse("/login", status_code=302)

# ---------- PUBLIC: LOGIN ----------
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "user": None, "title": "Login"})

@app.post("/login")
def login_action(
    request: Request,
    store_name: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    store = get_store_by_name(db, store_name)
    if not store:
        return templates.TemplateResponse("login.html", {"request": request, "user": None, "error": "Loja não encontrada."})

    user = get_user(db, store.id, username)
    if not user:
        return templates.TemplateResponse("login.html", {"request": request, "user": None, "error": "Usuário/senha inválidos."})

    if not verify_password(password, user.password_hash):
        return templates.TemplateResponse("login.html", {"request": request, "user": None, "error": "Usuário/senha inválidos."})

    resp = RedirectResponse("/dashboard", status_code=302)
    # Cookies simples (para MVP). Em produção, use Secure/HttpOnly + sessão JWT/redis.
    resp.set_cookie("user_id", str(user.id), httponly=True, samesite="lax")
    resp.set_cookie("store_id", str(store.id), httponly=True, samesite="lax")
    return resp

@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie("user_id")
    resp.delete_cookie("store_id")
    return resp

# ---------- PUBLIC: ADMIN SETUP ----------
@app.get("/admin/setup", response_class=HTMLResponse)
def admin_setup_page(request: Request):
    return templates.TemplateResponse("setup.html", {"request": request, "user": None, "title": "Criar Loja"})

@app.post("/admin/setup")
def admin_setup_action(
    request: Request,
    store_name: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    store_name = store_name.strip()
    username = username.strip()

    if len(password.encode("utf-8")) > 72:
        return templates.TemplateResponse("setup.html", {"request": request, "user": None, "error": "Senha muito grande. Use até 72 bytes."})

    if get_store_by_name(db, store_name):
        return templates.TemplateResponse("setup.html", {"request": request, "user": None, "error": "Essa loja já existe."})

    store = Store(name=store_name)
    db.add(store)
    db.commit()
    db.refresh(store)

    user = User(store_id=store.id, username=username, password_hash=hash_password(password), role="admin")
    db.add(user)
    db.commit()

    return RedirectResponse("/login", status_code=302)

# ---------- DASHBOARD ----------
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    # stats simples
    today = datetime.now(timezone.utc).date()
    month = datetime.now(timezone.utc).month
    year = datetime.now(timezone.utc).year

    sales_today_value = db.query(func.coalesce(func.sum(Sale.total), 0.0)).filter(
        Sale.store_id == user.store_id,
        func.date(Sale.created_at) == str(today)
    ).scalar() or 0.0

    sales_today_count = db.query(func.count(Sale.id)).filter(
        Sale.store_id == user.store_id,
        func.date(Sale.created_at) == str(today)
    ).scalar() or 0

    sales_month_value = db.query(func.coalesce(func.sum(Sale.total), 0.0)).filter(
        Sale.store_id == user.store_id,
        func.strftime("%m", Sale.created_at) == f"{month:02d}" if DATABASE_URL.startswith("sqlite") else func.extract("month", Sale.created_at) == month,
        func.strftime("%Y", Sale.created_at) == str(year) if DATABASE_URL.startswith("sqlite") else func.extract("year", Sale.created_at) == year,
    ).scalar() or 0.0

    pending_orders = db.query(func.count(Order.id)).filter(
        Order.store_id == user.store_id,
        Order.status.in_(["novo", "separando", "saiu"])
    ).scalar() or 0

    low_stock = db.query(func.count(Product.id)).filter(
        Product.store_id == user.store_id,
        Product.stock <= 3
    ).scalar() or 0

    last_sales = db.query(Sale).filter(Sale.store_id == user.store_id).order_by(Sale.id.desc()).limit(10).all()

    stats = {
        "sales_today_value": float(sales_today_value),
        "sales_today_count": int(sales_today_count),
        "sales_month_value": float(sales_month_value),
        "pending_orders": int(pending_orders),
        "low_stock": int(low_stock),
    }
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "user": user, "stats": stats, "last_sales": last_sales,
        "kicker": "Visão geral", "page_title": "Dashboard"
    })

# ---------- PRODUCTS ----------
@app.get("/products", response_class=HTMLResponse)
def products_page(request: Request, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    products = db.query(Product).filter(Product.store_id == user.store_id).order_by(Product.id.desc()).all()
    return templates.TemplateResponse("products.html", {"request": request, "user": user, "products": products, "kicker": "Cadastro", "page_title": "Produtos"})

@app.post("/products/create")
def products_create(
    request: Request,
    name: str = Form(...),
    sku: str = Form(""),
    price: float = Form(0.0),
    stock: int = Form(0),
    user: SimpleUser = Depends(require_auth),
    db: Session = Depends(get_db),
):
    p = Product(store_id=user.store_id, name=name.strip(), sku=sku.strip() or None, price=float(price or 0), stock=int(stock or 0))
    db.add(p)
    db.commit()
    return RedirectResponse("/products", status_code=302)

# ---------- CUSTOMERS ----------
@app.get("/customers", response_class=HTMLResponse)
def customers_page(request: Request, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    customers = db.query(Customer).filter(Customer.store_id == user.store_id).order_by(Customer.id.desc()).all()
    return templates.TemplateResponse("customers.html", {"request": request, "user": user, "customers": customers, "kicker": "Cadastro", "page_title": "Clientes"})

@app.post("/customers/create")
def customers_create(
    name: str = Form(...),
    phone: str = Form(""),
    address: str = Form(""),
    user: SimpleUser = Depends(require_auth),
    db: Session = Depends(get_db),
):
    c = Customer(store_id=user.store_id, name=name.strip(), phone=phone.strip() or None, address=address.strip() or None)
    db.add(c)
    db.commit()
    return RedirectResponse("/customers", status_code=302)

# ---------- SALES ----------
@app.get("/sales", response_class=HTMLResponse)
def sales_page(request: Request, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    sales = db.query(Sale).filter(Sale.store_id == user.store_id).order_by(Sale.id.desc()).limit(200).all()
    return templates.TemplateResponse("sales.html", {"request": request, "user": user, "sales": sales, "kicker": "Movimento", "page_title": "Vendas"})

@app.get("/sales/new", response_class=HTMLResponse)
def sale_new_page(request: Request, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    products = db.query(Product).filter(Product.store_id == user.store_id).order_by(Product.name.asc()).all()
    customers = db.query(Customer).filter(Customer.store_id == user.store_id).order_by(Customer.name.asc()).all()
    return templates.TemplateResponse("sale_new.html", {"request": request, "user": user, "products": products, "customers": customers, "kicker": "Movimento", "page_title": "Nova venda"})

@app.post("/sales/create")
def sale_create(
    request: Request,
    customer_name: str = Form(""),
    item_name: List[str] = Form([]),
    item_qty: List[int] = Form([]),
    item_price: List[float] = Form([]),
    user: SimpleUser = Depends(require_auth),
    db: Session = Depends(get_db),
):
    # build items
    lines = []
    total = 0.0
    for n, q, pr in zip(item_name, item_qty, item_price):
        n = (n or "").strip()
        if not n:
            continue
        qty = int(q or 0)
        price = float(pr or 0)
        if qty <= 0:
            continue
        line_total = qty * price
        total += line_total
        lines.append((n, qty, price, line_total))

    sale = Sale(store_id=user.store_id, customer_name=customer_name.strip() or None, total=total, status="concluida")
    db.add(sale)
    db.commit()
    db.refresh(sale)

    for n, qty, price, line_total in lines:
        db.add(SaleItem(sale_id=sale.id, product_name=n, qty=qty, price=price, line_total=line_total))

        # baixa estoque se produto existir por nome
        p = db.query(Product).filter(Product.store_id == user.store_id, func.lower(Product.name) == n.lower()).first()
        if p:
            p.stock = max(0, int(p.stock) - qty)

    db.commit()
    return RedirectResponse("/sales", status_code=302)

# ---------- ORDERS ----------
@app.get("/orders", response_class=HTMLResponse)
def orders_page(request: Request, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    orders = db.query(Order).filter(Order.store_id == user.store_id).order_by(Order.id.desc()).limit(300).all()
    return templates.TemplateResponse("orders.html", {"request": request, "user": user, "orders": orders, "kicker": "Movimento", "page_title": "Pedidos"})

@app.get("/orders/new", response_class=HTMLResponse)
def order_new_page(request: Request, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    products = db.query(Product).filter(Product.store_id == user.store_id).order_by(Product.name.asc()).all()
    customers = db.query(Customer).filter(Customer.store_id == user.store_id).order_by(Customer.name.asc()).all()
    return templates.TemplateResponse("order_new.html", {"request": request, "user": user, "products": products, "customers": customers, "kicker": "Movimento", "page_title": "Novo pedido"})

@app.post("/orders/create")
def order_create(
    customer_name: str = Form(""),
    item_name: List[str] = Form([]),
    item_qty: List[int] = Form([]),
    item_price: List[float] = Form([]),
    user: SimpleUser = Depends(require_auth),
    db: Session = Depends(get_db),
):
    lines = []
    total = 0.0
    for n, q, pr in zip(item_name, item_qty, item_price):
        n = (n or "").strip()
        if not n:
            continue
        qty = int(q or 0)
        price = float(pr or 0)
        if qty <= 0:
            continue
        line_total = qty * price
        total += line_total
        lines.append((n, qty, price, line_total))

    order = Order(store_id=user.store_id, customer_name=customer_name.strip() or None, total=total, status="novo")
    db.add(order)
    db.commit()
    db.refresh(order)

    for n, qty, price, line_total in lines:
        db.add(OrderItem(order_id=order.id, product_name=n, qty=qty, price=price, line_total=line_total))

    db.commit()
    return RedirectResponse("/orders", status_code=302)

@app.post("/orders/{order_id}/status")
def order_set_status(order_id: int, status: str = Form(...), user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.store_id == user.store_id, Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Pedido não encontrado.")

    status = (status or "").strip().lower()
    valid = {"novo", "separando", "saiu", "entregue", "cancelado"}
    if status not in valid:
        raise HTTPException(status_code=400, detail="Status inválido.")

    order.status = status
    db.commit()

    # ✅ Se entregou, vira venda concluída automaticamente
    if status == "entregue":
        items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
        sale = Sale(store_id=user.store_id, customer_name=order.customer_name, total=order.total, status="concluida")
        db.add(sale)
        db.commit()
        db.refresh(sale)

        for it in items:
            db.add(SaleItem(
                sale_id=sale.id,
                product_name=it.product_name,
                qty=it.qty,
                price=it.price,
                line_total=it.line_total
            ))
            p = db.query(Product).filter(Product.store_id == user.store_id, func.lower(Product.name) == it.product_name.lower()).first()
            if p:
                p.stock = max(0, int(p.stock) - int(it.qty))

        db.commit()

    return RedirectResponse("/orders", status_code=302)