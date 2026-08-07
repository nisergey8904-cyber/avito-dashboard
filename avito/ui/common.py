"""Общие помощники интерфейса: форматирование и кэш данных."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from avito import db

NBSP = " "
DASH = "—"


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
def cached_listings(_engine, version: int) -> pd.DataFrame:
    return db.load_listings(_engine)


@st.cache_data(ttl=300, show_spinner=False)
def cached_calls(_engine, version: int) -> pd.DataFrame:
    return db.load_calls(_engine)


@st.cache_data(ttl=300, show_spinner=False)
def cached_accounts(_engine, version: int) -> pd.DataFrame:
    return db.list_accounts(_engine)


def data_version() -> int:
    """Счётчик, по которому сбрасывается кэш после изменения данных."""
    return st.session_state.get("data_version", 0)


def bump_data_version() -> None:
    st.session_state["data_version"] = data_version() + 1


def empty_state(message: str, hint: str = "") -> None:
    st.info(message)
    if hint:
        st.caption(hint)
