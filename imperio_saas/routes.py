
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional, List

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from starlette.templating import Jinja2Templates

from .db import engine
from .deps import get_db, require_auth, SimpleUser, require_feature
from .models import Store, User, Product, Customer, Sale, SaleItem, Order, OrderItem, StoreBranding, StoreFeature
from .security import hash_password, verify_password
from .migrations import seed_store_defaults

import os

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(PROJECT_DIR, "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

router = APIRouter()

# =========================
# Helpers
# =========================
def redirect_login():
    return RedirectResponse(url="/login", status_code=302)

def get_store_by_name(db: Session, store_name: str) -> Optional[Store]:
    return db.query(Store).filter(func.lower(Store.name) == store_name.strip().lower()).first()

def get_user(db: Session, store_id: int, username: str) -> Optional[User]:
    return db.query(User).filter(User.store_id == store_id, func.lower(User.username) == username.strip().lower()).first()

def get_current_store(db: Session, user: SimpleUser) -> Store:
    s = db.query(Store).filter(Store.id == user.store_id).first()
    if not s:
        raise HTTPException(status_code=401)
    return s

def ensure_store_ready(db: Session, store: Store):
    # ensure branding + features exist
    if not store.branding:
        store.branding = StoreBranding(product_name="IMPÉRIO")
    store.ensure_trial()
    db.commit()
    seed_store_defaults(db)

def ctx(request: Request, db: Session, user: Optional[SimpleUser]):
    branding = None
    segment = None
    features = {}
    plan = None
    store = None
    if user:
        store = db.query(Store).filter(Store.id == user.store_id).first()
        if store:
            branding = store.branding
            segment = store.segment
            plan = store.plan
            for f in store.features:
                features[f.key] = bool(int(f.enabled))
    return {"request": request, "user": user, "branding": branding, "segment": segment, "features": features, "plan": plan, "store": store}

# =========================
# PUBLIC
# =========================
@router.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/login", status_code=302)

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "user": None, "title": "Login"})

@router.post("/login")
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

    ensure_store_ready(db, store)

    user = get_user(db, store.id, username)
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse("login.html", {"request": request, "user": None, "error": "Usuário ou senha inválidos."})

    resp = RedirectResponse("/dashboard", status_code=302)
    # session cookie (simple)
    resp.set_cookie("user_id", str(user.id), httponly=True, samesite="lax")
    resp.set_cookie("store_id", str(store.id), httponly=True, samesite="lax")
    return resp

@router.get("/logout")
def logout():
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie("user_id")
    resp.delete_cookie("store_id")
    return resp

# ---------- PUBLIC: ADMIN SETUP ----------
@router.get("/admin/setup", response_class=HTMLResponse)
def admin_setup_page(request: Request):
    return templates.TemplateResponse("setup.html", {"request": request, "user": None, "title": "Criar Loja"})

@router.post("/admin/setup")
def admin_setup_action(
    request: Request,
    store_name: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    segment: str = Form("deposito"),
    db: Session = Depends(get_db),
):
    store_name = store_name.strip()
    username = username.strip()

    if len(password.encode("utf-8")) > 72:
        return templates.TemplateResponse("setup.html", {"request": request, "user": None, "error": "Senha muito grande. Use até 72 bytes."})

    if get_store_by_name(db, store_name):
        return templates.TemplateResponse("setup.html", {"request": request, "user": None, "error": "Essa loja já existe."})

    store = Store(name=store_name, segment=segment, plan="trial", subscription_status="trial")
    store.ensure_trial()
    store.branding = StoreBranding(product_name="IMPÉRIO", whatsapp_support=os.getenv("SUPPORT_WHATSAPP"))
    db.add(store)
    db.commit()
    db.refresh(store)

    # default features seeded
    seed_store_defaults(db)

    user = User(store_id=store.id, username=username, password_hash=hash_password(password), role="admin")
    db.add(user)
    db.commit()

    return RedirectResponse("/login", status_code=302)

