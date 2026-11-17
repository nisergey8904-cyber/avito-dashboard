"""Общие помощники интерфейса: форматирование и кэш данных."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from avito import db

NBSP = " "
DASH = "—"

PALETTE = ["#2E6FD9", "#00A87E", "#F2A93B", "#D9534F", "#7B5AA6", "#4BB3C4"]


def _is_missing(value) -> bool:
    return value is None or (isinstance(value, float) and pd.isna(value))


def fmt_int(value) -> str:
    if _is_missing(value):
        return DASH
    return f"{int(round(float(value))):,}".replace(",", NBSP)


def fmt_money(value, digits: int = 0) -> str:
    if _is_missing(value):
        return DASH
    text = f"{float(value):,.{digits}f}".replace(",", NBSP).replace(".", ",")
    return f"{text}{NBSP}₽"


def fmt_pct(value, digits: int = 1) -> str:
    if _is_missing(value):
        return DASH
    return f"{float(value) * 100:.{digits}f}".replace(".", ",") + f"{NBSP}%"


@st.cache_data(ttl=300, show_spinner="Читаю данные…")
def cached_daily(_engine, version: int, start: date, end: date) -> pd.DataFrame:
    return db.load_daily(_engine, start, end)


@st.cache_data(ttl=300, show_spinner=False)
def cached_bounds(_engine, version: int) -> tuple[date, date] | None:
    return db.date_bounds(_engine)


@st.cache_data(ttl=300, show_spinner=False)
def cached_days(_engine, version: int) -> pd.DataFrame:
    return db.imported_days(_engine)


@st.cache_data(ttl=300, show_spinner=False)
def cached_calls(_engine, version: int) -> pd.DataFrame:
    return db.load_calls(_engine)


@st.cache_data(ttl=300, show_spinner=False)
def cached_accounts(_engine, version: int) -> pd.DataFrame:
    return db.list_accounts(_engine)


@st.cache_data(ttl=300, show_spinner=False)
def cached_contract_bounds(_engine, version: int) -> tuple[date, date] | None:
    return db.contract_bounds(_engine)


@st.cache_data(ttl=300, show_spinner=False)
def cached_contract(_engine, version: int, start: date,
                    end: date) -> dict[str, pd.DataFrame]:
    """Таблицы AI Avitolog за период: касса, чаты, инвентарь, партии ставок."""
    return {
        "kassa": db.load_kassa(_engine, start, end),
        "chats": db.load_chats(_engine, start, end),
        "inventory": db.load_inventory(_engine, start, end),
        "batches": db.load_bid_batches(_engine, start, end),
    }


@st.cache_data(ttl=300, show_spinner=False)
def cached_plan(_engine, version: int) -> dict[str, pd.DataFrame]:
    """Текущие окна план/факт с плановой кривой и фактом по их кабинетам."""
    windows, curve = db.load_plan(_engine)
    if windows.empty:
        return {"windows": windows, "curve": curve,
                "chats": pd.DataFrame(), "kassa": pd.DataFrame()}
    ids = sorted({i.strip() for ids in windows["avito_ids"]
                  for i in str(ids).split(",") if i.strip()})
    start, end = windows["date_from"].min(), windows["date_to"].max()
    return {
        "windows": windows, "curve": curve,
        "chats": db.load_chats(_engine, start, end, ids),
        "kassa": db.load_kassa(_engine, start, end, ids),
    }


def data_version() -> int:
    """Счётчик, по которому сбрасывается кэш после изменения данных."""
    return st.session_state.get("data_version", 0)


def bump_data_version() -> None:
    st.session_state["data_version"] = data_version() + 1


def empty_state(message: str, hint: str = "") -> None:
    st.info(message)
    if hint:
        st.caption(hint)
