"""Дашборд статистики Авито-аккаунтов.

Запуск локально:  streamlit run app.py
"""

from __future__ import annotations

import os

import streamlit as st

st.set_page_config(page_title="Статистика Авито", page_icon="📊", layout="wide")


def _secret(key: str, default: str = "") -> str:
    """st.secrets падает, если файла секретов нет — например, при локальном запуске."""
    try:
        value = st.secrets.get(key, default)
    except Exception:  # noqa: BLE001 — отсутствие secrets.toml не должно ронять приложение
        value = default
    return str(value or default)


# Адрес базы должен быть в окружении до импорта db: в облаке это внешний Postgres.
if _secret("db_url") and not os.environ.get("DASHBOARD_DB_URL"):
    os.environ["DASHBOARD_DB_URL"] = _secret("db_url")

from avito import db  # noqa: E402 — импорт после установки DASHBOARD_DB_URL
from avito.ui import calls_page, dashboard_page, settings_page, upload_page  # noqa: E402

ADMIN_PASSWORD = _secret("admin_password")
VIEWER_PASSWORD = _secret("viewer_password")


@st.cache_resource
def engine():
    return db.get_engine()


def connect_or_explain():
    """Подключается к базе, а при неудаче объясняет причину вместо трейса."""
    try:
        return engine()
    except db.DatabaseConfigError as exc:
        st.title("📊 Статистика Авито")
        st.error(str(exc), icon="⚠️")
        st.markdown(
            "Строка подключения задаётся в настройках приложения: "
            "**Manage app → Settings → Secrets**, параметр `db_url`. "
            "После сохранения приложение перезапустится само."
        )
        st.stop()
    except Exception as exc:  # noqa: BLE001 — показываем причину, а не трейс
        st.title("📊 Статистика Авито")
        st.error("Не удалось подключиться к базе данных.", icon="⚠️")
        st.caption(f"Ответ драйвера: {type(exc).__name__}: {exc}"[:500])
        st.markdown(
            "Что проверить:\n"
            "- в секрете `db_url` указана строка из панели Neon целиком, "
            "вместе с `?sslmode=require`;\n"
            "- проект в Neon не удалён и не приостановлен;\n"
            "- пароль в строке не был изменён после копирования."
        )
        st.stop()


def authenticate() -> str:
    """Возвращает роль: 'admin' или 'viewer'.

    Пароли не заданы — приложение работает без входа с полными правами
    (режим локального запуска). В облаке пароли задаются в секретах.
    """
    if not ADMIN_PASSWORD and not VIEWER_PASSWORD:
        return "admin"

    if role := st.session_state.get("role"):
        return role

    st.title("📊 Статистика Авито")
    st.caption("Введите пароль для доступа к дашборду.")
    with st.form("login"):
        password = st.text_input("Пароль", type="password")
        submitted = st.form_submit_button("Войти", type="primary")
    if submitted:
        if ADMIN_PASSWORD and password == ADMIN_PASSWORD:
            st.session_state["role"] = "admin"
            st.rerun()
        elif VIEWER_PASSWORD and password == VIEWER_PASSWORD:
            st.session_state["role"] = "viewer"
            st.rerun()
        else:
            st.error("Неверный пароль.")
    st.stop()


def main() -> None:
    role = authenticate()
    eng = connect_or_explain()

    with st.sidebar:
        st.title("📊 Статистика Авито")
        pages = ["Дашборд"]
        if role == "admin":
            pages += ["Загрузка выгрузок", "Звонки", "Настройки"]
        page = st.radio("Раздел", pages, label_visibility="collapsed")
        st.divider()
        if role == "viewer":
            st.caption("Режим просмотра.")
        if st.session_state.get("role"):
            if st.button("Выйти", width="stretch"):
                st.session_state.pop("role", None)
                st.rerun()

    if page == "Дашборд":
        dashboard_page(eng)
    elif page == "Загрузка выгрузок":
        upload_page(eng)
    elif page == "Звонки":
        calls_page(eng)
    else:
        settings_page(eng)


main()