# =========================
# BILLING / SUBSCRIPTION
# =========================
@router.get("/billing", response_class=HTMLResponse)
def billing_page(request: Request, db: Session = Depends(get_db)):
    # If logged, show tenant context; else simple page
    user_id = request.cookies.get("user_id")
    store_id = request.cookies.get("store_id")
    user = None
    store = None
    if user_id and store_id:
        u = db.query(User).filter(User.id == int(user_id), User.store_id == int(store_id)).first()
        if u:
            store = db.query(Store).filter(Store.id == u.store_id).first()
            if store:
                user = SimpleUser(id=u.id, store_id=u.store_id, username=u.username, store_name=store.name, role=u.role, segment=store.segment, plan=store.plan)

    pix = os.getenv("PIX_KEY", "")
    price = os.getenv("PRICE_ELITE", "157,00")
    support = os.getenv("SUPPORT_WHATSAPP", "")
    message = "Faça o PIX e envie o comprovante no WhatsApp para liberar/renovar sua assinatura."
    data = ctx(request, db, user)
    data.update({"title":"Assinatura", "kicker":"Assinatura", "page_title":"Ativar assinatura", "pix_key":pix, "price":price, "support":support, "message":message, "store_obj":store})
    return templates.TemplateResponse("billing.html", data)

# =========================
# DASHBOARD
# =========================
@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    require_feature(db, user.store_id, "core_dashboard")

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

    # SQLite vs Postgres month/year
    from .db import DATABASE_URL
    if DATABASE_URL.startswith("sqlite"):
        month_filter = func.strftime("%m", Sale.created_at) == f"{month:02d}"
        year_filter = func.strftime("%Y", Sale.created_at) == str(year)
    else:
        month_filter = func.extract("month", Sale.created_at) == month
        year_filter = func.extract("year", Sale.created_at) == year

    sales_month_value = db.query(func.coalesce(func.sum(Sale.total), 0.0)).filter(
        Sale.store_id == user.store_id,
        month_filter, year_filter
    ).scalar() or 0.0

    pending_orders = db.query(func.count(Order.id)).filter(
        Order.store_id == user.store_id,
        Order.status.in_(["novo", "separando", "saiu"])
    ).scalar() or 0

    low_stock = db.query(func.count(Product.id)).filter(
        Product.store_id == user.store_id,
        Product.stock <= 3
    ).scalar() or 0

    # Ticket médio
    ticket_avg = 0.0
    if int(sales_today_count) > 0:
        ticket_avg = float(sales_today_value) / int(sales_today_count)

    last_sales = db.query(Sale).filter(Sale.store_id == user.store_id).order_by(Sale.id.desc()).limit(10).all()

    stats = {
        "sales_today_value": float(sales_today_value),
        "sales_today_count": int(sales_today_count),
        "sales_month_value": float(sales_month_value),
        "pending_orders": int(pending_orders),
        "low_stock": int(low_stock),
        "ticket_avg": float(ticket_avg),
    }

    data = ctx(request, db, user)
    data.update({"stats": stats, "last_sales": last_sales, "kicker": "Visão geral", "page_title": "Dashboard", "title":"Dashboard"})
    return templates.TemplateResponse("dashboard.html", data)

