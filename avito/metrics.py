"""Агрегации и производные метрики воронки."""

from __future__ import annotations

import numpy as np
import pandas as pd

SUM_COLUMNS = [
    "impressions", "views", "target_views", "contacts", "phone",
    "favorites", "spend_total", "spend_placement",
    "spend_promo", "bonus_spent",
]

RU_LABELS = {
    "account": "Аккаунт",
    "region": "Регион",
    "city": "Город",
    "stat_date": "Дата",
    "rows": "Строк",
    "days": "Дней",
    "listings_per_day": "Объявлений в день",
    "impressions": "Показы",
    "views": "Просмотры",
    "contacts": "Контакты",
    "calls": "Звонки",
    "spend_total": "Расходы, ₽",
    "spend_promo": "Продвижение, ₽",
    "favorites": "В избранном",
    "cr_impr_views": "CR показ→просмотр",
    "cr_views_contacts": "CR просмотр→контакт",
    "cr_contacts_calls": "CR контакт→звонок",
    "cost_per_view": "Цена просмотра, ₽",
    "cost_per_contact": "Цена контакта, ₽",
    "cost_per_call": "Цена звонка, ₽",
}


# Предмет объявления — по метке в названии, тем же правилом, что в AI Avitolog
# (app/predmety.py, SUBJECTS в app/zaliv_plan.py): «русск» → русский,
# «физик» → физика, без метки — математика. Порядок — порядок показа.
MATH = "Математика"
SUBJECT_MARKS = {"Русский": "русск", "Физика": "физик"}
SUBJECTS = [MATH, *SUBJECT_MARKS]
# Первый залив предмета: подпись «с 18.11» у новых предметов.
SUBJECT_START = {"Русский": pd.Timestamp("2026-11-18"),
                 "Физика": pd.Timestamp("2026-11-23")}
# Сравнивать предметы честно только на карточках одного возраста: конверсия
# растёт с возрастом карточки. База AI Avitolog — карточки с 17.11.
FRESH_FROM = pd.Timestamp("2026-11-17")


def subject_of(titles: pd.Series) -> pd.Series:
    lower = titles.fillna("").astype(str).str.lower()
    out = pd.Series(MATH, index=titles.index)
    for name, mark in SUBJECT_MARKS.items():
        out = out.mask(lower.str.contains(mark, regex=False) & (out == MATH), name)
    return out


# Сегмент — по региону строки (решение руководителя 25.11.2026): в Москве и
# области контакт может стоить дороже, потому что там выше конверсия в сделку.
# Зеленоград приходит с регионом «Москва». Пустой регион — вне сегментов.
SEG_MSK = "Москва + МО"
SEG_REGIONS = "Регионы"
SEG_UNKNOWN = "Не указан"
SEGMENTS = [SEG_MSK, SEG_REGIONS, SEG_UNKNOWN]
MSK_REGIONS = {"Москва", "Московская область"}
# Код сегмента в таблице cpl_targets.
SEGMENT_CODES = {SEG_MSK: "msk_mo", SEG_REGIONS: "regions"}
# Планки, пока cpl_targets пуста или в ней нет строки сегмента.
DEFAULT_TARGETS = {SEG_MSK: 750.0, SEG_REGIONS: 650.0}


def segment_of(regions: pd.Series, unknown: str = SEG_UNKNOWN) -> pd.Series:
    """Сегмент строки. unknown — подпись пустого региона в данных."""
    text = regions.fillna("").astype(str).str.strip()
    out = pd.Series(SEG_REGIONS, index=regions.index)
    out = out.mask(text.isin(MSK_REGIONS), SEG_MSK)
    return out.mask((text == "") | (text == unknown), SEG_UNKNOWN)


def target_on(targets: pd.DataFrame, segment: str, day) -> float | None:
    """Планка сегмента на день: строка с наибольшим valid_from ≤ day."""
    code = SEGMENT_CODES.get(segment)
    if code is None:
        return None
    if targets is not None and not targets.empty:
        own = targets[(targets["segment"] == code)
                      & (targets["valid_from"] <= pd.Timestamp(day))]
        if not own.empty:
            return float(own.sort_values("valid_from")["target_cpl_rub"].iloc[-1])
    return DEFAULT_TARGETS.get(segment)


def targets_from_table(targets: pd.DataFrame) -> bool:
    """Есть ли в cpl_targets хоть одна строка — для подписи об источнике планок."""
    return targets is not None and not targets.empty


