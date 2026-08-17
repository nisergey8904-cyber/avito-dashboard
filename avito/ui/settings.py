"""Настройки: имена аккаунтов и состояние базы."""

from __future__ import annotations

import streamlit as st

import pandas as pd

from avito import db
from avito.ui.common import (
    bump_data_version, cached_accounts, cached_days, data_version, fmt_int,
)


def _text(value) -> str:
    """Пустой ID приходит из базы как None, а из DataFrame — как NaN."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def settings_page(engine) -> None:
    st.header("Настройки")

    st.subheader("Аккаунты")
    st.caption("В выгрузках Авито нет признака аккаунта, поэтому названия задаются "
               "здесь. ID кабинета Авито — это имя папки при пакетном импорте: по "
               "нему файлы находят свой аккаунт даже после переименования.")

    accounts = cached_accounts(engine, data_version())
    with st.form("edit_accounts"):
        edited = {}
        for row in accounts.itertuples():
            name_cell, id_cell = st.columns([2, 1])
            edited[int(row.id)] = (
                name_cell.text_input(f"Аккаунт #{row.id}", row.name,
                                     key=f"name_{row.id}"),
                id_cell.text_input("ID Авито", _text(row.avito_id),
                                   key=f"avito_{row.id}",
                                   placeholder="например 181777530"),
            )
        if st.form_submit_button("Сохранить", type="primary"):
            changed = 0
            for account_id, (name, avito_id) in edited.items():
                current = accounts.loc[accounts["id"] == account_id].iloc[0]
                if name.strip() and name.strip() != current["name"]:
                    db.rename_account(engine, account_id, name)
                    changed += 1
                if _text(avito_id) != _text(current["avito_id"]):
                    db.set_avito_id(engine, account_id, avito_id)
                    changed += 1
            bump_data_version()
            st.success(f"Сохранено изменений: {changed}." if changed
                       else "Изменений не было.")
            st.rerun()

    with st.expander("Добавить аккаунт"):
        name = st.text_input("Название нового аккаунта", key="new_account")
        avito_id = st.text_input("ID Авито (необязательно)", key="new_account_avito")
        if st.button("Добавить"):
            if not name.strip():
                st.error("Введите название.")
            elif name.strip() in set(accounts["name"]):
                st.error("Аккаунт с таким названием уже есть.")
            else:
                db.add_account(engine, name, avito_id)
                bump_data_version()
                st.success("Аккаунт добавлен.")
                st.rerun()

    _delete_unused(engine, accounts)

    st.divider()
    st.subheader("База данных")
    url = db.database_url()
    safe_url = url if url.startswith("sqlite") else url.split("@")[-1]
    st.code(safe_url, language="text")
    if url.startswith("sqlite"):
        st.warning(
            "Сейчас используется локальный файл SQLite. При публикации на Streamlit "
            "Community Cloud файловая система обнуляется при каждом перезапуске "
            "приложения — данные пропадут. Для облака укажите внешнюю базу в секрете "
            "`db_url` (см. README).",
            icon="⚠️",
        )

    days = cached_days(engine, data_version())
    calls = db.load_calls(engine)
    stats = st.columns(3)
    stats[0].metric("Дней с данными", days["stat_date"].nunique() if not days.empty else 0)
    stats[1].metric("Строк статистики",
                    fmt_int(days["rows_count"].sum()) if not days.empty else 0)
    stats[2].metric("Записей о звонках", len(calls))

    if not days.empty:
        st.download_button(
            "Скачать сводку по дням (CSV)",
            days.to_csv(index=False).encode("utf-8-sig"),
            file_name="imported_days.csv", mime="text/csv",
        )


def _delete_unused(engine, accounts) -> None:
    """Удаление пустых аккаунтов: после перехода на импорт по ID кабинета
    остаются заготовки вроде «Аккаунт 1», которые только мешают в списках."""
    used = set(cached_days(engine, data_version()).get("account_id", []))
    unused = accounts[~accounts["id"].isin(used)]
    if unused.empty:
        return

    with st.expander(f"Удалить аккаунт без данных ({len(unused)})"):
        names = dict(zip(unused["id"], unused["name"]))
        target = st.selectbox("Аккаунт", list(names), format_func=lambda i: names[i],
                              key="delete_account_choice")
        st.caption("Удаляются только аккаунты, по которым нет загруженной статистики. "
                   "Введённые для них звонки тоже удалятся.")
        if st.button("Удалить аккаунт"):
            if db.delete_account(engine, int(target)):
                bump_data_version()
                st.success(f"Аккаунт «{names[target]}» удалён.")
                st.rerun()
            else:
                st.error("У аккаунта появились данные — сначала удалите их "
                         "в разделе «Загрузка выгрузок».")