# =========================
# PRODUCTS
# =========================
@router.get("/products", response_class=HTMLResponse)
def products_page(request: Request, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    require_feature(db, user.store_id, "core_products")
    q = db.query(Product).filter(Product.store_id == user.store_id).order_by(Product.id.desc()).all()
    data = ctx(request, db, user)
    data.update({"products": q, "kicker":"Catálogo", "page_title":"Produtos", "title":"Produtos"})
    return templates.TemplateResponse("products.html", data)

@router.post("/products/create")
def products_create(
    request: Request,
    name: str = Form(...),
    sku: str = Form(""),
    price: float = Form(0.0),
    stock: int = Form(0),
    user: SimpleUser = Depends(require_auth),
    db: Session = Depends(get_db),
):
    require_feature(db, user.store_id, "core_products")
    p = Product(store_id=user.store_id, name=name.strip(), sku=(sku.strip() or None), price=float(price or 0), stock=int(stock or 0))
    db.add(p)
    db.commit()
    return RedirectResponse("/products", status_code=302)

@router.post("/products/{pid}/delete")
def products_delete(pid: int, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    require_feature(db, user.store_id, "core_products")
    p = db.query(Product).filter(Product.id == pid, Product.store_id == user.store_id).first()
    if p:
        db.delete(p)
        db.commit()
    return RedirectResponse("/products", status_code=302)

# =========================
# CUSTOMERS
# =========================
@router.get("/customers", response_class=HTMLResponse)
def customers_page(request: Request, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    require_feature(db, user.store_id, "core_customers")
    q = db.query(Customer).filter(Customer.store_id == user.store_id).order_by(Customer.id.desc()).all()
    data = ctx(request, db, user)
    data.update({"customers": q, "kicker":"Cadastro", "page_title":"Clientes", "title":"Clientes"})
    return templates.TemplateResponse("customers.html", data)

@router.post("/customers/create")
def customers_create(
    request: Request,
    name: str = Form(...),
    phone: str = Form(""),
    address: str = Form(""),
    user: SimpleUser = Depends(require_auth),
    db: Session = Depends(get_db),
):
    require_feature(db, user.store_id, "core_customers")
    c = Customer(store_id=user.store_id, name=name.strip(), phone=(phone.strip() or None), address=(address.strip() or None))
    db.add(c)
    db.commit()
    return RedirectResponse("/customers", status_code=302)

@router.post("/customers/{cid}/delete")
def customers_delete(cid: int, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    require_feature(db, user.store_id, "core_customers")
    c = db.query(Customer).filter(Customer.id == cid, Customer.store_id == user.store_id).first()
    if c:
        db.delete(c)
        db.commit()
    return RedirectResponse("/customers", status_code=302)

# =========================
# SALES
# =========================
@router.get("/sales", response_class=HTMLResponse)
def sales_page(request: Request, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    require_feature(db, user.store_id, "core_sales")
    sales = db.query(Sale).filter(Sale.store_id == user.store_id).order_by(Sale.id.desc()).limit(200).all()
    data = ctx(request, db, user)
    data.update({"sales": sales, "kicker":"Histórico", "page_title":"Vendas", "title":"Vendas"})
    return templates.TemplateResponse("sales.html", data)

@router.get("/sales/new", response_class=HTMLResponse)
def sale_new_page(request: Request, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    require_feature(db, user.store_id, "core_sales")
    products = db.query(Product).filter(Product.store_id == user.store_id).order_by(Product.name.asc()).all()
    data = ctx(request, db, user)
    data.update({"products": products, "kicker":"Venda", "page_title":"Nova venda", "title":"Nova venda"})
    return templates.TemplateResponse("sale_new.html", data)

@router.post("/sales/new")
def sale_new_action(
    request: Request,
    customer_name: str = Form(""),
    product_ids: List[int] = Form([]),
    qtys: List[int] = Form([]),
    user: SimpleUser = Depends(require_auth),
    db: Session = Depends(get_db),
):
    require_feature(db, user.store_id, "core_sales")
    if not product_ids:
        return RedirectResponse("/sales/new", status_code=302)

    total = 0.0
    items: list[SaleItem] = []
    for pid, q in zip(product_ids, qtys):
        q = int(q or 1)
        p = db.query(Product).filter(Product.id == int(pid), Product.store_id == user.store_id).first()
        if not p:
            continue
        if p.stock < q:
            raise HTTPException(status_code=400, detail=f"Estoque insuficiente para {p.name}")
        line_total = float(p.price) * q
        total += line_total
        items.append(SaleItem(product_name=p.name, qty=q, price=float(p.price), line_total=line_total))
        p.stock -= q

    sale = Sale(store_id=user.store_id, customer_name=(customer_name.strip() or None), total=total, status="concluida")
    db.add(sale)
    db.commit()
    db.refresh(sale)

    for it in items:
        it.sale_id = sale.id
        db.add(it)
    db.commit()

    return RedirectResponse("/sales", status_code=302)

# =========================
# ORDERS (Delivery/Depósito)
# =========================
@router.get("/orders", response_class=HTMLResponse)
def orders_page(request: Request, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    require_feature(db, user.store_id, "segment_orders")
    orders = db.query(Order).filter(Order.store_id == user.store_id).order_by(Order.id.desc()).limit(200).all()
    data = ctx(request, db, user)
    data.update({"orders": orders, "kicker":"Pedidos", "page_title":"Pedidos", "title":"Pedidos"})
    return templates.TemplateResponse("orders.html", data)

@router.get("/orders/new", response_class=HTMLResponse)
def order_new_page(request: Request, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    require_feature(db, user.store_id, "segment_orders")
    products = db.query(Product).filter(Product.store_id == user.store_id).order_by(Product.name.asc()).all()
    data = ctx(request, db, user)
    data.update({"products": products, "kicker":"Pedido", "page_title":"Novo pedido", "title":"Novo pedido"})
    return templates.TemplateResponse("order_new.html", data)

@router.get("/orders/create")
def orders_create_redirect():
    return RedirectResponse("/orders/new", status_code=302)

@router.post("/orders/create")
@router.post("/orders/new")
def order_new_action(
    request: Request,
    customer_name: str = Form(""),
    status: str = Form("novo"),
    product_ids: List[int] = Form([]),
    qtys: List[int] = Form([]),
    user: SimpleUser = Depends(require_auth),
    db: Session = Depends(get_db),
):
    require_feature(db, user.store_id, "segment_orders")
    total = 0.0
    items: list[OrderItem] = []

    for pid, q in zip(product_ids, qtys):
        q = int(q or 1)
        p = db.query(Product).filter(Product.id == int(pid), Product.store_id == user.store_id).first()
        if not p:
            continue
        if p.stock < q:
            raise HTTPException(status_code=400, detail=f"Estoque insuficiente para {p.name}")
        line_total = float(p.price) * q
        total += line_total
        items.append(OrderItem(product_name=p.name, qty=q, price=float(p.price), line_total=line_total))
        p.stock -= q

    order = Order(store_id=user.store_id, customer_name=(customer_name.strip() or None), status=status, total=total)
    db.add(order)
    db.commit()
    db.refresh(order)

    for it in items:
        it.order_id = order.id
        db.add(it)
    db.commit()

    return RedirectResponse("/orders", status_code=302)

@router.post("/orders/{oid}/status")
def order_update_status(
    oid: int,
    status: str = Form(...),
    user: SimpleUser = Depends(require_auth),
    db: Session = Depends(get_db),
):
    require_feature(db, user.store_id, "segment_orders")
    order = db.query(Order).filter(Order.id == oid, Order.store_id == user.store_id).first()
    if not order:
        return RedirectResponse("/orders", status_code=302)
    order.status = status
    db.commit()
    return RedirectResponse("/orders", status_code=302)

# =========================
# SETTINGS (Premium)
# =========================
@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    # Only admins
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Apenas admin.")
    store = get_current_store(db, user)
    data = ctx(request, db, user)
    data.update({"kicker":"Admin", "page_title":"Configurações", "title":"Configurações"})
    return templates.TemplateResponse("settings.html", data)

@router.post("/settings/branding")
def settings_branding(
    request: Request,
    product_name: str = Form(...),
    primary_color: str = Form("#2f6bff"),
    secondary_color: str = Form("#9a7bff"),
    whatsapp_support: str = Form(""),
    user: SimpleUser = Depends(require_auth),
    db: Session = Depends(get_db),
):
    # white label feature
    require_feature(db, user.store_id, "white_label")
    store = get_current_store(db, user)
    if not store.branding:
        store.branding = StoreBranding()
    store.branding.product_name = product_name.strip()[:80]
    store.branding.primary_color = primary_color.strip()[:30]
    store.branding.secondary_color = secondary_color.strip()[:30]
    store.branding.whatsapp_support = whatsapp_support.strip()[:40] or None
    db.commit()
    return RedirectResponse("/settings", status_code=302)
