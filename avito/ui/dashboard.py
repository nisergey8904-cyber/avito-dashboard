"""Главный экран: воронка, экономика и сравнение аккаунтов."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from avito import metrics
from avito.ui.common import (
    DASH, bump_data_version, cached_calls, cached_listings, data_version,
    fmt_int, fmt_money, fmt_pct,
)

PALETTE = ["#2E6FD9", "#00A87E", "#F2A93B", "#D9534F", "#7B5AA6", "#4BB3C4"]


def _period_options(listings: pd.DataFrame) -> pd.DataFrame:
    periods = (listings[["period_start", "period_end"]]
               .drop_duplicates()
               .sort_values("period_start", ascending=False)
               .reset_index(drop=True))
    periods["label"] = [metrics.period_label(s, e)
                        for s, e in zip(periods.period_start, periods.period_end)]
    return periods


def _filters(listings: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    periods = _period_options(listings)
    accounts = sorted(listings["account"].unique())

    left, middle, right = st.columns([2, 2, 1.4])
    with left:
        chosen_labels = st.multiselect(
            "Период выгрузки", periods["label"].tolist(),
            default=periods["label"].tolist(),
            help="По умолчанию — все загруженные периоды.",
        )
    with middle:
        chosen_accounts = st.multiselect("Аккаунты", accounts, default=accounts)
    with right:
        regions = sorted(listings["region"].dropna().unique())
        chosen_regions = st.multiselect("Регионы", regions,
                                        placeholder="Все регионы")

    selected = periods[periods["label"].isin(chosen_labels)]
    mask = (
        listings["period_start"].isin(selected["period_start"])
        & listings["account"].isin(chosen_accounts)
    )
    if chosen_regions:
        mask &= listings["region"].isin(chosen_regions)
    return listings[mask].copy(), selected


def _calls_for(listings: pd.DataFrame, calls: pd.DataFrame) -> pd.DataFrame:
    """Звонки, попавшие в периоды и аккаунты отфильтрованной выборки."""
    if listings.empty:
        return pd.DataFrame(columns=["account_id", "period_start", "period_end",
                                     "calls", "deals"])
    periods = (listings[["account_id", "account", "period_start", "period_end"]]
               .drop_duplicates())
    per_period = metrics.calls_by_period(calls, periods.drop(columns="account"))
    return per_period.merge(periods[["account_id", "account"]].drop_duplicates(),
                            on="account_id", how="left")


def _kpi_row(totals: dict, has_calls: bool) -> None:
    """Три ряда по четыре карточки: подписи не обрезаются даже на ноутбуке."""
    rows = [
        [("Расходы", fmt_money(totals["spend_total"])),
         ("Объявлений", fmt_int(totals["listings"])),
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


def _account_comparison(listings: pd.DataFrame, calls_periods: pd.DataFrame) -> None:
    st.subheader("Сравнение аккаунтов")
    agg = metrics.aggregate(listings, ["account"])
    calls_by_account = (calls_periods.groupby("account", dropna=False)[["calls", "deals"]]
                        .sum().reset_index()
                        if not calls_periods.empty else pd.DataFrame())
    if calls_by_account.empty:
        agg["calls"] = 0
        agg["deals"] = 0
    else:
        agg = agg.merge(calls_by_account, on="account", how="left")
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
        ("account", "Аккаунт", None), ("listings", "Объявлений", fmt_int),
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


def _dynamics(listings: pd.DataFrame, calls_periods: pd.DataFrame) -> None:
    periods_count = listings[["period_start", "period_end"]].drop_duplicates().shape[0]
    if periods_count < 2:
        st.caption("Динамика появится, когда будет загружено больше одного периода.")
        return

    st.subheader("Динамика по периодам")
    agg = metrics.aggregate(listings, ["period_start", "period_end", "account"])
    if not calls_periods.empty:
        agg = agg.merge(
            calls_periods[["account", "period_start", "period_end", "calls"]],
            on=["account", "period_start", "period_end"], how="left")
        agg["calls"] = agg["calls"].fillna(0)
    else:
        agg["calls"] = 0
    agg = metrics.add_derived(agg)
    agg["Период"] = [metrics.period_label(s, e)
                     for s, e in zip(agg.period_start, agg.period_end)]
    agg = agg.sort_values("period_start")

    choice = st.radio(
        "Показатель",
        ["Контакты", "Звонки", "Расходы, ₽", "Цена контакта, ₽"],
        horizontal=True, label_visibility="collapsed",
    )
    column = {v: k for k, v in metrics.RU_LABELS.items()}[choice]
    figure = px.line(agg, x="Период", y=column, color="account", markers=True,
                     color_discrete_sequence=PALETTE,
                     labels={column: choice, "account": "Аккаунт"})
    figure.update_layout(height=340, margin={"l": 10, "r": 10, "t": 30, "b": 10})
    st.plotly_chart(figure, width="stretch")


def _geography(listings: pd.DataFrame) -> None:
    st.subheader("География")
    level = st.radio("Разрез", ["Регион", "Город"], horizontal=True,
                     label_visibility="collapsed")
    column = "region" if level == "Регион" else "city"
    agg = metrics.add_derived(metrics.aggregate(listings, [column]))
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
        hover_data={"contacts": True, "views": True, "listings": True},
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
            (column, level, None), ("listings", "Объявлений", fmt_int),
            ("impressions", "Показы", fmt_int), ("views", "Просмотры", fmt_int),
            ("contacts", "Контакты", fmt_int),
            ("spend_total", "Расходы", fmt_money),
            ("cost_per_contact", "Цена контакта", fmt_money),
        ]), width="stretch", hide_index=True)


def dashboard_page(engine) -> None:
    version = data_version()
    listings = cached_listings(engine, version)
    calls = cached_calls(engine, version)

    st.header("Дашборд")
    if listings.empty:
        st.info("Данных пока нет. Загрузите выгрузки Авито в разделе «Загрузка выгрузок».")
        return

    filtered, selected_periods = _filters(listings)
    if filtered.empty or selected_periods.empty:
        st.warning("По выбранным фильтрам данных нет.")
        return

    calls_periods = _calls_for(filtered, calls)
    calls_total = float(calls_periods["calls"].sum()) if not calls_periods.empty else 0.0
    has_calls = calls_total > 0
    totals = metrics.funnel_totals(filtered, calls_total)

    st.divider()
    _kpi_row(totals, has_calls)
    st.divider()

    left, right = st.columns([1, 1.3])
    with left:
        _funnel(totals, has_calls)
    with right:
        _account_comparison_summary(filtered, calls_periods)

    st.divider()
    _account_comparison(filtered, calls_periods)
    st.divider()
    _dynamics(filtered, calls_periods)
    st.divider()
    _geography(filtered)

    if st.button("Обновить данные"):
        bump_data_version()
        st.rerun()


def _account_comparison_summary(listings: pd.DataFrame,
                                calls_periods: pd.DataFrame) -> None:
    """Доли расходов по аккаунтам рядом с воронкой."""
    agg = metrics.aggregate(listings, ["account"])
    figure = px.pie(agg, names="account", values="spend_total", hole=0.55,
                    color_discrete_sequence=PALETTE, title="Распределение расходов")
    figure.update_traces(textposition="inside", textinfo="percent+label")
    figure.update_layout(height=320, showlegend=False,
                         margin={"l": 10, "r": 10, "t": 30, "b": 10})
    st.plotly_chart(figure, width="stretch")
