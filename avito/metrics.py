"""Агрегации и производные метрики воронки."""

from __future__ import annotations

import numpy as np
import pandas as pd

SUM_COLUMNS = [
    "impressions", "views", "target_views", "contacts", "chat", "phone",
    "phone_and_chat", "favorites", "spend_total", "spend_placement",
    "spend_promo", "bonus_spent",
]

RU_LABELS = {
    "account": "Аккаунт",
    "region": "Регион",
    "city": "Город",
    "period": "Период",
    "listings": "Объявлений",
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
    return out


def aggregate(listings: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Суммирует метрики по срезу и считает число объявлений."""
    if listings.empty:
        return pd.DataFrame(columns=by + SUM_COLUMNS + ["listings"])
    present = [c for c in SUM_COLUMNS if c in listings.columns]
    grouped = listings.groupby(by, dropna=False, observed=True)
    out = grouped[present].sum().reset_index()
    out["listings"] = grouped.size().reset_index(name="n")["n"]
    return out


def period_label(start, end) -> str:
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    return f"{start:%d.%m} – {end:%d.%m.%Y}"


def calls_by_period(calls: pd.DataFrame, periods: pd.DataFrame) -> pd.DataFrame:
    """Раскладывает подневные звонки по периодам выгрузок.

    periods — уникальные (account_id, period_start, period_end). Звонок относится
    к периоду, если его дата попадает в интервал включительно.
    """
    columns = ["account_id", "period_start", "period_end", "calls", "deals"]
    if periods.empty:
        return pd.DataFrame(columns=columns)
    if calls.empty:
        result = periods.copy()
        result["calls"] = 0
        result["deals"] = 0
        return result[columns]

    merged = periods.merge(calls, on="account_id", how="left")
    inside = merged["call_date"].between(merged["period_start"], merged["period_end"])
    merged.loc[~inside, ["calls", "deals"]] = 0
    result = (merged.groupby(["account_id", "period_start", "period_end"],
                             dropna=False)[["calls", "deals"]]
              .sum().reset_index())
    return result[columns]


def attach_calls(agg: pd.DataFrame, calls_agg: pd.DataFrame,
                 keys: list[str]) -> pd.DataFrame:
    """Присоединяет звонки к агрегату по общим ключам (аккаунт и/или период)."""
    out = agg.copy()
    if calls_agg.empty or not keys:
        out["calls"] = 0
        out["deals"] = 0
        return out
    trimmed = calls_agg.groupby(keys, dropna=False)[["calls", "deals"]].sum().reset_index()
    out = out.merge(trimmed, on=keys, how="left")
    out[["calls", "deals"]] = out[["calls", "deals"]].fillna(0)
    return out


def funnel_totals(listings: pd.DataFrame, calls_total: float) -> dict[str, float]:
    """Сводные числа для карточек и воронки."""
    def total(col: str) -> float:
        return float(listings[col].sum()) if col in listings.columns else 0.0

    impressions, views = total("impressions"), total("views")
    contacts, spend = total("contacts"), total("spend_total")
    return {
        "listings": int(len(listings)),
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
