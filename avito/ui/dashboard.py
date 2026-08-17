"""Главный экран: воронка, экономика и сравнение аккаунтов.

Период задаётся календарём: данные лежат подневно, поэтому любой интервал —
от одного дня до всей истории — собирается на лету.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from avito import metrics
from avito.ui.common import (
    DASH, bump_data_version, cached_bounds, cached_calls, cached_daily,
    data_version, fmt_int, fmt_money, fmt_pct,
)

PALETTE = ["#2E6FD9", "#00A87E", "#F2A93B", "#D9534F", "#7B5AA6", "#4BB3C4"]

# Выбранный интервал хранится в обычном ключе сессии, а не в ключе виджета:
# значение виджета Streamlit запрещает менять из кода после первой отрисовки,
# а кнопкам быстрого выбора это нужно на каждом перезапуске.
RANGE_KEY = "dashboard_range"

# Быстрый выбор: подпись -> сколько последних дней истории брать (None — всю).
QUICK_RANGES = {"7 дней": 7, "30 дней": 30, "90 дней": 90, "Всё время": None}

GRANULARITY = {"По дням": "D", "По неделям": "W", "По месяцам": "M"}


def _default_range(first: date, last: date) -> tuple[date, date]:
    return max(first, last - timedelta(days=29)), last


def _remembered_range(first: date, last: date) -> tuple[date, date]:
    """Прошлый выбор, подрезанный под доступные даты.

    Новые данные сдвигают границы календаря, а в сессии может лежать интервал
    за их пределами — date_input на таком значении падает.
    """
    stored = st.session_state.get(RANGE_KEY)
    if isinstance(stored, (tuple, list)) and len(stored) == 2:
        start, end = stored
        if isinstance(start, date) and isinstance(end, date):
            start, end = max(start, first), min(end, last)
            if start <= end:
                return start, end
    return _default_range(first, last)


def _period_picker(first: date, last: date) -> tuple[date, date]:
    """Календарь с кнопками быстрого выбора. Возвращает границы интервала.

    Кнопки обрабатываются раньше календаря: нажатие меняет интервал, который
    календарь получает как значение по умолчанию в этом же перезапуске.
    """
    calendar_cell, quick_cell = st.columns([2, 2])

    with quick_cell:
        st.caption("Быстрый выбор")
        buttons = st.columns(len(QUICK_RANGES))
        for column, (label, days) in zip(buttons, QUICK_RANGES.items()):
            if column.button(label, width="stretch", key=f"quick_{label}"):
                start = (first if days is None
                         else max(first, last - timedelta(days=days - 1)))
                st.session_state[RANGE_KEY] = (start, last)

    with calendar_cell:
        chosen = st.date_input(
            "Период", value=_remembered_range(first, last),
            min_value=first, max_value=last, format="DD.MM.YYYY",
            help=f"Данные есть с {first:%d.%m.%Y} по {last:%d.%m.%Y}.",
        )

    if not isinstance(chosen, (tuple, list)):
        chosen = (chosen,)
    if len(chosen) < 2:
        # Первый клик в календаре: конец интервала ещё не выбран.
        st.caption("Выберите вторую дату периода — пока показан один день.")
        return chosen[0], chosen[0]

    st.session_state[RANGE_KEY] = (chosen[0], chosen[1])
    return chosen[0], chosen[1]


def _filters(rows: pd.DataFrame) -> pd.DataFrame:
    accounts = sorted(rows["account"].unique())
    left, right = st.columns([2, 1.4])
    with left:
        chosen_accounts = st.multiselect("Аккаунты", accounts, default=accounts)
    with right:
        regions = sorted(rows["region"].dropna().unique())
        chosen_regions = st.multiselect("Регионы", regions, placeholder="Все регионы")

    mask = rows["account"].isin(chosen_accounts)
    if chosen_regions:
        mask &= rows["region"].isin(chosen_regions)
    return rows[mask].copy()


def _kpi_row(totals: dict, has_calls: bool) -> None:
    """Три ряда по четыре карточки: подписи не обрезаются даже на ноутбуке."""
    rows = [
        [("Расходы", fmt_money(totals["spend_total"])),
         ("Объявлений в день", fmt_int(totals["listings_per_day"])),
         ("Показы", fmt_int(totals["impressions"])),
         ("Просмотры", fmt_int(totals["views"]))],
        [("Контакты", fmt_int(totals["contacts"])),
         ("Звонки", fmt_int(totals["calls"]) if has_calls else DASH),
         ("Цена контакта", fmt_money(totals["cost_per_contact"])),
         ("Цена звонка",
          fmt_money(totals["cost_per_call"]) if has_calls else DASH)],
        [("Показ → просмотр", fmt_pct(totals["cr_impr_views"])),
         ("Просмотр → контакт", fmt_pct(totals["cr_views_contacts"])),
         ("Контакт → звонок",
          fmt_pct(totals["cr_contacts_calls"]) if has_calls else DASH),
         ("Цена просмотра", fmt_money(totals["cost_per_view"], 1))],
    ]
    for row in rows:
        for cell, (label, value) in zip(st.columns(4), row):
            cell.metric(label, value)


def _funnel(totals: dict, has_calls: bool) -> None:
    stages = ["Показы", "Просмотры", "Контакты"]
    values = [totals["impressions"], totals["views"], totals["contacts"]]
    if has_calls:
        stages.append("Звонки")
        values.append(totals["calls"])

    figure = go.Figure(go.Funnel(
        y=stages, x=values,
        textposition="inside", textinfo="value+percent previous",
        marker={"color": PALETTE[:len(stages)]},
        connector={"line": {"color": "#D8DEE9"}},
    ))
    figure.update_layout(height=320, margin={"l": 10, "r": 10, "t": 30, "b": 10},
                         title="Воронка")
    st.plotly_chart(figure, width="stretch")


def _format_table(df: pd.DataFrame, spec: list[tuple]) -> pd.DataFrame:
    """Готовит таблицу к показу: числа форматируются в строки.

    Streamlit печатает NaN в числовой колонке как «None», поэтому денежные и
    процентные значения превращаются в текст с прочерком для пустых.
    """
    out = pd.DataFrame()
    for column, label, formatter in spec:
        if column not in df.columns:
            continue
        out[label] = df[column].map(formatter) if formatter else df[column]
    return out


def _account_comparison(rows: pd.DataFrame, calls_accounts: pd.DataFrame) -> None:
    st.subheader("Сравнение аккаунтов")
    agg = metrics.aggregate(rows, ["account"])
    if calls_accounts.empty:
        agg["calls"] = 0
        agg["deals"] = 0
    else:
        agg = agg.merge(calls_accounts[["account", "calls", "deals"]],
                        on="account", how="left")
        agg[["calls", "deals"]] = agg[["calls", "deals"]].fillna(0)
    agg = metrics.add_derived(agg).sort_values("contacts", ascending=False)

    has_calls = agg["calls"].sum() > 0
    left, right = st.columns(2)
    with left:
        figure = px.bar(agg, x="account", y="contacts", text="contacts",
                        color="account", color_discrete_sequence=PALETTE,
                        labels={"account": "", "contacts": "Контакты"},
                        title="Контакты по аккаунтам")
        figure.update_traces(textposition="outside")
        figure.update_layout(showlegend=False, height=330,
                             margin={"l": 10, "r": 10, "t": 50, "b": 10})
        st.plotly_chart(figure, width="stretch")
    with right:
        metric_col = "cost_per_call" if has_calls else "cost_per_contact"
        title = "Цена звонка" if has_calls else "Цена контакта"
        chart_data = agg.dropna(subset=[metric_col])
        if chart_data.empty:
            st.info(f"{title}: нет аккаунтов с результатом за выбранный период.")
        else:
            figure = px.bar(chart_data, x="account", y=metric_col,
                            text=chart_data[metric_col].map(lambda v: fmt_money(v)),
                            color="account", color_discrete_sequence=PALETTE,
                            labels={"account": "", metric_col: title + ", ₽"},
                            title=f"{title} по аккаунтам")
            figure.update_traces(textposition="outside")
            figure.update_layout(showlegend=False, height=330,
                                 margin={"l": 10, "r": 10, "t": 50, "b": 10})
            st.plotly_chart(figure, width="stretch")

    st.dataframe(_format_table(agg, [
        ("account", "Аккаунт", None), ("days", "Дней", fmt_int),
        ("listings_per_day", "Объявлений в день", fmt_int),
        ("impressions", "Показы", fmt_int), ("views", "Просмотры", fmt_int),
        ("contacts", "Контакты", fmt_int), ("calls", "Звонки", fmt_int),
        ("spend_total", "Расходы", fmt_money),
        ("cr_views_contacts", "Просмотр → контакт", fmt_pct),
        ("cost_per_contact", "Цена контакта", fmt_money),
        ("cost_per_call", "Цена звонка", fmt_money),
    ]), width="stretch", hide_index=True)
    if not has_calls:
        st.caption("Звонки за выбранный период не введены — колонка «Цена звонка» пуста. "
                   "Данные вносятся в разделе «Звонки».")


def _dynamics(rows: pd.DataFrame, calls_days: pd.DataFrame) -> None:
    if rows["stat_date"].nunique() < 2:
        st.caption("Динамика появится, когда в выбранном периоде будет больше "
                   "одного дня с данными.")
        return

    st.subheader("Динамика")
    left, right = st.columns([2, 1.4])
    with left:
        choice = st.radio(
            "Показатель",
            ["Контакты", "Звонки", "Расходы, ₽", "Цена контакта, ₽"],
            horizontal=True, label_visibility="collapsed",
        )
    with right:
        grain = st.radio("Шаг", list(GRANULARITY), horizontal=True,
                         label_visibility="collapsed")

    freq = GRANULARITY[grain]
    daily = rows.copy()
    daily["bucket"] = metrics.bucket(daily["stat_date"], freq)
    agg = metrics.aggregate(daily, ["bucket", "account"])

    if not calls_days.empty:
        by_bucket = calls_days.copy()
        by_bucket["bucket"] = metrics.bucket(by_bucket["stat_date"], freq)
        by_bucket = (by_bucket.groupby(["bucket", "account"], dropna=False)["calls"]
                     .sum().reset_index())
        agg = agg.merge(by_bucket, on=["bucket", "account"], how="left")
        agg["calls"] = agg["calls"].fillna(0)
    else:
        agg["calls"] = 0

    agg = metrics.add_derived(agg).sort_values("bucket")
    column = {v: k for k, v in metrics.RU_LABELS.items()}[choice]
    figure = px.line(agg, x="bucket", y=column, color="account", markers=True,
                     color_discrete_sequence=PALETTE,
                     labels={column: choice, "account": "Аккаунт", "bucket": ""})
    figure.update_layout(height=340, margin={"l": 10, "r": 10, "t": 30, "b": 10},
                         hovermode="x unified")
    figure.update_xaxes(tickformat="%d.%m.%Y" if freq != "M" else "%m.%Y")
    st.plotly_chart(figure, width="stretch")


def _geography(rows: pd.DataFrame) -> None:
    st.subheader("География")
    level = st.radio("Разрез", ["Регион", "Город"], horizontal=True,
                     label_visibility="collapsed")
    column = "region" if level == "Регион" else "city"
    agg = metrics.add_derived(metrics.aggregate(rows, [column]))
    agg = agg.sort_values("spend_total", ascending=False)

    # Длина столбца — потраченные деньги, цвет — сколько контактов они принесли.
    # Две оси на одном графике здесь только путали: контакты измеряются единицами,
    # а расходы — тысячами.
    top = agg.head(15).sort_values("spend_total")
    figure = px.bar(
        top, x="spend_total", y=column, orientation="h",
        color="contacts", color_continuous_scale=["#5A6478", "#F2A93B", "#00A87E"],
        text=top["spend_total"].map(lambda v: fmt_money(v)),
        labels={"spend_total": "Расходы, ₽", column: "", "contacts": "Контакты"},
        title=f"Топ-15 по расходам · цвет — число контактов ({level.lower()})",
        hover_data={"contacts": True, "views": True, "listings_per_day": ":.1f"},
    )
    figure.update_traces(textposition="outside", cliponaxis=False)
    figure.update_layout(height=480, margin={"l": 10, "r": 60, "t": 60, "b": 10})
    st.plotly_chart(figure, width="stretch")

    dead = agg[(agg["contacts"] == 0) & (agg["spend_total"] > 0)]
    if not dead.empty:
        wasted = dead["spend_total"].sum()
        share = wasted / agg["spend_total"].sum() if agg["spend_total"].sum() else 0
        st.warning(
            f"{level} без единого контакта: {len(dead)} из {len(agg)}. "
            f"На них потрачено {fmt_money(wasted)} — {fmt_pct(share)} бюджета.",
            icon="⚠️",
        )

    with st.expander(f"Таблица по всем ({len(agg)})"):
        st.dataframe(_format_table(agg.sort_values("spend_total", ascending=False), [
            (column, level, None),
            ("listings_per_day", "Объявлений в день", fmt_int),
            ("impressions", "Показы", fmt_int), ("views", "Просмотры", fmt_int),
            ("contacts", "Контакты", fmt_int),
            ("spend_total", "Расходы", fmt_money),
            ("cost_per_contact", "Цена контакта", fmt_money),
        ]), width="stretch", hide_index=True)


def _account_comparison_summary(rows: pd.DataFrame) -> None:
    """Доли расходов по аккаунтам рядом с воронкой."""
    agg = metrics.aggregate(rows, ["account"])
    figure = px.pie(agg, names="account", values="spend_total", hole=0.55,
                    color_discrete_sequence=PALETTE, title="Распределение расходов")
    figure.update_traces(textposition="inside", textinfo="percent+label")
    figure.update_layout(height=320, showlegend=False,
                         margin={"l": 10, "r": 10, "t": 30, "b": 10})
    st.plotly_chart(figure, width="stretch")


def dashboard_page(engine) -> None:
    version = data_version()
    bounds = cached_bounds(engine, version)

    st.header("Дашборд")
    if bounds is None:
        st.info("Данных пока нет. Загрузите дневные выгрузки Авито в разделе "
                "«Загрузка выгрузок».")
        return

    first, last = bounds
    start, end = _period_picker(first, last)
    rows = cached_daily(engine, version, start, end)
    if rows.empty:
        st.warning(f"За {metrics.range_label(start, end)} данных нет — "
                   "выберите другой период.")
        return

    filtered = _filters(rows)
    if filtered.empty:
        st.warning("По выбранным фильтрам данных нет.")
        return

    calls = cached_calls(engine, version)
    accounts_in_view = set(filtered["account"].unique())
    calls_accounts = metrics.calls_by_account(calls, start, end)
    calls_accounts = calls_accounts[calls_accounts["account"].isin(accounts_in_view)]
    calls_days = metrics.calls_by_day(calls, start, end)
    calls_days = calls_days[calls_days["account"].isin(accounts_in_view)]

    calls_total = float(calls_accounts["calls"].sum()) if not calls_accounts.empty else 0.0
    has_calls = calls_total > 0
    totals = metrics.funnel_totals(filtered, calls_total)

    st.caption(f"{metrics.range_label(start, end)} · дней с данными: "
               f"{fmt_int(totals['days'])} · строк: {fmt_int(totals['rows'])}")

    st.divider()
    _kpi_row(totals, has_calls)
    st.divider()

    left, right = st.columns([1, 1.3])
    with left:
        _funnel(totals, has_calls)
    with right:
        _account_comparison_summary(filtered)

    st.divider()
    _account_comparison(filtered, calls_accounts)
    st.divider()
    _dynamics(filtered, calls_days)
    st.divider()
    _geography(filtered)

    if st.button("Обновить данные"):
        bump_data_version()
        st.rerun()
