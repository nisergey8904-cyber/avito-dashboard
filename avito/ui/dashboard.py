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
from avito.ui.avitolog import (
    add_batch_marks, add_short_day_marks, inventory_block,
    kassa_block,
)
from avito.ui.common import (
    DASH, PALETTE, bump_data_version, cached_bounds, cached_calls,
    cached_contract, cached_contract_bounds, cached_cpl_targets, cached_daily,
    data_version, fmt_int, fmt_money, fmt_pct,
)

# Календарь живёт под постоянным ключом виджета. Без ключа Streamlit узнаёт
# виджет по параметрам, и смена value после каждого выбора пересоздавала
# календарь — первый клик нового выделения терялся. Кнопки быстрого выбора
# меняют значение через on_click: колбэк выполняется до отрисовки виджета.
PICKER_KEY = "dashboard_period"

# Копия последнего полного интервала. Ключ виджета Streamlit удаляет, когда
# страница не отрисована, а эта копия переживает переход между страницами.
RANGE_KEY = "dashboard_range"

# Быстрый выбор: подпись -> сколько последних дней истории брать (None — всю).
QUICK_RANGES = {"7 дней": 7, "30 дней": 30, "90 дней": 90, "Всё время": None}

GRANULARITY = {"По дням": "D", "По неделям": "W", "По месяцам": "M"}

# С 21.08.2026 файлы собирает AI Avitolog (app/dashboard_export.py): цифры из
# API, регион и город — из его справочника адресов. У части строк гео нет
# (объявление удалено раньше, чем адрес сохранили). Расходы по ним настоящие,
# поэтому строки не выбрасываются, а сводятся в отдельную группу с понятной
# подписью — вместо безымянного «NaN» на графике.
NO_GEO = "Не указан"


def _default_range(first: date, last: date) -> tuple[date, date]:
    return max(first, last - timedelta(days=29)), last


def _clamped(value, first: date, last: date) -> tuple[date, ...] | None:
    """Интервал из сессии, подрезанный под доступные даты, или None.

    Новые данные сдвигают границы календаря, а в сессии может лежать интервал
    за их пределами — date_input на таком значении падает. Недовыбранный
    интервал из одной даты сохраняется как есть, чтобы не сбросить выделение.
    """
    if isinstance(value, date):
        value = (value,)
    if not isinstance(value, (tuple, list)) or not 1 <= len(value) <= 2:
        return None
    if not all(isinstance(day, date) for day in value):
        return None
    clamped = tuple(min(max(day, first), last) for day in value)
    if len(clamped) == 2 and clamped[0] > clamped[1]:
        return None
    return clamped


def _prepare_picker(first: date, last: date) -> None:
    """Готовит значение календаря до его отрисовки в этом перезапуске."""
    current = st.session_state.get(PICKER_KEY)
    fixed = _clamped(current, first, last)
    if fixed is None:
        fixed = (_clamped(st.session_state.get(RANGE_KEY), first, last)
                 or _default_range(first, last))
    if fixed != (tuple(current) if isinstance(current, (tuple, list)) else current):
        st.session_state[PICKER_KEY] = fixed


def _apply_quick(start: date, end: date) -> None:
    st.session_state[PICKER_KEY] = (start, end)
    st.session_state[RANGE_KEY] = (start, end)


