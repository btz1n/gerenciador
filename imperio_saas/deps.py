
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import func

from .db import SessionLocal
from .models import Store, User, StoreFeature

@dataclass
class SimpleUser:
    id: int
    store_id: int
    username: str
    store_name: str
    role: str
    segment: str
    plan: str

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_store(db: Session, store_id: int) -> Store | None:
    return db.query(Store).filter(Store.id == store_id).first()

def is_subscription_ok(store: Store) -> tuple[bool, str]:
    # IMPORTANT:
    # SQLite/Postgres may return offset-naive datetimes depending on driver/config.
    # Normalize both values to offset-aware UTC before comparing to avoid:
    # TypeError: can't compare offset-naive and offset-aware datetimes
    now = datetime.now(timezone.utc)
    paid_until = store.paid_until
    if paid_until is not None:
        if paid_until.tzinfo is None:
            paid_until = paid_until.replace(tzinfo=timezone.utc)
        else:
            paid_until = paid_until.astimezone(timezone.utc)
    if store.subscription_status in ("suspended",):
        return False, "Sua assinatura está suspensa."
    if store.subscription_status in ("active", "trial"):
        if paid_until and paid_until < now and store.subscription_status != "active":
            # trial ended
            return False, "Seu teste grátis terminou. Ative sua assinatura para continuar."
        if paid_until and paid_until < now and store.subscription_status == "active":
            return False, "Assinatura vencida. Renove para continuar."
        return True, ""
    if store.subscription_status == "past_due":
        return False, "Pagamento pendente. Regularize para continuar."
    return False, "Acesso bloqueado."

def require_feature(db: Session, store_id: int, key: str):
    f = db.query(StoreFeature).filter(StoreFeature.store_id == store_id, StoreFeature.key == key).first()
    if not f or int(f.enabled) != 1:
        raise HTTPException(status_code=403, detail="Disponível apenas no plano superior.")

def require_auth(request: Request, db: Session = Depends(get_db)) -> SimpleUser:
    user_id = request.cookies.get("user_id")
    store_id = request.cookies.get("store_id")
    if not user_id or not store_id:
        raise HTTPException(status_code=401)

    u = db.query(User).filter(User.id == int(user_id), User.store_id == int(store_id)).first()
    if not u:
        raise HTTPException(status_code=401)

    s = get_store(db, u.store_id)
    if not s:
        raise HTTPException(status_code=401)

    ok, msg = is_subscription_ok(s)
    if not ok:
        # 402: Payment Required (we'll catch and redirect to billing)
        raise HTTPException(status_code=402, detail=msg)

    return SimpleUser(id=u.id, store_id=u.store_id, username=u.username, store_name=s.name, role=u.role, segment=s.segment, plan=s.plan)
