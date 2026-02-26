
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional, List
from urllib.parse import quote
import csv
import io

from fastapi import APIRouter, Request, Depends, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from sqlalchemy import func

from starlette.templating import Jinja2Templates

from .db import engine
from .deps import get_db, require_auth, SimpleUser, require_feature
from .models import Store, User, Product, Customer, Sale, SaleItem, Order, OrderItem, StoreBranding, StoreFeature, BarTab, BarTabItem
from .security import hash_password, verify_password
from .migrations import seed_store_defaults

import os
import base64

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(PROJECT_DIR, "templates")

def is_master(request: Request) -> bool:
    # Master access to manage subscriptions (only you)
    try:
        return request.cookies.get("imperio_master") == "1"
    except Exception:
        return False

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


# =========================
# MASTER PORTAL (only you)
# =========================
@router.get("/imperio-admin/login", response_class=HTMLResponse)
def master_login_page(request: Request):
    # simple login screen for master
    return templates.TemplateResponse("master_login.html", {"request": request})

@router.post("/imperio-admin/login")
def master_login(password: str = Form(""), request: Request = None):
    key = os.getenv("IMPERIO_MASTER_KEY", "").strip()
    if not key or password.strip() != key:
        resp = RedirectResponse("/imperio-admin/login?err=1", status_code=302)
        return resp
    resp = RedirectResponse("/imperio-admin", status_code=302)
    resp.set_cookie("imperio_master", "1", httponly=True, samesite="lax")
    return resp

@router.get("/imperio-admin/logout")
def master_logout():
    resp = RedirectResponse("/", status_code=302)
    resp.delete_cookie("imperio_master")
    return resp

@router.get("/imperio-admin", response_class=HTMLResponse)
def master_portal(request: Request, db: Session = Depends(get_db)):
    if not is_master(request):
        return RedirectResponse("/imperio-admin/login", status_code=302)
    stores = db.query(Store).order_by(Store.id.desc()).limit(200).all()
    return templates.TemplateResponse("master_portal.html", {"request": request, "stores": stores, "PLAN_PRICES": PLAN_PRICES})

@router.post("/imperio-admin/plan")
def master_set_plan(
    store_id: int = Form(...),
    plan: str = Form(...),
    subscription_status: str = Form("active"),
    paid_until: str = Form(""),
    db: Session = Depends(get_db),
    request: Request = None,
):
    if not is_master(request):
        raise HTTPException(status_code=403, detail="Acesso restrito.")
    store = db.query(Store).filter(Store.id == store_id).first()
    if not store:
        return RedirectResponse("/imperio-admin?err=store", status_code=302)
    p = (plan or "").strip().lower()
    if p not in ("basic","pro","elite"):
        p = "basic"
    store.plan = p
    st = (subscription_status or "active").strip().lower()
    if st not in ("trial","active","past_due","suspended"):
        st = "active"
    store.subscription_status = st
    if paid_until:
        try:
            dt = datetime.fromisoformat(paid_until)
            store.paid_until = dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    # ensure feature rows exist
    ensure_default_features(db, store)
    # apply bundle
    bundle = {
        "basic": {"reports_export": 0, "finance_module": 0, "multi_user": 0, "white_label": 0, "theme_custom": 0},
        "pro":   {"reports_export": 1, "finance_module": 1, "multi_user": 1, "white_label": 0, "theme_custom": 1},
        "elite": {"reports_export": 1, "finance_module": 1, "multi_user": 1, "white_label": 1, "theme_custom": 1},
    }[p]
    for f in store.features:
        if f.key in bundle:
            f.enabled = bundle[f.key]
    db.commit()
    return RedirectResponse("/imperio-admin?ok=1", status_code=302)


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



PLAN_PRICES = {'basic': 89, 'pro': 157, 'elite': 197}

