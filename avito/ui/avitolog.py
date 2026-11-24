"""Блоки по данным AI Avitolog: касса, инвентарь, партии ставок.

Строки пишет проект AI Avitolog (см. CONTRACT.md), здесь они только рисуются.
Чаты и план в чатах дашборд не показывает: считаем только контакты.
Нет строки — нет данных, а не ноль: пропущенный день остаётся разрывом на
графике, пустая колонка — прочерком.
"""

from __future__ import annotations

from datetime import timedelta
from html import escape

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from avito.ui.common import PALETTE, fmt_int, fmt_money, fmt_pct

GREEN, YELLOW, RED, GREY = "#00A87E", "#F2A93B", "#D9534F", "#A0A7B4"

# verdict_code -> (короткая подпись, цвет). Порядок — порядок легенды.
VERDICTS = {
    "ok": ("Догорел к полуночи", GREEN),
    "burned": ("Выгорел в 21–22", YELLOW),
    "early": ("Выгорел до 21:00", RED),
    "night": ("Горел ночью", RED),
    "no_deposit": ("Без внесений", GREY),
    "no_chats": ("Нет чатов", GREY),
    "unknown": ("Мало данных", GREY),
}

# Кластеры объявлений: колонка -> (подпись, цвет). В сумме дают active.
CLUSTERS = {
    "cl_core": ("Ядро", GREEN),
    "cl_core_expensive": ("Ядро-дорогое", YELLOW),
    "cl_single": ("Разовые", "#7B5AA6"),
    "cl_start": ("Старт", "#2E6FD9"),
    "cl_untested": ("Непроверенные", GREY),
    "cl_empty": ("Пустые", RED),
}

MSK = timedelta(hours=3)
MARGIN = {"l": 10, "r": 10, "t": 40, "b": 10}


def _no_data(block: str) -> None:
    st.info(f"{block}: данных пока нет — AI Avitolog ещё не записал строки.")


def _freshness(*frames: pd.DataFrame) -> str:
    """Когда писатель последний раз записал строки — loaded_at хранится в UTC."""
    stamps = [df["loaded_at"].max() for df in frames
              if not df.empty and "loaded_at" in df.columns]
    stamps = [s for s in stamps if pd.notna(s)]
    if not stamps:
        return ""
    moment = max(stamps)
    if moment.tzinfo is not None:
        moment = moment.tz_convert("UTC").tz_localize(None)
    return f"записано {moment + MSK:%d.%m в %H:%M} МСК"


def _text(value) -> str:
    """Текст из ячейки; пустая ячейка pandas (None или NaN) — пустая строка."""
    return "" if value is None or pd.isna(value) else str(value)


def _gapped_lines(figure: go.Figure, frame: pd.DataFrame, value: str,
                  days: pd.DatetimeIndex, hover: str) -> None:
    """Линия на аккаунт; дни без строки остаются разрывом, а не соединяются."""
    wide = frame.pivot_table(index="stat_date", columns="account", values=value,
                             aggfunc="sum").reindex(days)
    for i, account in enumerate(wide.columns):
        figure.add_trace(go.Scatter(
            x=wide.index, y=wide[account], name=account, mode="lines+markers",
            line={"color": PALETTE[i % len(PALETTE)]}, connectgaps=False,
            hovertemplate=f"%{{x|%d.%m}} · {escape(account)}: {hover}<extra></extra>",
        ))


# --- касса -------------------------------------------------------------------

def _badge(code: str) -> str:
    label, color = VERDICTS.get(code, (code, GREY))
    return (f"<span style='background:{color};color:#fff;border-radius:6px;"
            f"padding:2px 8px;font-size:0.8rem'>{escape(label)}</span>")


