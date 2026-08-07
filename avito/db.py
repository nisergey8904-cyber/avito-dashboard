"""Хранилище: SQLite локально, любой SQLAlchemy-URL в облаке.

Адрес базы берётся из переменной окружения DASHBOARD_DB_URL. Файловая система
Streamlit Community Cloud эфемерна, поэтому при деплое туда в эту переменную
подставляется URL внешнего Postgres — код при этом не меняется.
"""

from __future__ import annotations

import importlib.util
import os
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd
from sqlalchemy import (
    Column, Date, DateTime, Float, ForeignKey, Integer, MetaData, String,
    Table, UniqueConstraint, create_engine, delete, func, insert, select,
)
from sqlalchemy.engine import Engine

DEFAULT_ACCOUNTS = ["Аккаунт 1", "Аккаунт 2", "Аккаунт 3", "Аккаунт 4"]

metadata = MetaData()

accounts = Table(
    "accounts", metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(120), nullable=False, unique=True),
    Column("is_active", Integer, nullable=False, default=1),
)

uploads = Table(
    "uploads", metadata,
    Column("id", Integer, primary_key=True),
    Column("account_id", Integer, ForeignKey("accounts.id", ondelete="CASCADE"),
           nullable=False),
    Column("period_start", Date, nullable=False),
    Column("period_end", Date, nullable=False),
    Column("filename", String(255)),
    Column("uploaded_at", DateTime, nullable=False),
    Column("rows_count", Integer, nullable=False, default=0),
    UniqueConstraint("account_id", "period_start", "period_end", name="uq_upload_period"),
)

listings = Table(
    "listings", metadata,
    Column("id", Integer, primary_key=True),
    Column("upload_id", Integer, ForeignKey("uploads.id", ondelete="CASCADE"),
           nullable=False, index=True),
    Column("region", String(120)),
    Column("city", String(120)),
    Column("address", String(255)),
    Column("category", String(120)),
    Column("subcategory", String(120)),
    Column("param", String(160)),
    Column("title", String(255)),
    Column("price", Float),
    Column("first_published", Date),
    Column("unpublished_at", Date),
    Column("days_online", Float),
    Column("employee", String(120)),
    Column("impressions", Float),
    Column("views", Float),
    Column("target_views", Float),
    Column("contacts", Float),
    Column("chat", Float),
    Column("phone", Float),
    Column("phone_and_chat", Float),
    Column("discount_replies", Float),
    Column("favorites", Float),
    Column("spend_total", Float),
    Column("bonus_spent", Float),
    Column("spend_placement", Float),
    Column("spend_promo", Float),
    Column("spend_other", Float),
)

calls = Table(
    "calls", metadata,
    Column("id", Integer, primary_key=True),
    Column("account_id", Integer, ForeignKey("accounts.id", ondelete="CASCADE"),
           nullable=False),
    Column("call_date", Date, nullable=False),
    Column("calls", Integer, nullable=False, default=0),
    Column("deals", Integer, nullable=False, default=0),
    Column("note", String(255)),
    UniqueConstraint("account_id", "call_date", name="uq_calls_day"),
)

LISTING_FIELDS = [c.name for c in listings.columns if c.name not in ("id", "upload_id")]


def _postgres_driver() -> str:
    """Первый установленный драйвер Postgres.

    Готовые сборки psycopg2 отстают от новых версий Python, поэтому драйвер не
    зашит в строку подключения, а выбирается из доступных.
    """
    for module in ("psycopg", "psycopg2", "pg8000"):
        if importlib.util.find_spec(module) is not None:
            return module
    return "psycopg"


class DatabaseConfigError(RuntimeError):
    """Строка подключения задана неверно — сообщение показывается пользователю."""


def _check_postgres_target(url: str) -> None:
    """Ловит типовые ошибки в строке подключения до попытки соединиться.

    Иначе они всплывают глубоко внутри драйвера в виде невнятных исключений
    вроде UnicodeEncodeError из кодировщика доменных имён.
    """
    parts = urlsplit(url)
    try:
        host = parts.hostname
    except ValueError as exc:
        raise DatabaseConfigError(
            f"Адрес базы не разбирается: {exc}. Скопируйте строку подключения "
            "из панели Neon заново."
        ) from exc

    if not host:
        raise DatabaseConfigError(
            "В строке подключения не указан адрес сервера базы. Скопируйте "
            "строку целиком из панели Neon: Dashboard → Connection Details."
        )
    if "..." in url or ".." in host:
        raise DatabaseConfigError(
            f"В адресе сервера («{host}») стоит многоточие — похоже, в секреты "
            "попал пример из инструкции, а не настоящая строка подключения. "
            "Возьмите её в панели Neon: Dashboard → Connection Details → "
            "Connection string."
        )
    if any(word in url for word in ("имя:пароль", "ВАШ_ЛОГИН", "user:pass")):
        raise DatabaseConfigError(
            "В строке подключения остались слова-заглушки вместо логина и "
            "пароля. Скопируйте настоящую строку из панели Neon."
        )


def normalize_url(url: str) -> str:
    """Приводит строку подключения к рабочему виду.

    Строку можно копировать из панели Neon или Supabase как есть: префикс
    «postgres://», «postgresql://» или «postgresql+psycopg2://» одинаково
    приводится к драйверу, который реально установлен.
    """
    url = url.strip()
    scheme, separator, rest = url.partition("://")
    if not separator:
        return url
    if scheme.split("+", 1)[0] in ("postgres", "postgresql"):
        url = f"postgresql+{_postgres_driver()}://{rest}"
        _check_postgres_target(url)
    return url