def _period_picker(first: date, last: date) -> tuple[date, date]:
    """Календарь с кнопками быстрого выбора. Возвращает границы интервала."""
    _prepare_picker(first, last)
    calendar_cell, quick_cell = st.columns([2, 2])

    with quick_cell:
        st.caption("Быстрый выбор")
        buttons = st.columns(len(QUICK_RANGES))
        for column, (label, days) in zip(buttons, QUICK_RANGES.items()):
            start = (first if days is None
                     else max(first, last - timedelta(days=days - 1)))
            column.button(label, width="stretch", key=f"quick_{label}",
                          on_click=_apply_quick, args=(start, last))

    with calendar_cell:
        chosen = st.date_input(
            "Период", key=PICKER_KEY,
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


SEGMENT_ALL = "Все сегменты"


def _filters(rows: pd.DataFrame,
             extra_accounts: set[str]) -> tuple[list[str], list[str], list[str], str]:
    """Выбранные аккаунты, регионы, предметы и сегмент.

    Аккаунты — из выгрузок и из таблиц AI Avitolog: касса может прийти за
    день, выгрузки за который ещё нет. Регион и предмет есть только в выгрузках.
    """
    accounts = sorted(set(rows["account"].unique() if not rows.empty else [])
                      | extra_accounts)
    left, middle, right, segment_cell = st.columns([2, 1.4, 1.2, 1.1])
    with left:
        chosen_accounts = st.multiselect("Аккаунты", accounts, default=accounts)
    with middle:
        regions = sorted(rows["region"].dropna().unique()) if not rows.empty else []
        chosen_regions = st.multiselect("Регионы", regions, placeholder="Все регионы")
    with right:
        present = set(rows["subject"].unique()) if not rows.empty else set()
        subjects = [s for s in metrics.SUBJECTS if s in present]
        chosen_subjects = st.multiselect(
            "Предметы", subjects, placeholder="Все предметы",
            help="Предмет — по названию объявления: «русск» — русский, "
                 "«физик» — физика, остальное — математика.")
    with segment_cell:
        chosen_segment = st.selectbox(
            "Сегмент", [SEGMENT_ALL, metrics.SEG_MSK, metrics.SEG_REGIONS],
            key="segment_filter",
            help="«Москва + МО» — регион «Москва» или «Московская область» "
                 "(Зеленоград тоже здесь), «Регионы» — любой другой указанный "
                 "регион. Строки без региона входят только во «Все сегменты».")
    return chosen_accounts, chosen_regions, chosen_subjects, chosen_segment


def _merge_bounds(*bounds) -> tuple[date, date] | None:
    found = [b for b in bounds if b is not None]
    if not found:
        return None
    return min(b[0] for b in found), max(b[1] for b in found)


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


def _dynamics(rows: pd.DataFrame, calls_days: pd.DataFrame,
              batches: pd.DataFrame, kassa: pd.DataFrame,
              targets: pd.DataFrame) -> None:
    if rows["stat_date"].nunique() < 2:
        st.caption("Динамика появится, когда в выбранном периоде будет больше "
                   "одного дня с данными.")
        return

    st.subheader("Динамика")
    splits = ["По аккаунтам", "По сегментам"]
    if rows["subject"].nunique() > 1:
        splits.append("По предметам")
    left, right = st.columns([2, 1.4])
    with left:
        # Звонки вносятся по аккаунту, без предмета и географии: в разрезах
        # предметов и сегментов их нет.
        split = st.radio("Разрез", splits, horizontal=True,
                         label_visibility="collapsed", key="dynamics_split")
        by_subject = split == "По предметам"
        by_segment = split == "По сегментам"
        options = ["Контакты", "Расходы, ₽", "Цена контакта, ₽"] if by_subject or by_segment else \
            ["Контакты", "Звонки", "Расходы, ₽", "Цена контакта, ₽"]
        choice = st.radio("Показатель", options, horizontal=True,
                          label_visibility="collapsed")
    with right:
        grain = st.radio("Шаг", list(GRANULARITY), horizontal=True,
                         label_visibility="collapsed")

    freq = GRANULARITY[grain]
    daily = rows.copy()
    daily["bucket"] = metrics.bucket(daily["stat_date"], freq)
    if by_subject:
        _subject_dynamics(daily, choice, freq, batches)
        return
    if by_segment:
        _segment_dynamics(daily, choice, freq, batches, targets)
        return
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
    if freq == "D":
        add_short_day_marks(figure, agg, column, kassa)
    add_batch_marks(figure, batches)
    figure.update_layout(height=340, margin={"l": 10, "r": 10, "t": 30, "b": 10},
                         hovermode="x unified")
    figure.update_xaxes(tickformat="%d.%m.%Y" if freq != "M" else "%m.%Y")
    st.plotly_chart(figure, width="stretch")
    if not batches.empty:
        st.caption("◆ и пунктир — партии изменений ставок: наведите на ромб, "
                   "чтобы увидеть, что меняли и почему.")


def _subject_dynamics(daily: pd.DataFrame, choice: str, freq: str,
                      batches: pd.DataFrame) -> None:
    """Контакты и расходы — столбцами по предметам, цена контакта — линиями."""
    agg = metrics.add_derived(metrics.aggregate(daily, ["bucket", "subject"]))
    column = {v: k for k, v in metrics.RU_LABELS.items()}[choice]
    labels = {column: choice, "subject": "Предмет", "bucket": ""}
    order = {"subject": [s for s in metrics.SUBJECTS if s in set(agg["subject"])]}
    if column == "cost_per_contact":
        figure = px.line(agg.sort_values("bucket"), x="bucket", y=column,
                         color="subject", markers=True, labels=labels,
                         color_discrete_map=SUBJECT_COLORS, category_orders=order)
        figure.update_layout(hovermode="x unified")
    else:
        figure = px.bar(agg, x="bucket", y=column, color="subject", labels=labels,
                        color_discrete_map=SUBJECT_COLORS, category_orders=order)
        figure.update_layout(barmode="stack", hovermode="x unified", bargap=0.15)
    add_batch_marks(figure, batches)
    figure.update_layout(height=340, margin={"l": 10, "r": 10, "t": 30, "b": 10})
    figure.update_xaxes(tickformat="%d.%m.%Y" if freq != "M" else "%m.%Y")
    st.plotly_chart(figure, width="stretch")


GEO_METRICS = ["Расходы", "Контакты", "Конверсия"]

# Конверсия по городу с десятком просмотров — шум: 1 контакт из 3 просмотров
# даёт 33 % и выталкивает наверх случайные точки. Медиана по городам за месяц —
# около 10 просмотров, по регионам — около 150, отсюда порог по умолчанию.
GEO_MIN_VIEWS = 50


# Подсказка одна для всех режимов: значения форматируются заранее, чтобы пустая
# цена контакта печаталась прочерком, а не «NaN».
GEO_HOVER = [("Расходы", "spend_total", fmt_money),
             ("Просмотры", "views", fmt_int),
             ("Контакты", "contacts", fmt_int),
             ("Просмотр → контакт", "cr_views_contacts", fmt_pct),
             ("Цена контакта", "cost_per_contact", fmt_money)]


def _geo_hover(top: pd.DataFrame) -> dict:
    lines = "".join(f"<br>{label}: %{{customdata[{i}]}}"
                    for i, (label, _, _) in enumerate(GEO_HOVER))
    return {
        "customdata": [[fmt(row[col]) for _, col, fmt in GEO_HOVER]
                       for _, row in top.iterrows()],
        "hovertemplate": "<b>%{y}</b>" + lines + "<extra></extra>",
    }


def _geo_chart(agg: pd.DataFrame, column: str, level: str, metric: str,
               min_views: int) -> None:
    """Топ-15 по выбранному показателю, горизонтальные столбцы."""
    where = level.lower()
    if metric == "Расходы":
        # Длина столбца — потраченные деньги, цвет — сколько контактов они
        # принесли. Две оси на одном графике здесь только путали: контакты
        # измеряются единицами, а расходы — тысячами.
        top = agg.nlargest(15, "spend_total").sort_values("spend_total")
        figure = px.bar(
            top, x="spend_total", y=column, orientation="h",
            color="contacts",
            color_continuous_scale=["#5A6478", "#F2A93B", "#00A87E"],
            text=top["spend_total"].map(lambda v: fmt_money(v)),
            labels={"spend_total": "Расходы, ₽", column: "", "contacts": "Контакты"},
            title=f"Топ-15 по расходам · цвет — число контактов ({where})",
        )
    elif metric == "Контакты":
        # Цвет — цена контакта: среди лидеров по контактам сразу видно, какие
        # достались дорого.
        top = agg.nlargest(15, "contacts").sort_values("contacts")
        figure = px.bar(
            top, x="contacts", y=column, orientation="h",
            color="cost_per_contact",
            color_continuous_scale=["#00A87E", "#F2A93B", "#D9534F"],
            text=top["contacts"].map(fmt_int),
            labels={"contacts": "Контакты", column: "",
                    "cost_per_contact": "Цена контакта, ₽"},
            title=f"Топ-15 по контактам · цвет — цена контакта ({where})",
        )
    else:
        eligible = agg[agg["views"] >= min_views].dropna(subset=["cr_views_contacts"])
        if eligible.empty:
            st.info(f"Нет ни одного места ({where}) с {fmt_int(min_views)}+ "
                    "просмотрами — уменьшите порог.")
            return
        top = (eligible.nlargest(15, "cr_views_contacts")
               .sort_values("cr_views_contacts"))
        figure = px.bar(
            top, x="cr_views_contacts", y=column, orientation="h",
            color="contacts",
            color_continuous_scale=["#5A6478", "#F2A93B", "#00A87E"],
            text=top["cr_views_contacts"].map(fmt_pct),
            labels={"cr_views_contacts": "Просмотр → контакт", column: "",
                    "contacts": "Контакты"},
            title=(f"Топ-15 по конверсии просмотр → контакт · от "
                   f"{fmt_int(min_views)} просмотров · цвет — контакты ({where})"),
        )
        figure.update_xaxes(tickformat=".0%")
        views, contacts = agg["views"].sum(), agg["contacts"].sum()
        if views:
            average = contacts / views
            figure.add_vline(x=average, line_dash="dash", line_color="#5A6478",
                             annotation_text=f"в среднем {fmt_pct(average)}",
                             annotation_position="top")

    figure.update_traces(textposition="outside", cliponaxis=False,
                         **_geo_hover(top))
    figure.update_layout(height=480, margin={"l": 10, "r": 60, "t": 60, "b": 10},
                         separators=", ")
    st.plotly_chart(figure, width="stretch")
    if metric == "Конверсия":
        st.caption(f"В график попали {len(eligible)} из {len(agg)} — у остальных "
                   f"меньше {fmt_int(min_views)} просмотров, их конверсия случайна. "
                   "Пунктир — средняя конверсия по всем выбранным данным.")


def _geography(rows: pd.DataFrame) -> None:
    st.subheader("География")
    level_cell, metric_cell, threshold_cell = st.columns(
        [1, 1.6, 1], vertical_alignment="bottom")
    with level_cell:
        level = st.radio("Разрез", ["Регион", "Город"], horizontal=True,
                         label_visibility="collapsed")
    with metric_cell:
        metric = st.radio("Показатель на графике", GEO_METRICS, horizontal=True,
                          label_visibility="collapsed", key="geo_metric")
    min_views = GEO_MIN_VIEWS
    if metric == "Конверсия":
        with threshold_cell:
            min_views = int(st.number_input(
                "Минимум просмотров", min_value=0, value=GEO_MIN_VIEWS, step=10,
                key="geo_min_views",
                help="Места с меньшим числом просмотров в график конверсии "
                     "не попадают: на малых числах процент случаен.",
            ))
    column = "region" if level == "Регион" else "city"
    agg = metrics.add_derived(metrics.aggregate(rows, [column]))
    agg = agg.sort_values("spend_total", ascending=False)

    _geo_chart(agg, column, level, metric, min_views)

    unknown = agg[agg[column] == NO_GEO]
    if not unknown.empty:
        spent = float(unknown["spend_total"].iloc[0])
        total = agg["spend_total"].sum()
        st.caption(
            f"«{NO_GEO}» — объявления, для которых {level.lower()} не удалось "
            f"определить: {fmt_int(unknown['rows'].iloc[0])} строк, {fmt_money(spent)} "
            f"({fmt_pct(spent / total if total else 0)} расходов). Обычно это "
            "объявления, удалённые до того, как их адрес попал в справочник."
        )

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
            ("cr_views_contacts", "Просмотр → контакт", fmt_pct),
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

    st.header("Дашборд")

    bounds = _merge_bounds(cached_bounds(engine, version),
                           cached_contract_bounds(engine, version))
    if bounds is None:
        st.info("Данных пока нет. Загрузите дневные выгрузки Авито в разделе "
                "«Загрузка выгрузок».")
        return

    first, last = bounds
    start, end = _period_picker(first, last)
    rows = cached_daily(engine, version, start, end)
    contract = cached_contract(engine, version, start, end)
    contract_accounts = {account for frame in contract.values() if not frame.empty
                         for account in frame["account"].unique()}
    if rows.empty and not contract_accounts:
        st.warning(f"За {metrics.range_label(start, end)} данных нет — "
                   "выберите другой период.")
        return
    if not rows.empty:
        rows[["region", "city"]] = rows[["region", "city"]].fillna(NO_GEO)
        rows["subject"] = metrics.subject_of(rows["title"])
        rows["segment"] = metrics.segment_of(rows["region"], NO_GEO)

    chosen_accounts, chosen_regions, chosen_subjects, chosen_segment = _filters(
        rows, contract_accounts)
    contract = {name: frame[frame["account"].isin(chosen_accounts)]
                if not frame.empty else frame
                for name, frame in contract.items()}
    filtered = rows
    if not rows.empty:
        mask = rows["account"].isin(chosen_accounts)
        if chosen_regions:
            mask &= rows["region"].isin(chosen_regions)
        if chosen_subjects:
            mask &= rows["subject"].isin(chosen_subjects)
        if chosen_segment != SEGMENT_ALL:
            mask &= rows["segment"] == chosen_segment
        filtered = rows[mask].copy()
    if chosen_regions or chosen_subjects or chosen_segment != SEGMENT_ALL:
        st.caption("Фильтры по регионам, предметам и сегменту действуют только на "
                   "блоки по выгрузкам: в кассе и инвентаре их нет.")
    targets = cached_cpl_targets(engine, version)

    if filtered.empty:
        st.warning("Выгрузок статистики объявлений по выбранным фильтрам нет — "
                   "ниже только данные AI Avitolog.")
    else:
        calls_accounts, calls_days = _calls_in_view(engine, version, filtered, start, end)
        _listing_overview(filtered, calls_accounts, start, end)
        _segments(filtered, targets, end)
        _subjects(filtered)

    st.divider()
    kassa_block(contract["kassa"])

    if not filtered.empty:
        st.divider()
        _account_comparison(filtered, calls_accounts)
        st.divider()
        _dynamics(filtered, calls_days,
                  contract["batches"], contract["kassa"], targets)

    st.divider()
    inventory_block(contract["inventory"])

    if not filtered.empty:
        st.divider()
        _geography(filtered)

    if st.button("Обновить данные"):
        bump_data_version()
        st.rerun()


def _calls_in_view(engine, version: int, filtered: pd.DataFrame,
                   start: date, end: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Звонки за период по аккаунтам и по дням — только для видимых аккаунтов."""
    calls = cached_calls(engine, version)
    accounts_in_view = set(filtered["account"].unique())
    calls_accounts = metrics.calls_by_account(calls, start, end)
    calls_accounts = calls_accounts[calls_accounts["account"].isin(accounts_in_view)]
    calls_days = metrics.calls_by_day(calls, start, end)
    calls_days = calls_days[calls_days["account"].isin(accounts_in_view)]
    return calls_accounts, calls_days


def _listing_overview(filtered: pd.DataFrame, calls_accounts: pd.DataFrame,
                      start: date, end: date) -> None:
    """Карточки, воронка и доли расходов по выгрузкам."""
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


SUBJECT_COLORS = {metrics.MATH: PALETTE[0], "Русский": PALETTE[2], "Физика": PALETTE[1]}
SUBJECT_ALL = "Все карточки"
SUBJECT_FRESH = f"Только новые (с {metrics.FRESH_FROM:%d.%m})"


def _per_hundred(value) -> str:
    """Контактов на 100 объявлений в день: два знака, иначе всё округлится к 1."""
    if value is None or pd.isna(value):
        return DASH
    return f"{value:.2f}".replace(".", ",")


def _subject_frame(rows: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    agg = metrics.add_derived(metrics.aggregate(rows, by))
    # Контакт на 100 объявлений в день = контакты ÷ строки выгрузки × 100:
    # одна строка — одно объявление за один день.
    agg["per_hundred"] = 100 * agg["contacts"] / agg["rows"].where(agg["rows"] > 0)
    order = {name: i for i, name in enumerate(metrics.SUBJECTS)}
    return agg.sort_values("subject", key=lambda s: s.map(order))


def _subjects(rows: pd.DataFrame) -> None:
    """Карточка на предмет: сколько контактов, почём и с какой отдачей."""
    present = set(rows["subject"].unique())
    if present == {metrics.MATH}:
        return  # до первого залива второго предмета сравнивать нечего

    st.divider()
    head, mode_cell = st.columns([1, 1.4], vertical_alignment="bottom")
    head.subheader("По предметам")
    mode = mode_cell.radio("Какие карточки", [SUBJECT_ALL, SUBJECT_FRESH],
                           horizontal=True, label_visibility="collapsed",
                           key="subject_mode")
    data = rows
    if mode == SUBJECT_FRESH:
        published = pd.to_datetime(rows["first_published"], errors="coerce")
        data = rows[published >= metrics.FRESH_FROM]
        if data.empty:
            st.info(f"Объявлений, впервые опубликованных с "
                    f"{metrics.FRESH_FROM:%d.%m.%Y}, в выбранном периоде нет "
                    "(или в выгрузках не заполнена дата первой публикации).")
            return

    agg = _subject_frame(data, ["subject"])
    total = agg["contacts"].sum()
    for cell, row in zip(st.columns(len(agg)), agg.to_dict("records")):
        name = row["subject"]
        since = metrics.SUBJECT_START.get(name)
        with cell.container(border=True):
            st.markdown(
                f"<span style='color:{SUBJECT_COLORS.get(name, PALETTE[3])}'>●</span> "
                f"**{name}** · {fmt_int(row['listings_per_day'])} объявл. в день"
                + (f" · с {since:%d.%m}" if since is not None else ""),
                unsafe_allow_html=True)
            share = row["contacts"] / total if total else None
            st.metric(f"Контакты · {fmt_pct(share, 0)} всех", fmt_int(row["contacts"]))
            st.markdown(
                f"**{fmt_money(row['cost_per_contact'])}** за контакт  \n"
                f"Просмотр → контакт {fmt_pct(row['cr_views_contacts'])} · "
                f"{_per_hundred(row['per_hundred'])} на 100 объявл. в день")

    note = ("Новые карточки дают меньше контактов, чем старые, а математика в "
            "среднем старше. Сравнивать предметы честно в режиме "
            f"«{SUBJECT_FRESH}» — по дате первой публикации.")
    if mode == SUBJECT_FRESH:
        note = (f"Только объявления, впервые опубликованные с "
                f"{metrics.FRESH_FROM:%d.%m.%Y}: так предметы одного возраста.")
    st.caption(note)

    with st.expander("Аккаунт × предмет"):
        by = _subject_frame(data, ["account", "subject"])
        by = by.sort_values(["account", "subject"],
                            key=lambda s: s.map({n: i for i, n in enumerate(metrics.SUBJECTS)})
                            if s.name == "subject" else s)
        st.dataframe(_format_table(by, [
            ("account", "Аккаунт", None), ("subject", "Предмет", None),
            ("listings_per_day", "Объявлений в день", fmt_int),
            ("views", "Просмотры", fmt_int), ("contacts", "Контакты", fmt_int),
            ("spend_total", "Расходы", fmt_money),
            ("cost_per_contact", "Цена контакта", fmt_money),
            ("cr_views_contacts", "Просмотр → контакт", fmt_pct),
            ("per_hundred", "На 100 объявл. в день", _per_hundred),
        ]), width="stretch", hide_index=True)


# --- Сегменты: Москва + МО и регионы, каждый со своей планкой цены контакта ---

SEGMENT_COLORS = {metrics.SEG_MSK: PALETTE[0], metrics.SEG_REGIONS: PALETTE[2],
                  metrics.SEG_UNKNOWN: "#9AA3B2"}
OVER_COLOR = "#D9534F"   # цена контакта выше планки
UNDER_COLOR = "#00A87E"  # не выше планки


def _segment_frame(rows: pd.DataFrame, by: list[str], targets: pd.DataFrame,
                   day) -> pd.DataFrame:
    """Метрики по сегменту (и, если задано, аккаунту) с планкой на день day.

    Доли в расходе и контактах — внутри группы без сегмента: для блока это весь
    выбранный срез, для таблицы «аккаунт × сегмент» — аккаунт.
    """
    agg = metrics.add_derived(metrics.aggregate(rows, by))
    agg["target"] = agg["segment"].map(lambda s: metrics.target_on(targets, s, day))
    agg["target"] = pd.to_numeric(agg["target"], errors="coerce")
    agg["deviation"] = agg["cost_per_contact"] / agg["target"] - 1
    parent = [c for c in by if c != "segment"]
    for col, share in (("spend_total", "share_spend"), ("contacts", "share_contacts")):
        total = (agg.groupby(parent)[col].transform("sum") if parent
                 else pd.Series(agg[col].sum(), index=agg.index))
        agg[share] = agg[col] / total.where(total > 0)
    order = {name: i for i, name in enumerate(metrics.SEGMENTS)}
    return agg.sort_values(parent + ["segment"],
                           key=lambda s: s.map(order) if s.name == "segment" else s)


def _fmt_deviation(value) -> str:
    if value is None or pd.isna(value):
        return DASH
    return ("+" if value > 0 else "") + fmt_pct(value, 0)


SEGMENT_TABLE = [
    ("segment", "Сегмент", None),
    ("views", "Просмотры", fmt_int), ("contacts", "Контакты", fmt_int),
    ("cr_views_contacts", "Просмотр → контакт", fmt_pct),
    ("spend_total", "Расходы", fmt_money),
    ("cost_per_contact", "Цена контакта", fmt_money),
    ("target", "Целевая цена", fmt_money),
    ("deviation", "Отклонение от целевой", _fmt_deviation),
    ("share_spend", "Доля в расходе", lambda v: fmt_pct(v, 0)),
    ("share_contacts", "Доля в контактах", lambda v: fmt_pct(v, 0)),
]


def _segment_styled(agg: pd.DataFrame, spec: list[tuple]):
    """Таблица с красной/зелёной ценой контакта и отклонением."""
    shown = _format_table(agg, spec).reset_index(drop=True)
    deviation = agg["deviation"].reset_index(drop=True)

    def paint(frame: pd.DataFrame) -> pd.DataFrame:
        css = pd.DataFrame("", index=frame.index, columns=frame.columns)
        for column in ("Цена контакта", "Отклонение от целевой"):
            if column not in css.columns:
                continue
            css[column] = [
                "" if pd.isna(d) else
                f"color: {OVER_COLOR if d > 0 else UNDER_COLOR}; font-weight: 600"
                for d in deviation]
        return css

    return shown.style.apply(paint, axis=None)


def _segments(rows: pd.DataFrame, targets: pd.DataFrame, end: date) -> None:
    """Блок «Сегменты»: каждый сегмент рядом со своей планкой цены контакта."""
    st.divider()
    st.subheader("Сегменты")
    agg = _segment_frame(rows, ["segment"], targets, end)

    main = agg[agg["segment"].isin(metrics.SEGMENT_CODES)]
    if not main.empty:
        for cell, row in zip(st.columns(len(main)), main.to_dict("records")):
            with cell.container(border=True):
                st.markdown(
                    f"<span style='color:{SEGMENT_COLORS[row['segment']]}'>●</span> "
                    f"**{row['segment']}** · {fmt_pct(row['share_spend'], 0)} расхода, "
                    f"{fmt_pct(row['share_contacts'], 0)} контактов",
                    unsafe_allow_html=True)
                delta = None
                if not pd.isna(row["deviation"]):
                    delta = (f"{_fmt_deviation(row['deviation'])} к планке "
                             f"{fmt_money(row['target'])}")
                st.metric("Цена контакта", fmt_money(row["cost_per_contact"]),
                          delta=delta, delta_color="inverse")
                st.caption(f"Контакты {fmt_int(row['contacts'])} · расход "
                           f"{fmt_money(row['spend_total'])} · просмотр → контакт "
                           f"{fmt_pct(row['cr_views_contacts'])}")

    st.dataframe(_segment_styled(agg, SEGMENT_TABLE), width="stretch",
                 hide_index=True)

    source = ("из таблицы планок AI Avitolog" if metrics.targets_from_table(targets)
              else "по умолчанию — таблица планок AI Avitolog пока пуста")
    st.caption(
        f"Целевая цена — на {end:%d.%m.%Y}, последний день периода ({source}). "
        "Цена выше целевой — красным, не выше — зелёным. Общая цена контакта в "
        "карточках выше — среднее по сегментам, с планкой её не сравниваем: она "
        "зависит от доли расхода в столице. Касса по сегментам не делится — "
        "здесь только расход по объявлениям.")

    with st.expander("Аккаунт × сегмент"):
        by = _segment_frame(rows, ["account", "segment"], targets, end)
        spec = [("account", "Аккаунт", None)] + [
            (col, "Доля в расходе аккаунта" if col == "share_spend" else
             "Доля в контактах аккаунта" if col == "share_contacts" else label, fmt)
            for col, label, fmt in SEGMENT_TABLE]
        st.dataframe(_segment_styled(by, spec), width="stretch", hide_index=True)


def _bucket_end(buckets: pd.Series, freq: str) -> pd.Series:
    """Последний день шага: на него берётся планка в динамике."""
    if freq == "W":
        return buckets + pd.Timedelta(days=6)
    if freq == "M":
        return buckets + pd.offsets.MonthEnd(0)
    return buckets


def _segment_dynamics(daily: pd.DataFrame, choice: str, freq: str,
                      batches: pd.DataFrame, targets: pd.DataFrame) -> None:
    """Контакты и расходы — столбцами по сегментам, цена контакта — линиями с планкой."""
    agg = metrics.add_derived(metrics.aggregate(daily, ["bucket", "segment"]))
    column = {v: k for k, v in metrics.RU_LABELS.items()}[choice]
    labels = {column: choice, "segment": "Сегмент", "bucket": ""}
    present = set(agg["segment"])
    if column == "cost_per_contact":
        # У «Не указан» планки нет, а его цена контакта на малых числах шумит.
        agg = agg[agg["segment"].isin(metrics.SEGMENT_CODES)]
        order = {"segment": [s for s in metrics.SEGMENT_CODES if s in present]}
        figure = px.line(agg.sort_values("bucket"), x="bucket", y=column,
                         color="segment", markers=True, labels=labels,
                         color_discrete_map=SEGMENT_COLORS, category_orders=order)
        buckets = pd.Series(sorted(agg["bucket"].unique()))
        ends = _bucket_end(buckets, freq)
        # Каждый уровень рисуется на полшага в обе стороны от точки: так видна
        # и ступень последнего шага, которую line_shape="hv" обрезал бы.
        half = pd.Timedelta(hours={"D": 12, "W": 84, "M": 360}[freq])
        for segment in order["segment"]:
            xs, levels = [], []
            for point, day in zip(buckets, ends):
                level = metrics.target_on(targets, segment, day)
                xs += [point - half, point + half]
                levels += [level, level]
            figure.add_trace(go.Scatter(
                x=xs, y=levels, mode="lines",
                name=f"Планка: {segment}",
                line={"color": SEGMENT_COLORS[segment], "dash": "dash", "width": 1.5},
                hovertemplate=f"Планка {segment}: %{{y:.0f}} ₽<extra></extra>",
            ))
        figure.update_layout(hovermode="x unified")
    else:
        order = {"segment": [s for s in metrics.SEGMENTS if s in present]}
        figure = px.bar(agg, x="bucket", y=column, color="segment", labels=labels,
                        color_discrete_map=SEGMENT_COLORS, category_orders=order)
        figure.update_layout(barmode="stack", hovermode="x unified", bargap=0.15)
    add_batch_marks(figure, batches)
    figure.update_layout(height=340, margin={"l": 10, "r": 10, "t": 30, "b": 10})
    figure.update_xaxes(tickformat="%d.%m.%Y" if freq != "M" else "%m.%Y")
    st.plotly_chart(figure, width="stretch")
    if column == "cost_per_contact":
        step = {"W": "последний день недели", "M": "последний день месяца"}.get(freq)
        st.caption("Пунктир — целевая цена сегмента"
                   + (f" на {step}" if step else "")
                   + ". Строки без региона в линии цены не входят.")