def kassa_block(kassa: pd.DataFrame) -> None:
    st.subheader("Касса")
    if kassa.empty:
        _no_data("Касса")
        return

    latest = kassa["stat_date"].max()
    st.caption(f"Хватило ли денег и дожил ли аванс до полуночи · последний день "
               f"{latest:%d.%m.%Y} · {_freshness(kassa)}")

    day = kassa[kassa["stat_date"] == latest].sort_values("account")
    records = day.to_dict("records")
    for start in range(0, len(records), 4):
        for cell, row in zip(st.columns(4), records[start:start + 4]):
            with cell.container(border=True):
                st.markdown(f"**{escape(row['account'])}**<br>{_badge(row['verdict_code'])}",
                            unsafe_allow_html=True)
                st.caption(_text(row["verdict"]))
                st.markdown(
                    f"Внесено **{fmt_money(row['deposits_rub'])}** "
                    f"({fmt_int(row['deposits_count'])})  \n"
                    f"Расход **{fmt_money(row['spend_rub'])}**  \n"
                    f"Остаток на ночь **{fmt_money(row['left_rub'])}**"
                )
                details = []
                if _text(row["deposits_detail"]):
                    details.append(f"внесения: {_text(row['deposits_detail'])}")
                if pd.notna(row["balance_rub"]):
                    details.append(f"кошелёк утром {fmt_money(row['balance_rub'])}")
                if details:
                    st.caption(" · ".join(details))
                if _text(row["note"]):
                    st.warning(_text(row["note"]), icon="📝")

    _verdict_matrix(kassa)

    with st.expander(f"Касса по дням ({len(kassa)})"):
        table = kassa.sort_values(["stat_date", "account"], ascending=[False, True])
        st.dataframe(pd.DataFrame({
            "День": table["stat_date"].dt.strftime("%d.%m.%Y"),
            "Аккаунт": table["account"],
            "Внесено": table["deposits_rub"].map(fmt_money),
            "Внесений": table["deposits_count"].map(fmt_int),
            "Когда": table["deposits_detail"],
            "Расход": table["spend_rub"].map(fmt_money),
            "Остаток": table["left_rub"].map(fmt_money),
            "Сутки": table["full_day"].map({1: "полные", 0: "неполные"}),
            "Вердикт": table["verdict"],
            "Пометка": table["note"].fillna(""),
        }), width="stretch", hide_index=True)


def _verdict_matrix(kassa: pd.DataFrame) -> None:
    """Дни × аккаунты, клетка окрашена вердиктом. Нет строки — пустая клетка."""
    figure = go.Figure()
    for code, (label, color) in VERDICTS.items():
        part = kassa[kassa["verdict_code"] == code]
        if part.empty:
            continue
        figure.add_trace(go.Scatter(
            x=part["stat_date"], y=part["account"], mode="markers", name=label,
            marker={"symbol": "square", "size": 18, "color": color},
            customdata=part[["verdict", "deposits_rub", "spend_rub", "left_rub"]]
            .assign(full=part["full_day"].map({1: "", 0: " · сутки неполные"})),
            hovertemplate=("%{x|%d.%m} · %{y}<br>%{customdata[0]}%{customdata[4]}"
                           "<br>внесено %{customdata[1]:,.0f} ₽ · расход "
                           "%{customdata[2]:,.0f} ₽ · остаток %{customdata[3]:,.0f} ₽"
                           "<extra></extra>"),
        ))
    accounts = sorted(kassa["account"].unique())
    figure.update_layout(height=110 + 34 * len(accounts), margin=MARGIN,
                         legend={"orientation": "h", "y": -0.25},
                         title="Вердикт по дням")
    figure.update_yaxes(categoryorder="array", categoryarray=accounts[::-1])
    figure.update_xaxes(tickformat="%d.%m")
    st.plotly_chart(figure, width="stretch")


# --- инвентарь ---------------------------------------------------------------

