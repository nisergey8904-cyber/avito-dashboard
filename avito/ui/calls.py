"""Ручной ввод фактических звонков по дням и аккаунтам."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from avito import db
from avito.ui.common import bump_data_version, cached_accounts, data_version


def calls_page(engine) -> None:
    st.header("Звонки")
    st.caption("Фактические звонки вводятся вручную: одна строка — день и аккаунт. "
               "Дашборд соотносит их с периодами выгрузок автоматически.")

    accounts = cached_accounts(engine, data_version())
    name_by_id = dict(zip(accounts["id"], accounts["name"]))
    existing = db.load_calls(engine)

    _quick_form(engine, name_by_id, existing)
    st.divider()
    _history(engine, existing, name_by_id)


def _quick_form(engine, name_by_id: dict[int, str], existing: pd.DataFrame) -> None:
    st.subheader("Ввод за день")
    with st.form("calls_form"):
        day = st.date_input("Дата", date.today() - timedelta(days=1),
                            format="DD.MM.YYYY")
        st.caption("Введите количество звонков по каждому аккаунту за этот день.")

        previous = {}
        if not existing.empty:
            same_day = existing[existing["call_date"] == pd.Timestamp(day)]
            previous = dict(zip(same_day["account_id"], same_day["calls"]))

        columns = st.columns(len(name_by_id) or 1)
        entered = {}
        for column, (account_id, name) in zip(columns, name_by_id.items()):
            entered[account_id] = column.number_input(
                name, min_value=0, step=1,
                value=int(previous.get(account_id, 0)),
                key=f"calls_{account_id}",
            )
        submitted = st.form_submit_button("Сохранить", type="primary")

    if submitted:
        for account_id, value in entered.items():
            db.upsert_calls(engine, account_id, day, int(value))
        bump_data_version()
        st.success(f"Звонки за {day:%d.%m.%Y} сохранены.")
        st.rerun()


def _history(engine, existing: pd.DataFrame, name_by_id: dict[int, str]) -> None:
    st.subheader("История")
    if existing.empty:
        st.info("Звонки ещё не вводились.")
        return

    pivot = (existing.pivot_table(index="call_date", columns="account",
                                  values="calls", aggfunc="sum", fill_value=0)
             .sort_index(ascending=False))
    pivot.index = pivot.index.strftime("%d.%m.%Y")
    pivot.index.name = "Дата"
    total = pivot.sum(axis=1)
    display = pivot.copy()
    display["Всего"] = total
    st.dataframe(display, width="stretch")

    summary = st.columns(3)
    summary[0].metric("Всего звонков", int(existing["calls"].sum()))
    summary[1].metric("Дней с данными", existing["call_date"].nunique())
    last_day = existing["call_date"].max()
    summary[2].metric("Последний день", f"{last_day:%d.%m.%Y}")

    with st.expander("Удалить запись"):
        labels = {
            int(row.id): f"{row.call_date:%d.%m.%Y} · {row.account} · {row.calls}"
            for row in existing.itertuples()
        }
        target = st.selectbox("Запись", list(labels), format_func=lambda i: labels[i])
        if st.button("Удалить запись"):
            db.delete_calls(engine, target)
            bump_data_version()
            st.success("Запись удалена.")
            st.rerun()