def database_url() -> str:
    url = os.environ.get("DASHBOARD_DB_URL", "").strip()
    if url:
        return normalize_url(url)
    path = Path(__file__).resolve().parent.parent / "data" / "stats.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path.as_posix()}"


def get_engine(url: str | None = None) -> Engine:
    url = normalize_url(url) if url else database_url()
    kwargs = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    init_db(engine)
    return engine


def init_db(engine: Engine) -> None:
    metadata.create_all(engine)
    with engine.begin() as conn:
        existing = conn.execute(select(func.count()).select_from(accounts)).scalar_one()
        if not existing:
            conn.execute(insert(accounts),
                         [{"name": n, "is_active": 1} for n in DEFAULT_ACCOUNTS])


# --- аккаунты ---------------------------------------------------------------

def list_accounts(engine: Engine) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.DataFrame(
            conn.execute(select(accounts).order_by(accounts.c.id)).mappings().all()
        )


def rename_account(engine: Engine, account_id: int, new_name: str) -> None:
    with engine.begin() as conn:
        conn.execute(accounts.update()
                     .where(accounts.c.id == account_id)
                     .values(name=new_name.strip()))


def add_account(engine: Engine, name: str) -> int:
    with engine.begin() as conn:
        result = conn.execute(insert(accounts).values(name=name.strip(), is_active=1))
        return int(result.inserted_primary_key[0])


# --- выгрузки ---------------------------------------------------------------

def save_upload(engine: Engine, account_id: int, period_start: date, period_end: date,
                filename: str, rows: pd.DataFrame) -> tuple[int, bool]:
    """Сохраняет выгрузку. Повторная загрузка того же периода заменяет прежнюю.

    Возвращает (id выгрузки, была ли замена).
    """
    payload = []
    for record in rows.to_dict("records"):
        payload.append({f: record.get(f) for f in LISTING_FIELDS})

    with engine.begin() as conn:
        previous = conn.execute(
            select(uploads.c.id).where(
                uploads.c.account_id == account_id,
                uploads.c.period_start == period_start,
                uploads.c.period_end == period_end,
            )
        ).scalar_one_or_none()
        replaced = previous is not None
        if replaced:
            conn.execute(delete(listings).where(listings.c.upload_id == previous))
            conn.execute(delete(uploads).where(uploads.c.id == previous))

        upload_id = int(conn.execute(insert(uploads).values(
            account_id=account_id,
            period_start=period_start,
            period_end=period_end,
            filename=filename,
            uploaded_at=datetime.now(timezone.utc),
            rows_count=len(payload),
        )).inserted_primary_key[0])

        if payload:
            for chunk_start in range(0, len(payload), 500):
                chunk = payload[chunk_start:chunk_start + 500]
                conn.execute(insert(listings),
                             [dict(row, upload_id=upload_id) for row in chunk])
    return upload_id, replaced


def list_uploads(engine: Engine) -> pd.DataFrame:
    query = (
        select(
            uploads.c.id, uploads.c.period_start, uploads.c.period_end,
            uploads.c.filename, uploads.c.uploaded_at, uploads.c.rows_count,
            accounts.c.name.label("account"), accounts.c.id.label("account_id"),
        )
        .select_from(uploads.join(accounts, uploads.c.account_id == accounts.c.id))
        .order_by(uploads.c.period_start.desc(), accounts.c.id)
    )
    with engine.connect() as conn:
        return pd.DataFrame(conn.execute(query).mappings().all())


def delete_upload(engine: Engine, upload_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(delete(listings).where(listings.c.upload_id == upload_id))
        conn.execute(delete(uploads).where(uploads.c.id == upload_id))


def load_listings(engine: Engine) -> pd.DataFrame:
    """Все строки объявлений с привязкой к аккаунту и периоду."""
    query = (
        select(
            listings,
            uploads.c.period_start, uploads.c.period_end,
            uploads.c.account_id, accounts.c.name.label("account"),
        )
        .select_from(
            listings
            .join(uploads, listings.c.upload_id == uploads.c.id)
            .join(accounts, uploads.c.account_id == accounts.c.id)
        )
    )
    with engine.connect() as conn:
        df = pd.DataFrame(conn.execute(query).mappings().all())
    if df.empty:
        return df
    for col in ("period_start", "period_end"):
        df[col] = pd.to_datetime(df[col])
    return df


# --- звонки -----------------------------------------------------------------

def upsert_calls(engine: Engine, account_id: int, call_date: date,
                 calls_count: int, deals: int = 0, note: str | None = None) -> None:
    with engine.begin() as conn:
        existing = conn.execute(
            select(calls.c.id).where(calls.c.account_id == account_id,
                                     calls.c.call_date == call_date)
        ).scalar_one_or_none()
        values = {"calls": int(calls_count), "deals": int(deals), "note": note}
        if existing is not None:
            conn.execute(calls.update().where(calls.c.id == existing).values(**values))
        else:
            conn.execute(insert(calls).values(
                account_id=account_id, call_date=call_date, **values))


def delete_calls(engine: Engine, row_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(delete(calls).where(calls.c.id == row_id))


def load_calls(engine: Engine) -> pd.DataFrame:
    query = (
        select(calls.c.id, calls.c.call_date, calls.c.calls, calls.c.deals,
               calls.c.note, calls.c.account_id, accounts.c.name.label("account"))
        .select_from(calls.join(accounts, calls.c.account_id == accounts.c.id))
        .order_by(calls.c.call_date.desc())
    )
    with engine.connect() as conn:
        df = pd.DataFrame(conn.execute(query).mappings().all())
    if not df.empty:
        df["call_date"] = pd.to_datetime(df["call_date"])
    return df
