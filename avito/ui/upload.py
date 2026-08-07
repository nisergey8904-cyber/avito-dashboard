"""Загрузка недельных выгрузок Авито."""

from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from avito import db
from avito.parser import ParseError, parse_workbook
from avito.ui.common import (
    bump_data_version, cached_accounts, data_version, fmt_int, fmt_money,
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
    st.caption("Перетащите файлы выгрузки из Авито и укажите, какому аккаунту "
               "принадлежит каждый. Повторная загрузка того же периода заменит "
               "прежние данные.")

    accounts = cached_accounts(engine, data_version())
    name_by_id = dict(zip(accounts["id"], accounts["name"]))

    files = st.file_uploader("Файлы выгрузки (.xlsx)", type=["xlsx"],
                             accept_multiple_files=True)
    if not files:
        _uploads_table(engine)
        return

    plans = []
    st.subheader("Что будет загружено")
    for index, uploaded in enumerate(files):
        payload = uploaded.getvalue()
        try:
            parsed = _parse_cached(payload, uploaded.name)
        except ParseError as exc:
            st.error(f"**{uploaded.name}** — {exc}")
            continue

        with st.container(border=True):
            st.markdown(f"**{uploaded.name}**")
            st.caption(_preview_line(parsed.rows))
            columns = st.columns([2, 1.2, 1.2])
            account_id = columns[0].selectbox(
                "Аккаунт", list(name_by_id), key=f"acc_{index}",
                format_func=lambda i: name_by_id[i],
            )
            start = columns[1].date_input("Начало периода", parsed.period_start,
                                          key=f"start_{index}", format="DD.MM.YYYY")
            end = columns[2].date_input("Конец периода", parsed.period_end,
                                        key=f"end_{index}", format="DD.MM.YYYY")
            plans.append((uploaded.name, account_id, start, end, parsed.rows))

    if not plans:
        return

    duplicates = _duplicate_targets(plans)
    if duplicates:
        st.warning("Один и тот же аккаунт выбран для нескольких файлов с одинаковым "
                   "периодом: " + "; ".join(duplicates)
                   + ". Сохранится только последний — проверьте выбор.")

    if st.button("Импортировать", type="primary"):
        saved, replaced = 0, 0
        for filename, account_id, start, end, rows in plans:
            if start > end:
                st.error(f"**{filename}** — начало периода позже конца.")
                continue
            _, was_replaced = db.save_upload(engine, account_id, start, end,
                                             filename, rows)
            saved += 1
            replaced += int(was_replaced)
        bump_data_version()
        message = f"Загружено файлов: {saved}."
        if replaced:
            message += f" Из них заменили прежние данные: {replaced}."
        st.success(message)
        st.rerun()

    _uploads_table(engine)


def _duplicate_targets(plans) -> list[str]:
    seen: dict[tuple, int] = {}
    for _, account_id, start, end, _rows in plans:
        seen[(account_id, start, end)] = seen.get((account_id, start, end), 0) + 1
    return [f"{key[0]} / {key[1]:%d.%m.%Y}–{key[2]:%d.%m.%Y}"
            for key, count in seen.items() if count > 1]


def _uploads_table(engine) -> None:
    st.divider()
    st.subheader("Загруженные выгрузки")
    uploads = db.list_uploads(engine)
    if uploads.empty:
        st.info("Пока ничего не загружено.")
        return

    table = uploads.copy()
    table["Период"] = (pd.to_datetime(table["period_start"]).dt.strftime("%d.%m.%Y")
                       + " – "
                       + pd.to_datetime(table["period_end"]).dt.strftime("%d.%m.%Y"))
    table["Загружено"] = pd.to_datetime(table["uploaded_at"]).dt.strftime("%d.%m.%Y %H:%M")
    st.dataframe(
        table[["account", "Период", "rows_count", "filename", "Загружено"]]
        .rename(columns={"account": "Аккаунт", "rows_count": "Строк",
                         "filename": "Файл"}),
        width="stretch", hide_index=True,
    )

    with st.expander("Удалить выгрузку"):
        labels = {
            int(row.id): f"{row.account} · {row.period_start:%d.%m.%Y}–"
                         f"{row.period_end:%d.%m.%Y} · {row.filename}"
            for row in uploads.itertuples()
        }
        target = st.selectbox("Выгрузка", list(labels),
                              format_func=lambda i: labels[i])
        if st.button("Удалить", type="secondary"):
            db.delete_upload(engine, target)
            bump_data_version()
            st.success("Выгрузка удалена.")
            st.rerun()
