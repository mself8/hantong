"""신청서 저장소. 로컬은 SQLite, Railway는 DATABASE_URL(Postgres)을 쓴다."""
from __future__ import annotations

import json
import os

from sqlalchemy import Column, MetaData, String, Table, Text, create_engine, select, update


def _url() -> str:
    url = os.getenv("DATABASE_URL", "sqlite:///hantong.db")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


engine = create_engine(_url(), future=True)
meta = MetaData()
requests = Table(
    "requests", meta,
    Column("id", String(32), primary_key=True),
    Column("created_at", String(40), index=True),
    Column("status", String(20), index=True),
    Column("data", Text, nullable=False),
)
meta.create_all(engine)


def save(req: dict) -> dict:
    with engine.begin() as conn:
        exists = conn.execute(select(requests.c.id).where(requests.c.id == req["id"])).first()
        row = {"created_at": req["created_at"], "status": req["status"], "data": json.dumps(req, ensure_ascii=False)}
        if exists:
            conn.execute(update(requests).where(requests.c.id == req["id"]).values(**row))
        else:
            conn.execute(requests.insert().values(id=req["id"], **row))
    return req


def get(rid: str) -> dict | None:
    with engine.begin() as conn:
        row = conn.execute(select(requests.c.data).where(requests.c.id == rid)).first()
    return json.loads(row[0]) if row else None


def list_all(status: str | None = None) -> list[dict]:
    q = select(requests.c.data).order_by(requests.c.created_at.desc())
    if status:
        q = q.where(requests.c.status == status)
    with engine.begin() as conn:
        return [json.loads(r[0]) for r in conn.execute(q)]