FEATURE_META = {
    "core_dashboard": {"label": "Dashboard", "desc": "Visão geral do dia/mês, pedidos e indicadores."},
    "core_products": {"label": "Produtos e Estoque", "desc": "Cadastro de produtos, preço, SKU e controle de estoque."},
    "core_sales": {"label": "Vendas (PDV)", "desc": "Vendas rápidas com cálculo automático e baixa no estoque."},
    "core_customers": {"label": "Clientes", "desc": "Cadastro de clientes para vendas e pedidos."},
    "segment_orders": {"label": "Pedidos (Delivery/Depósito)", "desc": "Pedidos com status, finalizados e histórico."},
    "segment_tables": {"label": "Mesas e Comandas (Bar)", "desc": "Comandas por mesa, consumo e fechamento em venda."},
    "reports_export": {"label": "Exportação (CSV)", "desc": "Exportar vendas, pedidos e produtos para Excel (CSV)."},
    "finance_module": {"label": "Financeiro", "desc": "Recursos de caixa/financeiro (quando habilitado)."},
    "multi_user": {"label": "Múltiplos usuários", "desc": "Crie mais usuários e permissões (quando habilitado)."},
    "theme_custom": {"label": "Tema (claro/escuro e fundo)", "desc": "Escolha tema claro/escuro e cor de fundo.", "plan": "pro"},
    "white_label": {"label": "Personalização de marca", "desc": "Trocar nome, cores e logo do sistema (plano completo)."},
}


# =========================
# PUBLIC
# =========================
@router.get("/", include_in_schema=False)
def root(request: Request):
    # If already logged in, go straight to dashboard
    if request.cookies.get("user_id") and request.cookies.get("store_id"):
        return RedirectResponse("/dashboard", status_code=302)
    return RedirectResponse("/login", status_code=302)

def _alloc_number(db: Session, store_id: int, kind: str) -> str:
    """Allocate a sequential human-friendly number per store.
    kind: 'P' (pedido), 'V' (venda), 'C' (comanda)
    """
    store = db.query(Store).filter(Store.id == store_id).with_for_update().first()
    if not store:
        raise HTTPException(status_code=400, detail="Loja inválida.")
    if kind == "P":
        seq = int(store.next_order_seq or 1)
        store.next_order_seq = seq + 1
        number = f"P-{seq:06d}"
    elif kind == "V":
        seq = int(store.next_sale_seq or 1)
        store.next_sale_seq = seq + 1
        number = f"V-{seq:06d}"
    else:  # "C"
        seq = int(getattr(store, "next_tab_seq", 1) or 1)
        store.next_tab_seq = seq + 1
        number = f"C-{seq:06d}"
    db.add(store)
    return number

def convert_order_to_sale(db: Session, order: Order) -> Sale:
    """Convert an order to a sale (idempotent). Does NOT change stock (reserved on order creation)."""
    if order.converted_sale_id:
        sale = db.query(Sale).filter(Sale.id == order.converted_sale_id, Sale.store_id == order.store_id).first()
        if sale:
            return sale

    items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
    sale = Sale(
        store_id=order.store_id,
        customer_name=order.customer_name,
        total=order.total,
        status="concluida",
    )
    sale.number = _alloc_number(db, order.store_id, "V")
    db.add(sale)
    db.flush()  # get sale.id

    for it in items:
        db.add(SaleItem(
            sale_id=sale.id,
            product_name=it.product_name,
            qty=it.qty,
            price=it.price,
            line_total=it.line_total,
        ))

    order.converted_sale_id = sale.id
    db.add(order)
    return sale

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
@router.get("/setup", response_class=HTMLResponse)
@router.get("/admin/setup", response_class=HTMLResponse)
def admin_setup_page(request: Request):
    return templates.TemplateResponse("setup.html", {"request": request, "user": None, "title": "Criar Loja"})

