"""Настройки: имена аккаунтов и состояние базы."""

from __future__ import annotations

import streamlit as st

from avito import db
from avito.ui.common import bump_data_version, cached_accounts, data_version


def settings_page(engine) -> None:
    st.header("Настройки")

    st.subheader("Аккаунты")
    st.caption("В выгрузках Авито нет признака аккаунта, поэтому названия задаются "
               "здесь и выбираются при загрузке файла. Понятные имена помогут "
               "начальнику читать дашборд.")

    accounts = cached_accounts(engine, data_version())
    with st.form("rename_accounts"):
        new_names = {}
        for row in accounts.itertuples():
            new_names[int(row.id)] = st.text_input(
                f"Аккаунт #{row.id}", row.name, key=f"name_{row.id}")
        if st.form_submit_button("Сохранить названия", type="primary"):
            renamed = 0
            for account_id, name in new_names.items():
                current = accounts.loc[accounts["id"] == account_id, "name"].iloc[0]
                if name.strip() and name.strip() != current:
                    db.rename_account(engine, account_id, name)
                    renamed += 1
            bump_data_version()
            st.success(f"Обновлено названий: {renamed}." if renamed
                       else "Изменений не было.")
            st.rerun()

    with st.expander("Добавить аккаунт"):
        name = st.text_input("Название нового аккаунта", key="new_account")
        if st.button("Добавить"):
            if not name.strip():
                st.error("Введите название.")
            elif name.strip() in set(accounts["name"]):
                st.error("Аккаунт с таким названием уже есть.")
            else:
                db.add_account(engine, name)
                bump_data_version()
                st.success("Аккаунт добавлен.")
                st.rerun()

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

    uploads = db.list_uploads(engine)
    calls = db.load_calls(engine)
    stats = st.columns(3)
    stats[0].metric("Выгрузок", len(uploads))
    stats[1].metric("Строк объявлений",
                    int(uploads["rows_count"].sum()) if not uploads.empty else 0)
    stats[2].metric("Записей о звонках", len(calls))

    if not uploads.empty:
        st.download_button(
            "Скачать сводку по выгрузкам (CSV)",
            uploads.to_csv(index=False).encode("utf-8-sig"),
            file_name="uploads.csv", mime="text/csv",
        )
