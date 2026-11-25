"""Хранилище: SQLite локально, любой SQLAlchemy-URL в облаке.

Адрес базы берётся из переменной окружения DASHBOARD_DB_URL. Файловая система
Streamlit Community Cloud эфемерна, поэтому при деплое туда в эту переменную
подставляется URL внешнего Postgres — код при этом не меняется.

Данные хранятся в одной таблице daily_stats: строка выгрузки + дата дня +
аккаунт. Дашборд фильтрует её по интервалу дат, поэтому отдельного понятия
«выгрузка за период» в модели нет.
"""

from __future__ import annotations

import importlib.util
import math
import os
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd
from sqlalchemy import (
    Column, Date, DateTime, Float, ForeignKey, Index, Integer, MetaData,
    String, Table, UniqueConstraint, create_engine, delete, func, insert,
    inspect, select, text,
)
from sqlalchemy.engine import Engine

DEFAULT_ACCOUNTS = ["Аккаунт 1", "Аккаунт 2", "Аккаунт 3", "Аккаунт 4"]

# Таблицы недельной модели: строки в них привязаны к периоду, а не к дню, и
# разложить их обратно по дням нельзя — при обновлении схемы они удаляются.
LEGACY_TABLES = ("listings", "uploads")

metadata = MetaData()

accounts = Table(
    "accounts", metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String(120), nullable=False, unique=True),
    # ID кабинета Авито: по нему импорт находит аккаунт, даже если название
    # поменяли. Пустой — аккаунт заведён вручную и файлы к нему выбираются сами.
    Column("avito_id", String(40)),
    Column("is_active", Integer, nullable=False, default=1),
)