@router.post("/setup")
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

    store = Store(name=store_name, segment=segment, plan="basic", subscription_status="trial")
    store.ensure_trial()
    store.branding = StoreBranding(product_name="IMPÉRIO", whatsapp_support=os.getenv("SUPPORT_WHATSAPP"))

    # Default features (per segment/plan)
    base_feats = {
        "core_products": 1,
        "core_sales": 1,
        "core_customers": 1,
        "core_dashboard": 1,
        "segment_orders": 1 if segment in ("deposito","delivery") else 0,
        "segment_tables": 1 if segment == "bar" else 0,
        "reports_export": 0,
        "finance_module": 0,
        "multi_user": 0,
        "white_label": 0,
    }
    for k,v in base_feats.items():
        store.features.append(StoreFeature(key=k, enabled=v))

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
    price_start = os.getenv("PRICE_START", "89,00")
    price_pro = os.getenv("PRICE_PRO", "157,00")
    price_elite = os.getenv("PRICE_ELITE", "197,00")
    support = os.getenv("SUPPORT_WHATSAPP", "")
    message = "Faça o PIX e envie o comprovante no WhatsApp para liberar/renovar sua assinatura."
    data = ctx(request, db, user)
    data["is_master"] = is_master(request)
    data.update({"title":"Assinatura", "kicker":"Assinatura", "page_title":"Ativar assinatura", "pix_key":pix, "prices":{"start":price_start,"pro":price_pro,"elite":price_elite}, "support":support, "message":message, "store_obj":store})
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

    delivered_orders_today = db.query(func.count(Order.id)).filter(
        Order.store_id == user.store_id,
        Order.status == 'entregue',
        func.date(Order.created_at) == str(today)
    ).scalar() or 0

    open_tabs = db.query(func.count(BarTab.id)).filter(
        BarTab.store_id == user.store_id,
        BarTab.status == 'aberta'
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
        "delivered_orders_today": int(delivered_orders_today),
        "open_tabs": int(open_tabs),
        "low_stock": int(low_stock),
        "ticket_avg": float(ticket_avg),
    }

    data = ctx(request, db, user)
    data["is_master"] = is_master(request)
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
    data["is_master"] = is_master(request)
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
    data["is_master"] = is_master(request)
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
    data["is_master"] = is_master(request)
    data.update({"sales": sales, "kicker":"Histórico", "page_title":"Vendas", "title":"Vendas"})
    return templates.TemplateResponse("sales.html", data)