def _safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Деление с NaN вместо бесконечности — 0 контактов не значит цена 0."""
    num = pd.to_numeric(numerator, errors="coerce")
    den = pd.to_numeric(denominator, errors="coerce")
    return (num / den.where(den > 0)).replace([np.inf, -np.inf], np.nan)


def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    """Добавляет конверсии и удельные стоимости к агрегированной таблице."""
    out = df.copy()
    for col in ("impressions", "views", "contacts", "spend_total", "calls"):
        if col not in out.columns:
            out[col] = 0.0
    out["cr_impr_views"] = _safe_div(out["views"], out["impressions"])
    out["cr_views_contacts"] = _safe_div(out["contacts"], out["views"])
    out["cr_contacts_calls"] = _safe_div(out["calls"], out["contacts"])
    out["cost_per_view"] = _safe_div(out["spend_total"], out["views"])
    out["cost_per_contact"] = _safe_div(out["spend_total"], out["contacts"])
    out["cost_per_call"] = _safe_div(out["spend_total"], out["calls"])
    if "rows" in out.columns and "days" in out.columns:
        out["listings_per_day"] = _safe_div(out["rows"], out["days"])
    return out


def aggregate(rows: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Суммирует метрики по срезу.

    rows — число строк выгрузки, days — сколько разных дней в них попало. Одно
    объявление даёт по строке за каждый день, поэтому «сколько объявлений» —
    это rows/days (см. add_derived), а не число строк.
    """
    if rows.empty:
        return pd.DataFrame(columns=by + SUM_COLUMNS + ["rows", "days"])
    present = [c for c in SUM_COLUMNS if c in rows.columns]
    grouped = rows.groupby(by, dropna=False, observed=True)
    out = grouped[present].sum().reset_index()
    out["rows"] = grouped.size().to_numpy()
    out["days"] = (grouped["stat_date"].nunique().to_numpy()
                   if "stat_date" in rows.columns else 1)
    return out


def range_label(start, end) -> str:
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if start == end:
        return f"{start:%d.%m.%Y}"
    return f"{start:%d.%m.%Y} – {end:%d.%m.%Y}"


def bucket(dates: pd.Series, freq: str) -> pd.Series:
    """Сводит даты к началу недели или месяца — для графика динамики.

    freq: «D» — как есть, «W» — понедельник недели, «M» — первое число месяца.
    """
    if freq == "W":
        # «W» в pandas — неделя, заканчивающаяся воскресеньем, то есть start_time
        # приходится на понедельник. С «W-MON» неделя начиналась бы со вторника.
        return dates.dt.to_period("W").dt.start_time
    if freq == "M":
        return dates.dt.to_period("M").dt.start_time
    return dates


def _calls_in_range(calls: pd.DataFrame, start, end) -> pd.DataFrame:
    """Звонки, попавшие в интервал включительно."""
    if calls.empty:
        return calls
    inside = calls["call_date"].between(pd.Timestamp(start), pd.Timestamp(end))
    return calls[inside]


def calls_by_account(calls: pd.DataFrame, start, end) -> pd.DataFrame:
    """Звонки за интервал, просуммированные по аккаунтам."""
    picked = _calls_in_range(calls, start, end)
    if picked.empty:
        return pd.DataFrame(columns=["account_id", "account", "calls", "deals"])
    return (picked.groupby(["account_id", "account"], dropna=False)[["calls", "deals"]]
            .sum().reset_index())


def calls_by_day(calls: pd.DataFrame, start, end) -> pd.DataFrame:
    """Звонки за интервал по дням и аккаунтам."""
    columns = ["account", "stat_date", "calls", "deals"]
    picked = _calls_in_range(calls, start, end)
    if picked.empty:
        return pd.DataFrame(columns=columns)
    out = (picked.groupby(["account", "call_date"], dropna=False)[["calls", "deals"]]
           .sum().reset_index()
           .rename(columns={"call_date": "stat_date"}))
    return out[columns]


def funnel_totals(rows: pd.DataFrame, calls_total: float) -> dict[str, float]:
    """Сводные числа для карточек и воронки."""
    def total(col: str) -> float:
        return float(rows[col].sum()) if col in rows.columns else 0.0

    impressions, views = total("impressions"), total("views")
    contacts, spend = total("contacts"), total("spend_total")
    days = int(rows["stat_date"].nunique()) if "stat_date" in rows.columns else 0
    return {
        "rows": int(len(rows)),
        "days": days,
        "listings_per_day": len(rows) / days if days else np.nan,
        "impressions": impressions,
        "views": views,
        "contacts": contacts,
        "calls": float(calls_total),
        "spend_total": spend,
        "favorites": total("favorites"),
        "cr_impr_views": views / impressions if impressions else np.nan,
        "cr_views_contacts": contacts / views if views else np.nan,
        "cr_contacts_calls": calls_total / contacts if contacts else np.nan,
        "cost_per_view": spend / views if views else np.nan,
        "cost_per_contact": spend / contacts if contacts else np.nan,
        "cost_per_call": spend / calls_total if calls_total else np.nan,
    }
