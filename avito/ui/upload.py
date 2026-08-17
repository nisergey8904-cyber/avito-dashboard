"""Загрузка дневных выгрузок Авито."""

from __future__ import annotations

import io
from datetime import date

import pandas as pd
import streamlit as st

from avito import db
from avito.parser import ParseError, parse_workbook
from avito.ui.common import (
    bump_data_version, cached_accounts, cached_days, data_version, fmt_int,
    fmt_money,
)


@st.cache_data(show_spinner=False)
def _parse_cached(payload: bytes, filename: str):
    return parse_workbook(io.BytesIO(payload), filename)


def _preview_line(rows: pd.DataFrame) -> str:
    return (f"{fmt_int(len(rows))} объявл. · "
            f"{fmt_int(rows['impressions'].sum())} показов · "
            f"{fmt_int(rows['views'].sum())} просмотров · "
            f"{fmt_int(rows['contacts'].sum())} контактов · "
            f"{fmt_money(rows['spend_total'].sum())}")


def upload_page(engine) -> None:
    st.header("Загрузка выгрузок")
    st.caption("Нужны выгрузки за один день — «Статистика_за_2026-07-23.xlsx». "
               "Дата берётся из имени файла, аккаунт указывается здесь. "
               "Повторная загрузка того же дня заменит прежние данные.")

    accounts = cached_accounts(engine, data_version())
    name_by_id = dict(zip(accounts["id"], accounts["name"]))

    files = st.file_uploader("Файлы выгрузки (.xlsx)", type=["xlsx"],
                             accept_multiple_files=True)
    if not files:
        _days_table(engine)
        return

    st.subheader("Что будет загружено")
    same_account = st.checkbox(
        "Все файлы одного аккаунта", value=True,
        help="Обычно за раз загружают папку дней одного кабинета Авито.")
    shared_account = None
    if same_account:
        shared_account = st.selectbox("Аккаунт", list(name_by_id),
                                      format_func=lambda i: name_by_id[i])

    plans = []
    for uploaded in files:
        payload = uploaded.getvalue()
        try:
            parsed = _parse_cached(payload, uploaded.name)
        except ParseError as exc:
            st.error(f"**{uploaded.name}** — {exc}")
            continue

        with st.container(border=True):
            st.markdown(f"**{uploaded.name}**")
            st.caption(_preview_line(parsed.rows))
            if same_account:
                account_id = shared_account
                day_cell = st.container()
            else:
                account_cell, day_cell = st.columns([2, 1.4])
                account_id = account_cell.selectbox(
                    "Аккаунт", list(name_by_id), key=f"acc_{uploaded.name}",
                    format_func=lambda i: name_by_id[i],
                )
            if parsed.stat_date is None:
                day_cell.warning("В имени файла нет даты — укажите день вручную.",
                                 icon="📅")
            # Ключ включает файл и распознанный день: иначе Streamlit оставил бы
            # в поле дату от файла, который был на этом месте в прошлый раз.
            day = day_cell.date_input(
                "День", parsed.stat_date or date.today(), format="DD.MM.YYYY",
                key=f"day_{uploaded.name}_{parsed.stat_date}",
            )
            plans.append((uploaded.name, account_id, day, parsed.rows))

    if not plans:
        return

    duplicates = _duplicate_targets(plans, name_by_id)
    if duplicates:
        st.warning("Один и тот же аккаунт и день выбраны для нескольких файлов: "
                   + "; ".join(duplicates)
                   + ". Сохранится только последний — проверьте выбор.")

    if st.button("Импортировать", type="primary"):
        saved, replaced, rows_total = 0, 0, 0
        for filename, account_id, day, rows in plans:
            count, was_replaced = db.save_day(engine, account_id, day, filename, rows)
            saved += 1
            rows_total += count
            replaced += int(was_replaced)
        bump_data_version()
        message = f"Загружено файлов: {saved}, строк: {fmt_int(rows_total)}."
        if replaced:
            message += f" Дней перезаписано: {replaced}."
        st.success(message)
        st.rerun()

    _days_table(engine)


def _duplicate_targets(plans, name_by_id: dict[int, str]) -> list[str]:
    seen: dict[tuple, int] = {}
    for _, account_id, day, _rows in plans:
        seen[(account_id, day)] = seen.get((account_id, day), 0) + 1
    return [f"{name_by_id.get(account_id, account_id)} / {day:%d.%m.%Y}"
            for (account_id, day), count in seen.items() if count > 1]


def _days_table(engine) -> None:
    st.divider()
    st.subheader("Загруженные дни")
    days = cached_days(engine, data_version())
    if days.empty:
        st.info("Пока ничего не загружено.")
        return

    summary = st.columns(3)
    summary[0].metric("Дней с данными", days["stat_date"].nunique())
    summary[1].metric("Строк всего", fmt_int(days["rows_count"].sum()))
    summary[2].metric("Последний день", f"{days['stat_date'].max():%d.%m.%Y}")

    table = days.copy()
    table["День"] = table["stat_date"].dt.strftime("%d.%m.%Y")
    table["Загружено"] = table["imported_at"].dt.strftime("%d.%m.%Y %H:%M")
    st.dataframe(
        table[["account", "День", "rows_count", "source_file", "Загружено"]]
        .rename(columns={"account": "Аккаунт", "rows_count": "Строк",
                         "source_file": "Файл"}),
        width="stretch", hide_index=True, height=320,
    )

    with st.expander("Удалить данные"):
        accounts_in_data = (days[["account_id", "account"]].drop_duplicates()
                           .sort_values("account"))
        target_account = st.selectbox(
            "Аккаунт", accounts_in_data["account_id"].tolist(),
            format_func=lambda i: accounts_in_data.loc[
                accounts_in_data["account_id"] == i, "account"].iloc[0],
            key="delete_account",
        )
        own = days[days["account_id"] == target_account]
        first = own["stat_date"].min().date()
        last = own["stat_date"].max().date()
        chosen = st.date_input("Дни к удалению", (first, last), min_value=first,
                               max_value=last, format="DD.MM.YYYY",
                               key="delete_range")
        if isinstance(chosen, (tuple, list)) and len(chosen) == 2:
            start, end = chosen
            if st.button(f"Удалить {start:%d.%m.%Y}–{end:%d.%m.%Y}", type="secondary"):
                removed = db.delete_range(engine, int(target_account), start, end)
                bump_data_version()
                st.success(f"Удалено строк: {fmt_int(removed)}.")
                st.rerun()
        else:
            st.caption("Выберите вторую дату интервала.")