daily_stats = Table(
    "daily_stats", metadata,
    Column("id", Integer, primary_key=True),
    Column("account_id", Integer, ForeignKey("accounts.id", ondelete="CASCADE"),
           nullable=False),
    Column("stat_date", Date, nullable=False),
    Column("source_file", String(255)),
    Column("imported_at", DateTime, nullable=False),
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
    Index("ix_daily_account_date", "account_id", "stat_date"),
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

# --- таблицы AI Avitolog (CONTRACT.md) --------------------------------------
# Пишет их проект AI Avitolog — upsert'ом по первичному ключу, каждое утро.
# Здесь они только читаются. Кабинет — avito_id строкой, без внешнего ключа на
# accounts: писатель не знает внутренних номеров. Деньги в рублях, даты — МСК,
# loaded_at — UTC. Схема меняется только после правки CONTRACT.md.

kassa_daily = Table(
    "kassa_daily", metadata,
    Column("stat_date", Date, primary_key=True),
    Column("avito_id", String(40), primary_key=True),
    Column("deposits_rub", Float, nullable=False),
    Column("deposits_count", Integer, nullable=False),
    Column("deposits_detail", String(255), nullable=False),
    Column("spend_rub", Float, nullable=False),
    Column("left_rub", Float, nullable=False),
    Column("last_chat_hour", Integer),
    Column("verdict_code", String(16), nullable=False),
    Column("verdict", String(160), nullable=False),
    Column("full_day", Integer, nullable=False),
    Column("balance_rub", Float),
    Column("note", String(255)),
    Column("loaded_at", DateTime, nullable=False),
)

chats_daily = Table(
    "chats_daily", metadata,
    Column("stat_date", Date, primary_key=True),
    Column("avito_id", String(40), primary_key=True),
    Column("chats", Integer, nullable=False),
    Column("chats_evening", Integer, nullable=False),
    Column("chats_night", Integer, nullable=False),
    Column("chats_silent", Integer),
    Column("chats_phone_left", Integer),
    Column("loaded_at", DateTime, nullable=False),
)

inventory_daily = Table(
    "inventory_daily", metadata,
    Column("snap_date", Date, primary_key=True),
    Column("avito_id", String(40), primary_key=True),
    Column("active", Integer, nullable=False),
    Column("on_bid", Integer, nullable=False),
    Column("new_1d", Integer, nullable=False),
    Column("expiring_7d", Integer, nullable=False),
    Column("price_main", Float),
    Column("cl_start", Integer, nullable=False),
    Column("cl_core", Integer, nullable=False),
    Column("cl_core_expensive", Integer, nullable=False),
    Column("cl_single", Integer, nullable=False),
    Column("cl_empty", Integer, nullable=False),
    Column("cl_untested", Integer, nullable=False),
    Column("spend_28d_rub", Float, nullable=False),
    Column("spend_28d_no_contact_rub", Float, nullable=False),
    Column("loaded_at", DateTime, nullable=False),
)

bid_batches = Table(
    "bid_batches", metadata,
    Column("batch", String(80), primary_key=True),
    Column("avito_id", String(40), primary_key=True),
    Column("batch_date", Date, primary_key=True),
    Column("batch_time", String(5)),
    Column("cards", Integer, nullable=False),
    Column("n_on", Integer, nullable=False),
    Column("n_up", Integer, nullable=False),
    Column("n_down", Integer, nullable=False),
    Column("n_off", Integer, nullable=False),
    Column("n_limit", Integer, nullable=False),
    Column("reason", String(500), nullable=False),
    Column("loaded_at", DateTime, nullable=False),
)

plan_windows = Table(
    "plan_windows", metadata,
    Column("window_id", String(40), primary_key=True),
    Column("title", String(120), nullable=False),
    Column("date_from", Date, nullable=False),
    Column("date_to", Date, nullable=False),
    Column("target_chats", Integer, nullable=False),
    Column("cpl_cap_rub", Float, nullable=False),
    Column("avito_ids", String(255), nullable=False),
    Column("forecast_chats", Integer),
    Column("forecast_note", String(255)),
    Column("is_current", Integer, nullable=False),
    Column("loaded_at", DateTime, nullable=False),
)

plan_daily = Table(
    "plan_daily", metadata,
    Column("window_id", String(40), primary_key=True),
    Column("stat_date", Date, primary_key=True),
    Column("plan_chats_cum", Float, nullable=False),
    Column("loaded_at", DateTime, nullable=False),
)

# Целевая цена контакта сегмента. Для дня действует строка сегмента с
# наибольшим valid_from, не превышающим этот день (см. CONTRACT.md).
cpl_targets = Table(
    "cpl_targets", metadata,
    Column("valid_from", Date, primary_key=True),
    Column("segment", String(16), primary_key=True),
    Column("target_cpl_rub", Float, nullable=False),
    Column("note", String(255)),
    Column("loaded_at", DateTime, nullable=False),
)

CONTRACT_TABLES = (kassa_daily, chats_daily, inventory_daily, bid_batches,
                   plan_windows, plan_daily, cpl_targets)

# Поля строки объявления — всё, кроме служебных колонок дня.
ROW_FIELDS = [c.name for c in daily_stats.columns
              if c.name not in ("id", "account_id", "stat_date", "source_file",
                                "imported_at")]

CHUNK = 500


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
    _migrate(engine)
    with engine.begin() as conn:
        existing = conn.execute(select(func.count()).select_from(accounts)).scalar_one()
        if not existing:
            conn.execute(insert(accounts),
                         [{"name": n, "is_active": 1} for n in DEFAULT_ACCOUNTS])


def _migrate(engine: Engine) -> None:
    """Догоняет схему в базах, созданных прежней версией приложения.

    Схема сначала целиком читается, и только потом выполняются DDL — каждое
    отдельной короткой транзакцией. Держать транзакцию открытой, пока в неё
    вклинивается чтение схемы, нельзя: под Postgres «DROP TABLE uploads»
    блокирует и accounts (внешний ключ), чтение с другого соединения встаёт в
    очередь за этой блокировкой, и транзакция ждёт сама себя, пока сервер не
    оборвёт соединение по idle-in-transaction timeout.
    """
    inspector = inspect(engine)
    present = set(inspector.get_table_names())
    account_columns = ({c["name"] for c in inspector.get_columns("accounts")}
                       if "accounts" in present else set())

    statements = [f"DROP TABLE IF EXISTS {table}"
                  for table in LEGACY_TABLES if table in present]
    if "accounts" in present and "avito_id" not in account_columns:
        # IF NOT EXISTS — на случай, если колонку успел добавить параллельный
        # процесс приложения: SQLite такого синтаксиса не понимает, но там
        # одновременных запусков не бывает.
        exists_clause = "IF NOT EXISTS " if engine.dialect.name == "postgresql" else ""
        statements.append(
            f"ALTER TABLE accounts ADD COLUMN {exists_clause}avito_id VARCHAR(40)")

    statements += _nan_cleanup(engine, present)

    for statement in statements:
        with engine.begin() as conn:
            conn.execute(text(statement))


def _nan_cleanup(engine: Engine, present: set[str]) -> list[str]:
    """Чинит строки, записанные до появления _clean().

    Тогда пустые регион и город попадали в Postgres текстом «NaN», и на
    дашборде это выглядело как город с таким названием. Проверка дешёвая и
    после первой починки ничего не находит, так что остаётся безвредной.
    """
    if "daily_stats" not in present:
        return []
    with engine.connect() as conn:
        broken = conn.execute(text(
            "SELECT 1 FROM daily_stats WHERE region = 'NaN' OR city = 'NaN' LIMIT 1"
        )).first()
    if not broken:
        return []
    return [f"UPDATE daily_stats SET {column} = NULL WHERE {column} = 'NaN'"
            for column in ("region", "city")]


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


def set_avito_id(engine: Engine, account_id: int, avito_id: str | None) -> None:
    value = (avito_id or "").strip() or None
    with engine.begin() as conn:
        conn.execute(accounts.update()
                     .where(accounts.c.id == account_id)
                     .values(avito_id=value))


def add_account(engine: Engine, name: str, avito_id: str | None = None) -> int:
    with engine.begin() as conn:
        result = conn.execute(insert(accounts).values(
            name=name.strip(), avito_id=(avito_id or "").strip() or None, is_active=1))
        return int(result.inserted_primary_key[0])


def delete_account(engine: Engine, account_id: int) -> bool:
    """Удаляет аккаунт вместе с его звонками. Аккаунт со статистикой не трогает.

    Возвращает False, если данные за дни у аккаунта есть — их сначала удаляют
    в разделе загрузки, чтобы статистика не пропадала одним нажатием.
    """
    with engine.begin() as conn:
        rows = conn.execute(
            select(func.count()).select_from(daily_stats)
            .where(daily_stats.c.account_id == account_id)
        ).scalar_one()
        if rows:
            return False
        conn.execute(delete(calls).where(calls.c.account_id == account_id))
        conn.execute(delete(accounts).where(accounts.c.id == account_id))
    return True


def account_for_avito_id(engine: Engine, avito_id: str) -> int:
    """Аккаунт с этим ID кабинета Авито; создаёт новый, если такого ещё нет.

    Название нового аккаунта — «Аккаунт <id>»; его можно переименовать в
    настройках, привязка к файлам от этого не потеряется.
    """
    avito_id = str(avito_id).strip()
    with engine.begin() as conn:
        found = conn.execute(
            select(accounts.c.id).where(accounts.c.avito_id == avito_id)
        ).scalar_one_or_none()
        if found is not None:
            return int(found)
        # Аккаунт мог быть заведён вручную под именем, совпадающим с ID.
        by_name = conn.execute(
            select(accounts.c.id).where(accounts.c.name == avito_id)
        ).scalar_one_or_none()
        if by_name is not None:
            conn.execute(accounts.update()
                         .where(accounts.c.id == by_name)
                         .values(avito_id=avito_id))
            return int(by_name)
        result = conn.execute(insert(accounts).values(
            name=f"Аккаунт {avito_id}", avito_id=avito_id, is_active=1))
        return int(result.inserted_primary_key[0])


# --- дневные данные ---------------------------------------------------------

def _clean(value):
    """Пустое значение pandas -> NULL.

    Обязательно делать это до вставки: в текстовой колонке Postgres сохраняет
    float('nan') строкой «NaN», и в данных заводятся города с таким именем.
    SQLite такую вставку молча превращает в NULL, поэтому локально расхождение
    не видно — ошибка проявляется только в облаке.
    """
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def save_day(engine: Engine, account_id: int, stat_date: date, filename: str,
             rows: pd.DataFrame) -> tuple[int, bool]:
    """Записывает строки за один день. Повторная загрузка заменяет прежние.

    Возвращает (сколько строк записано, были ли данные за этот день раньше).
    """
    payload = [{f: _clean(record.get(f)) for f in ROW_FIELDS}
               for record in rows.to_dict("records")]
    imported_at = datetime.now(timezone.utc)

    with engine.begin() as conn:
        target = (daily_stats.c.account_id == account_id,
                  daily_stats.c.stat_date == stat_date)
        existed = conn.execute(
            select(func.count()).select_from(daily_stats).where(*target)
        ).scalar_one()
        if existed:
            conn.execute(delete(daily_stats).where(*target))

        for start in range(0, len(payload), CHUNK):
            conn.execute(insert(daily_stats), [
                dict(row, account_id=account_id, stat_date=stat_date,
                     source_file=filename, imported_at=imported_at)
                for row in payload[start:start + CHUNK]
            ])
    return len(payload), bool(existed)


def delete_day(engine: Engine, account_id: int, stat_date: date) -> None:
    with engine.begin() as conn:
        conn.execute(delete(daily_stats).where(
            daily_stats.c.account_id == account_id,
            daily_stats.c.stat_date == stat_date,
        ))


def delete_range(engine: Engine, account_id: int, start: date, end: date) -> int:
    with engine.begin() as conn:
        result = conn.execute(delete(daily_stats).where(
            daily_stats.c.account_id == account_id,
            daily_stats.c.stat_date >= start,
            daily_stats.c.stat_date <= end,
        ))
        return int(result.rowcount or 0)


def date_bounds(engine: Engine) -> tuple[date, date] | None:
    """Первый и последний день с данными — границы календаря в дашборде."""
    with engine.connect() as conn:
        row = conn.execute(select(func.min(daily_stats.c.stat_date),
                                  func.max(daily_stats.c.stat_date))).one()
    first, last = row
    if first is None or last is None:
        return None
    return _as_date(first), _as_date(last)


def _as_date(value) -> date:
    """SQLite отдаёт даты строками, Postgres — объектами date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def imported_days(engine: Engine) -> pd.DataFrame:
    """Сводка загруженного: аккаунт, день, число строк, файл, время импорта."""
    query = (
        select(
            daily_stats.c.account_id,
            accounts.c.name.label("account"),
            daily_stats.c.stat_date,
            func.count().label("rows_count"),
            func.max(daily_stats.c.source_file).label("source_file"),
            func.max(daily_stats.c.imported_at).label("imported_at"),
        )
        .select_from(daily_stats.join(accounts,
                                      daily_stats.c.account_id == accounts.c.id))
        .group_by(daily_stats.c.account_id, accounts.c.name, daily_stats.c.stat_date)
        .order_by(daily_stats.c.stat_date.desc(), accounts.c.name)
    )
    with engine.connect() as conn:
        df = pd.DataFrame(conn.execute(query).mappings().all())
    if not df.empty:
        df["stat_date"] = pd.to_datetime(df["stat_date"])
        df["imported_at"] = pd.to_datetime(df["imported_at"])
    return df


def load_daily(engine: Engine, start: date | None = None,
               end: date | None = None) -> pd.DataFrame:
    """Строки объявлений за интервал дат с названием аккаунта."""
    query = (
        select(daily_stats, accounts.c.name.label("account"))
        .select_from(daily_stats.join(accounts,
                                      daily_stats.c.account_id == accounts.c.id))
    )
    if start is not None:
        query = query.where(daily_stats.c.stat_date >= start)
    if end is not None:
        query = query.where(daily_stats.c.stat_date <= end)

    with engine.connect() as conn:
        df = pd.DataFrame(conn.execute(query).mappings().all())
    if df.empty:
        return df
    df["stat_date"] = pd.to_datetime(df["stat_date"])
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


# --- данные AI Avitolog: только чтение --------------------------------------

def _account_names(conn) -> dict[str, str]:
    rows = conn.execute(select(accounts.c.avito_id, accounts.c.name)
                        .where(accounts.c.avito_id.is_not(None))).all()
    return {str(avito_id): name for avito_id, name in rows}


def account_label(names: dict[str, str], avito_id) -> str:
    """Имя кабинета для показа; незнакомый кабинет подписывается его ID."""
    return names.get(str(avito_id), f"Кабинет {avito_id}")


def _read_contract(engine: Engine, table: Table, date_column: str,
                   start: date | None, end: date | None,
                   avito_ids: list[str] | None = None) -> pd.DataFrame:
    """Строки таблицы контракта за интервал с колонкой account — именем кабинета."""
    column = table.c[date_column]
    query = select(table).order_by(column)
    if start is not None:
        query = query.where(column >= start)
    if end is not None:
        query = query.where(column <= end)
    if avito_ids is not None:
        query = query.where(table.c.avito_id.in_(avito_ids))
    with engine.connect() as conn:
        df = pd.DataFrame(conn.execute(query).mappings().all())
        names = _account_names(conn)
    if df.empty:
        return pd.DataFrame(columns=[c.name for c in table.columns] + ["account"])
    df[date_column] = pd.to_datetime(df[date_column])
    df["loaded_at"] = pd.to_datetime(df["loaded_at"])
    df["avito_id"] = df["avito_id"].astype(str)
    df["account"] = df["avito_id"].map(lambda v: account_label(names, v))
    return df


def load_kassa(engine: Engine, start: date | None = None, end: date | None = None,
               avito_ids: list[str] | None = None) -> pd.DataFrame:
    return _read_contract(engine, kassa_daily, "stat_date", start, end, avito_ids)


def load_inventory(engine: Engine, start: date | None = None,
                   end: date | None = None) -> pd.DataFrame:
    return _read_contract(engine, inventory_daily, "snap_date", start, end)


def load_bid_batches(engine: Engine, start: date | None = None,
                     end: date | None = None) -> pd.DataFrame:
    return _read_contract(engine, bid_batches, "batch_date", start, end)


def load_cpl_targets(engine: Engine) -> pd.DataFrame:
    """Вся история планок: valid_from, segment, target_cpl_rub. Таблица крошечная."""
    query = select(cpl_targets.c.valid_from, cpl_targets.c.segment,
                   cpl_targets.c.target_cpl_rub).order_by(cpl_targets.c.valid_from)
    with engine.connect() as conn:
        df = pd.DataFrame(conn.execute(query).mappings().all())
    if df.empty:
        return pd.DataFrame(columns=["valid_from", "segment", "target_cpl_rub"])
    df["valid_from"] = pd.to_datetime(df["valid_from"])
    df["target_cpl_rub"] = pd.to_numeric(df["target_cpl_rub"], errors="coerce")
    return df.dropna(subset=["target_cpl_rub"])


def contract_bounds(engine: Engine) -> tuple[date, date] | None:
    """Первый и последний день в дневных таблицах AI Avitolog."""
    found = []
    with engine.connect() as conn:
        for table, column in ((kassa_daily, "stat_date"), (inventory_daily, "snap_date")):
            found.append(conn.execute(select(func.min(table.c[column]),
                                             func.max(table.c[column]))).one())
    days = [_as_date(v) for row in found for v in row if v is not None]
    if not days:
        return None
    return min(days), max(days)