@router.get("/export/sales.csv")
def export_sales_csv(
    request: Request,
    user: SimpleUser = Depends(require_auth),
    db: Session = Depends(get_db),
):
    require_feature(db, user.store_id, "reports_export")
    # optional filters
    date_from = request.query_params.get("from")
    date_to = request.query_params.get("to")
    q = db.query(Sale).filter(Sale.store_id == user.store_id)
    if date_from:
        q = q.filter(Sale.created_at >= date_from)
    if date_to:
        q = q.filter(Sale.created_at <= date_to)
    rows = q.order_by(Sale.id.desc()).limit(5000).all()

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["numero", "data", "cliente", "total", "status"])
    for s in rows:
        w.writerow([s.number or s.id, (s.created_at.isoformat() if s.created_at else ""), s.customer_name or "", f"{s.total:.2f}", s.status])
    data = out.getvalue().encode("utf-8-sig")
    return Response(content=data, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=vendas.csv"})

@router.get("/export/orders.csv")
def export_orders_csv(
    request: Request,
    user: SimpleUser = Depends(require_auth),
    db: Session = Depends(get_db),
):
    require_feature(db, user.store_id, "reports_export")
    date_from = request.query_params.get("from")
    date_to = request.query_params.get("to")
    status = (request.query_params.get("status") or "").strip().lower()
    q = db.query(Order).filter(Order.store_id == user.store_id)
    if status:
        q = q.filter(Order.status == status)
    if date_from:
        q = q.filter(Order.created_at >= date_from)
    if date_to:
        q = q.filter(Order.created_at <= date_to)
    rows = q.order_by(Order.id.desc()).limit(5000).all()

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["numero", "data", "cliente", "status", "total", "venda_id"])
    for o in rows:
        w.writerow([o.number or o.id, (o.created_at.isoformat() if o.created_at else ""), o.customer_name or "", o.status, f"{o.total:.2f}", o.converted_sale_id or ""])
    data = out.getvalue().encode("utf-8-sig")
    return Response(content=data, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=pedidos.csv"})

@router.get("/export/products.csv")
def export_products_csv(
    request: Request,
    user: SimpleUser = Depends(require_auth),
    db: Session = Depends(get_db),
):
    require_feature(db, user.store_id, "reports_export")
    rows = db.query(Product).filter(Product.store_id == user.store_id).order_by(Product.name.asc()).all()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["nome", "sku", "estoque", "preco"])
    for p in rows:
        w.writerow([p.name, p.sku or "", p.stock, f"{p.price:.2f}"])
    data = out.getvalue().encode("utf-8-sig")
    return Response(content=data, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=produtos.csv"})


@router.get("/sales/new", response_class=HTMLResponse)
def sale_new_page(request: Request, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    require_feature(db, user.store_id, "core_sales")
    products = db.query(Product).filter(Product.store_id == user.store_id).order_by(Product.name.asc()).all()
    customers = db.query(Customer).filter(Customer.store_id == user.store_id).order_by(Customer.name.asc()).all()
    data = ctx(request, db, user)
    data["is_master"] = is_master(request)
    data.update({"products": products, "customers": customers, "kicker":"Venda", "page_title":"Nova venda", "title":"Nova venda"})
    return templates.TemplateResponse("sale_new.html", data)

# Backwards-compat: some frontends use /sales/create
@router.get("/sales/create")
def sale_create_redirect():
    return RedirectResponse("/sales/new", status_code=302)

@router.post("/sales/create")
def sale_create_action(
    request: Request,
    customer_name: str = Form(""),
    product_ids: List[str] = Form([]),
    qtys: List[int] = Form([]),
    user: SimpleUser = Depends(require_auth),
    db: Session = Depends(get_db),
):
    return sale_new_action(request, customer_name, product_ids, qtys, user, db)


@router.post("/sales/new")
def sale_new_action(
    request: Request,
    customer_name: str = Form(""),
    product_ids: List[str] = Form([]),
    qtys: List[int] = Form([]),
    user: SimpleUser = Depends(require_auth),
    db: Session = Depends(get_db),
):
    require_feature(db, user.store_id, "core_sales")
    if not product_ids:
        return RedirectResponse("/sales/new", status_code=302)

    total = 0.0
    items: list[SaleItem] = []

    # product_ids can contain blanks from the form; filter safely
    n = min(len(product_ids), len(qtys))
    for i in range(n):
        pid_raw = (product_ids[i] or "").strip()
        if not pid_raw:
            continue
        try:
            pid = int(pid_raw)
        except ValueError:
            continue

        try:
            q = int(qtys[i] or 1)
        except ValueError:
            q = 1
        if q < 1:
            q = 1

        p = db.query(Product).filter(Product.id == pid, Product.store_id == user.store_id).first()
        if not p:
            continue
        if p.stock < q:
            return RedirectResponse("/sales/new?err=" + quote(f"Estoque insuficiente para {p.name}"), status_code=302)
        line_total = float(p.price) * q
        total += line_total
        items.append(SaleItem(product_name=p.name, qty=q, price=float(p.price), line_total=line_total))
        p.stock -= q

    sale = Sale(store_id=user.store_id, customer_name=(customer_name.strip() or None), total=total, status="concluida")
    sale.number = _alloc_number(db, user.store_id, "V")
    db.add(sale)
    db.commit()
    db.refresh(sale)

    for it in items:
        it.sale_id = sale.id
        db.add(it)
    db.commit()

    return RedirectResponse("/sales?ok=venda_salva", status_code=302)

# =========================
# ORDERS (Delivery/Depósito)
# =========================
@router.get("/orders", response_class=HTMLResponse)
def orders_page(request: Request, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    require_feature(db, user.store_id, "segment_orders")

    tab = (request.query_params.get("tab") or "ativos").lower()
    q = db.query(Order).filter(Order.store_id == user.store_id)

    # Tabs
    if tab == "finalizados":
        q = q.filter(Order.status.in_(["entregue", "cancelado"]))
    elif tab == "historico":
        # optional filters
        date_from = request.query_params.get("from")
        date_to = request.query_params.get("to")
        status = (request.query_params.get("status") or "").strip()
        if status:
            q = q.filter(Order.status == status)
        if date_from:
            q = q.filter(Order.created_at >= date_from)
        if date_to:
            q = q.filter(Order.created_at <= date_to)
    else:
        # ativos
        q = q.filter(Order.status.in_(["novo", "preparo", "saiu"]))

    orders = q.order_by(Order.id.desc()).limit(300).all()
    data = ctx(request, db, user)
    data["is_master"] = is_master(request)
    data.update({"orders": orders, "tab": tab, "kicker":"Pedidos", "page_title":"Pedidos", "title":"Pedidos"})
    return templates.TemplateResponse("orders.html", data)


@router.get("/orders/new", response_class=HTMLResponse)
def order_new_page(request: Request, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    require_feature(db, user.store_id, "segment_orders")
    products = db.query(Product).filter(Product.store_id == user.store_id).order_by(Product.name.asc()).all()
    data = ctx(request, db, user)
    data["is_master"] = is_master(request)
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
    product_ids: List[str] = Form([]),
    qtys: List[int] = Form([]),
    user: SimpleUser = Depends(require_auth),
    db: Session = Depends(get_db),
):
    require_feature(db, user.store_id, "segment_orders")
    total = 0.0
    items: list[OrderItem] = []

    n = min(len(product_ids), len(qtys))
    for i in range(n):
        pid_raw = (product_ids[i] or "").strip()
        if not pid_raw:
            continue
        try:
            pid = int(pid_raw)
        except ValueError:
            continue

        try:
            q = int(qtys[i] or 1)
        except ValueError:
            q = 1
        if q < 1:
            q = 1

        p = db.query(Product).filter(Product.id == pid, Product.store_id == user.store_id).first()
        if not p:
            continue
        if p.stock < q:
            return RedirectResponse("/orders/new?err=" + quote(f"Estoque insuficiente para {p.name}"), status_code=302)
        line_total = float(p.price) * q
        total += line_total
        items.append(OrderItem(product_name=p.name, qty=q, price=float(p.price), line_total=line_total))
        p.stock -= q

    order = Order(store_id=user.store_id, customer_name=(customer_name.strip() or None), status=status, total=total)
    order.number = _alloc_number(db, user.store_id, "P")
    db.add(order)
    db.commit()
    db.refresh(order)

    for it in items:
        it.order_id = order.id
        db.add(it)
    db.commit()

    return RedirectResponse("/orders?tab=ativos&ok=pedido_salvo", status_code=302)

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
        return RedirectResponse("/orders?tab=ativos", status_code=302)

    status = (status or "").strip().lower()
    order.status = status
    # Convert to sale when delivered
    if status == "entregue":
        convert_order_to_sale(db, order)
    db.commit()

    if status in ("entregue", "cancelado"):
        return RedirectResponse("/orders?tab=finalizados&ok=status", status_code=302)
    return RedirectResponse("/orders?tab=ativos&ok=status", status_code=302)

    order.status = status
    db.commit()
    return RedirectResponse("/orders", status_code=302)


# =========================
# BAR: COMANDAS / MESAS
# =========================
@router.get("/tabs", response_class=HTMLResponse)
def tabs_page(request: Request, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    require_feature(db, user.store_id, "segment_tables")
    tab = (request.query_params.get("tab") or "abertas").lower()
    q = db.query(BarTab).filter(BarTab.store_id == user.store_id)
    if tab == "fechadas":
        q = q.filter(BarTab.status == "fechada")
    else:
        q = q.filter(BarTab.status == "aberta")
    tabs = q.order_by(BarTab.id.desc()).limit(300).all()
    data = ctx(request, db, user)
    data["is_master"] = is_master(request)
    data.update({"tabs": tabs, "tab": tab, "kicker":"Bar", "page_title":"Comandas", "title":"Comandas"})
    return templates.TemplateResponse("tabs.html", data)

@router.get("/tabs/new", response_class=HTMLResponse)
def tab_new_page(request: Request, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    require_feature(db, user.store_id, "segment_tables")
    data = ctx(request, db, user)
    data["is_master"] = is_master(request)
    data.update({"kicker":"Bar", "page_title":"Nova comanda", "title":"Nova comanda"})
    return templates.TemplateResponse("tab_new.html", data)

@router.post("/tabs/new")
def tab_new_action(
    table_name: str = Form("Mesa"),
    user: SimpleUser = Depends(require_auth),
    db: Session = Depends(get_db),
):
    require_feature(db, user.store_id, "segment_tables")
    t = BarTab(store_id=user.store_id, table_name=(table_name.strip() or "Mesa"), status="aberta", total=0.0)
    t.number = _alloc_number(db, user.store_id, "C")
    db.add(t)
    db.commit()
    return RedirectResponse(f"/tabs/{t.id}", status_code=302)

@router.get("/tabs/{tid}", response_class=HTMLResponse)
def tab_detail(request: Request, tid: int, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    require_feature(db, user.store_id, "segment_tables")
    t = db.query(BarTab).filter(BarTab.id == tid, BarTab.store_id == user.store_id).first()
    if not t:
        return RedirectResponse("/tabs", status_code=302)
    items = db.query(BarTabItem).filter(BarTabItem.tab_id == t.id).all()
    products = db.query(Product).filter(Product.store_id == user.store_id).order_by(Product.name.asc()).all()
    data = ctx(request, db, user)
    data["is_master"] = is_master(request)
    data.update({"tabobj": t, "items": items, "products": products, "kicker":"Bar", "page_title":"Comanda", "title":"Comanda"})
    return templates.TemplateResponse("tab_detail.html", data)

@router.post("/tabs/{tid}/add")
def tab_add_item(
    tid: int,
    product_id: int = Form(...),
    qty: int = Form(1),
    user: SimpleUser = Depends(require_auth),
    db: Session = Depends(get_db),
):
    require_feature(db, user.store_id, "segment_tables")
    t = db.query(BarTab).filter(BarTab.id == tid, BarTab.store_id == user.store_id).first()
    if not t or t.status != "aberta":
        return RedirectResponse("/tabs", status_code=302)
    p = db.query(Product).filter(Product.id == product_id, Product.store_id == user.store_id).first()
    if not p:
        return RedirectResponse(f"/tabs/{tid}?err=produto", status_code=302)
    q = max(int(qty or 1), 1)
    if p.stock < q:
        return RedirectResponse(f"/tabs/{tid}?err=estoque", status_code=302)
    line_total = float(p.price) * q
    db.add(BarTabItem(tab_id=t.id, product_name=p.name, qty=q, price=float(p.price), line_total=line_total))
    p.stock -= q
    t.total = float(t.total) + line_total
    db.commit()
    return RedirectResponse(f"/tabs/{tid}?ok=add", status_code=302)

@router.post("/tabs/{tid}/close")
def tab_close(
    tid: int,
    user: SimpleUser = Depends(require_auth),
    db: Session = Depends(get_db),
):
    require_feature(db, user.store_id, "segment_tables")
    t = db.query(BarTab).filter(BarTab.id == tid, BarTab.store_id == user.store_id).first()
    if not t or t.status != "aberta":
        return RedirectResponse("/tabs", status_code=302)
    # convert to sale
    if not t.converted_sale_id:
        items = db.query(BarTabItem).filter(BarTabItem.tab_id == t.id).all()
        sale = Sale(store_id=t.store_id, customer_name=t.table_name, total=t.total, status="concluida")
        sale.number = _alloc_number(db, t.store_id, "V")
        db.add(sale)
        db.flush()
        for it in items:
            db.add(SaleItem(sale_id=sale.id, product_name=it.product_name, qty=it.qty, price=it.price, line_total=it.line_total))
        t.converted_sale_id = sale.id
    t.status = "fechada"
    t.closed_at = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse("/tabs?tab=fechadas&ok=close", status_code=302)


# =========================
# SETTINGS (Premium)
# =========================
@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, user: SimpleUser = Depends(require_auth), db: Session = Depends(get_db)):
    # Only admins
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Apenas admin.")
    if not is_master(request):
        raise HTTPException(status_code=403, detail="Acesso restrito ao suporte.")
    store = get_current_store(db, user)
    data = ctx(request, db, user)
    data["is_master"] = is_master(request)
    data.update({"kicker":"Admin", "page_title":"Configurações", "title":"Configurações"})
    data["feature_meta"] = FEATURE_META
    return templates.TemplateResponse("settings.html", data)

@router.post("/settings/branding")
def settings_branding(
    request: Request,
    product_name: str = Form(...),
    primary_color: str = Form("#2f6bff"),
    secondary_color: str = Form("#9a7bff"),
    theme_mode: str = Form("dark"),
    bg_color: str = Form("#0b0f14"),
    whatsapp_support: str = Form(""),
    logo_file: UploadFile | None = File(None),
    user: SimpleUser = Depends(require_auth),
    db: Session = Depends(get_db),
):
    store = get_current_store(db, user)
    # Permissions by plan/features
    can_white = has_feature(store, "white_label")
    can_theme = has_feature(store, "theme_custom")
    if not store.branding:
        store.branding = StoreBranding()
    if can_white:
        store.branding.product_name = product_name.strip()[:80]
    store.branding.primary_color = primary_color.strip()[:30]
    store.branding.secondary_color = secondary_color.strip()[:30]
    if can_theme:
        tm = (theme_mode or 'dark').strip().lower()
        if tm not in ('dark','light'):
            tm = 'dark'
        store.branding.theme_mode = tm
        store.branding.bg_color = (bg_color or '#0b0f14').strip()[:30]
    store.branding.whatsapp_support = whatsapp_support.strip()[:40] or None
    # Optional logo upload (stored as data URL in DB)
    if can_white and logo_file is not None:
        try:
            content = logo_file.file.read()
            if content and len(content) <= 1024 * 1024:  # 1MB
                ctype = (logo_file.content_type or "image/png").split(";")[0].strip()
                if ctype.startswith("image/"):
                    b64 = base64.b64encode(content).decode("ascii")
                    store.branding.logo_url = f"data:{ctype};base64,{b64}"
        except Exception:
            pass

    db.commit()
    return RedirectResponse("/settings", status_code=302)


@router.post("/settings/segment")
def settings_segment(
    segment: str = Form(...),
    user: SimpleUser = Depends(require_auth),
    db: Session = Depends(get_db),
):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Apenas admin.")
    if not is_master(request):
        raise HTTPException(status_code=403, detail="Acesso restrito ao suporte.")
    store = get_current_store(db, user)
    seg = (segment or "").strip().lower()
    if seg not in ("deposito", "delivery", "bar"):
        seg = "deposito"
    store.segment = seg
    # auto-enable segment features based on segment
    for f in store.features:
        if f.key == "segment_orders":
            f.enabled = 1 if seg in ("deposito","delivery") else 0
        if f.key == "segment_tables":
            f.enabled = 1 if seg == "bar" else 0
    db.commit()
    return RedirectResponse("/settings?ok=segment", status_code=302)

@router.post("/settings/plan")
def settings_plan(
    plan: str = Form(...),
    subscription_status: str = Form("trial"),
    paid_until: str = Form(""),
    user: SimpleUser = Depends(require_auth),
    db: Session = Depends(get_db),
):
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Apenas admin.")
    if not is_master(request):
        raise HTTPException(status_code=403, detail="Acesso restrito ao suporte.")
    store = get_current_store(db, user)
    p = (plan or "").strip().lower()
    if p not in ("basic","pro","elite"):
        p = "basic"
    store.plan = p
    st = (subscription_status or "trial").strip().lower()
    if st not in ("trial","active","past_due","suspended"):
        st = "trial"
    store.subscription_status = st
    if paid_until:
        try:
            # date yyyy-mm-dd
            dt = datetime.fromisoformat(paid_until)
            store.paid_until = dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass

    # set feature bundles by plan
    bundle = {
        "basic": {"reports_export": 0, "finance_module": 0, "multi_user": 0, "white_label": 0, "theme_custom": 0},
        "pro":   {"reports_export": 1, "finance_module": 1, "multi_user": 1, "white_label": 0, "theme_custom": 1},
        "elite": {"reports_export": 1, "finance_module": 1, "multi_user": 1, "white_label": 1, "theme_custom": 1},
    }[p]
    for f in store.features:
        if f.key in bundle:
            f.enabled = bundle[f.key]
    db.commit()
    return RedirectResponse("/settings?ok=plan", status_code=302)

def has_feature(store: Store, key: str) -> bool:
    try:
        for f in (store.features or []):
            if f.key == key:
                return bool(int(f.enabled))
    except Exception:
        pass
    return False