def inventory_block(inventory: pd.DataFrame) -> None:
    st.subheader("Инвентарь")
    if inventory.empty:
        _no_data("Инвентарь")
        st.caption("Снимок задним числом не восстанавливается: ряд начнётся с "
                   "первого дня записи.")
        return

    latest = (inventory.sort_values("snap_date")
              .groupby("avito_id", as_index=False).tail(1)
              .sort_values("account"))
    dates = latest["snap_date"].unique()
    when = (f"снимок {pd.Timestamp(dates[0]):%d.%m.%Y}" if len(dates) == 1
            else "последние снимки по каждому аккаунту")
    st.caption(f"Сколько объявлений работает, сколько пустых, что истекает · "
               f"{when} · {_freshness(inventory)}")

    active = latest["active"].sum()
    spend = latest["spend_28d_rub"].sum()
    wasted = latest["spend_28d_no_contact_rub"].sum()
    figures = [
        [("Активных", fmt_int(active)),
         ("На ставке", fmt_int(latest["on_bid"].sum())),
         (f"Пустые, {fmt_pct(latest['cl_empty'].sum() / active if active else None)}",
          fmt_int(latest["cl_empty"].sum())),
         ("Истекают за 7 дней", fmt_int(latest["expiring_7d"].sum()))],
        [("Новых за сутки", fmt_int(latest["new_1d"].sum())),
         ("Ядро", fmt_int(latest["cl_core"].sum())),
         ("Расход за 28 дней", fmt_money(spend)),
         (f"Из него без контактов, {fmt_pct(wasted / spend if spend else None)}",
          fmt_money(wasted))],
    ]
    for line in figures:
        for cell, (label, value) in zip(st.columns(4), line):
            cell.metric(label, value)

    left, right = st.columns(2)
    with left:
        figure = go.Figure()
        for column, (label, color) in CLUSTERS.items():
            figure.add_trace(go.Bar(
                y=latest["account"], x=latest[column], name=label, orientation="h",
                marker={"color": color},
                hovertemplate=f"%{{y}} · {label}: %{{x}}<extra></extra>",
            ))
        figure.update_layout(barmode="stack", height=120 + 46 * len(latest),
                             margin=MARGIN, title="Состав объявлений",
                             legend={"orientation": "h", "y": -0.2})
        figure.update_yaxes(autorange="reversed")
        st.plotly_chart(figure, width="stretch")
    with right:
        if inventory["snap_date"].nunique() < 2:
            st.info("График активных объявлений появится со второго снимка.")
        else:
            figure = go.Figure()
            days = pd.date_range(inventory["snap_date"].min(), inventory["snap_date"].max())
            _gapped_lines(figure, inventory.rename(columns={"snap_date": "stat_date"}),
                          "active", days, "%{y} активных")
            figure.update_layout(height=max(300, 120 + 46 * len(latest)), margin=MARGIN,
                                 title="Активных объявлений", hovermode="x unified")
            figure.update_xaxes(tickformat="%d.%m")
            st.plotly_chart(figure, width="stretch")

    st.dataframe(pd.DataFrame({
        "Аккаунт": latest["account"],
        "Снимок": latest["snap_date"].dt.strftime("%d.%m.%Y"),
        "Активных": latest["active"].map(fmt_int),
        "На ставке": latest["on_bid"].map(fmt_int),
        "Новых": latest["new_1d"].map(fmt_int),
        "Истекают": latest["expiring_7d"].map(fmt_int),
        "Цена в карточках": latest["price_main"].map(fmt_money),
        **{label: latest[column].map(fmt_int) for column, (label, _) in CLUSTERS.items()},
        "Расход 28 дн.": latest["spend_28d_rub"].map(fmt_money),
        "Без контактов": latest["spend_28d_no_contact_rub"].map(fmt_money),
    }), width="stretch", hide_index=True)


# --- отметки поверх «Динамики» -----------------------------------------------

def add_batch_marks(figure: go.Figure, batches: pd.DataFrame) -> None:
    """Пунктир в день партии ставок; подробности — во всплывающей подсказке ◆."""
    if batches.empty:
        return
    for day, part in batches.sort_values(["batch_time", "account"],
                                         na_position="first").groupby("batch_date"):
        lines = []
        for row in part.to_dict("records"):
            moves = [f"{sign}{row[col]}" for col, sign in
                     (("n_on", "вкл "), ("n_up", "↑"), ("n_down", "↓"),
                      ("n_off", "выкл "), ("n_limit", "лимит ")) if row[col]]
            time = f" {_text(row['batch_time'])}" if _text(row["batch_time"]) else ""
            lines.append(f"<b>{escape(row['batch'])}</b>{time} · {escape(row['account'])} · "
                         f"{row['cards']} объявл. ({', '.join(moves) or 'без изменений'})"
                         f"<br>{escape(_text(row['reason']))}")
        figure.add_shape(type="line", x0=day, x1=day, yref="paper", y0=0, y1=1,
                         line={"color": "#8A93A6", "width": 1, "dash": "dot"})
        figure.add_annotation(x=day, y=1, yref="paper", yanchor="bottom",
                              text="◆", showarrow=False,
                              font={"color": "#5A6478", "size": 14},
                              hovertext="<br>".join(lines))


def add_short_day_marks(figure: go.Figure, agg: pd.DataFrame, column: str,
                        kassa: pd.DataFrame) -> None:
    """Кружок на дне, когда аванс кончился до полуночи (full_day = 0)."""
    if kassa.empty:
        return
    short = kassa.loc[kassa["full_day"] == 0, ["stat_date", "account"]]
    points = agg.merge(short, left_on=["bucket", "account"],
                       right_on=["stat_date", "account"])
    if points.empty:
        return
    figure.add_trace(go.Scatter(
        x=points["bucket"], y=points[column], mode="markers",
        name="Аванс кончился", customdata=points["account"],
        marker={"symbol": "circle-open", "size": 14, "color": RED, "line": {"width": 2}},
        hovertemplate="%{customdata}: аванс кончился до полуночи<extra></extra>",
    ))
