"""Разбор дневных выгрузок Авито (xlsx).

Ожидается файл за один день — «Статистика_за_2026-07-23 (1).xlsx»: дата берётся
из имени, внутри файла её нет. Схема выгрузки нестабильна: в части файлов
присутствует колонка «Сотрудник», в части — нет, поэтому колонки сопоставляются
по нормализованным заголовкам, а не по позициям. В заголовках Авито использует
неразрывные пробелы (\\xa0).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

# Нормализованный заголовок Авито -> имя поля в базе.
COLUMN_MAP = {
    "Номер объявления": "listing_id",
    "Регион размещения": "region",
    "Город": "city",
    "Адрес": "address",
    "Категория": "category",
    "Подкатегория": "subcategory",
    "Параметр": "param",
    "Название объявления": "title",
    "Цена": "price_raw",
    "Дата первой публикации": "first_published",
    "Дата снятия с публикации": "unpublished_at",
    "Дней на Авито": "days_online",
    "Сотрудник": "employee",
    "Показы": "impressions",
    "Конверсия из показов в просмотры": "cr_impr_views_src",
    "Просмотры": "views",
    "Средняя цена просмотра": "avg_view_price",
    "Конверсия из просмотров в контакты": "cr_views_contacts_src",
    "Целевые просмотры": "target_views",
    "Контакты": "contacts",
    "Написали в чат": "chat",
    "Посмотрели телефон": "phone",
    "Посмотрели телефон и написали в чат": "phone_and_chat",
    "Откликнулись на скидку в чате": "discount_replies",
    "Средняя цена контакта": "avg_contact_price",
    "Добавили в избранное": "favorites",
    "Расходы на объявления": "spend_total",
    "Списано бонусов на объявления": "bonus_spent",
    "Расходы на размещение и целевые действия": "spend_placement",
    "Расходы на продвижение": "spend_promo",
    "Остальные расходы": "spend_other",
}

# Без этих колонок считать нечего — файл не является выгрузкой Авито.
REQUIRED = ["region", "city", "impressions", "views", "contacts", "spend_total"]

NUMERIC = [
    "days_online", "impressions", "views", "target_views", "contacts",
    "chat", "phone", "phone_and_chat", "discount_replies", "favorites",
    "spend_total", "bonus_spent", "spend_placement", "spend_promo", "spend_other",
    "avg_view_price", "avg_contact_price",
]

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_PRICE_RE = re.compile(r"\d+")


class ParseError(ValueError):
    """Файл не похож на дневную выгрузку Авито или не читается."""


@dataclass(frozen=True)
class ParsedDay:
    """Разобранный файл. stat_date=None — даты в имени нет, её задаёт пользователь."""

    stat_date: date | None
    rows: pd.DataFrame


def _norm_header(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("\xa0", " ").replace(" ", " ").strip()


def date_from_filename(filename: str) -> date | None:
    """Достаёт день из имени вида «Статистика_за_2026-07-23 (1).xlsx».

    Возвращает None, если дат в имени нет — тогда день задаёт пользователь.
    Несколько разных дат — это выгрузка за период: разложить её по дням
    невозможно, поэтому такой файл отклоняется, а не сваливается в один день.
    """
    found = set()
    for chunk in _DATE_RE.findall(filename):
        try:
            found.add(datetime.strptime(chunk, "%Y-%m-%d").date())
        except ValueError:
            continue
    if not found:
        return None
    if len(found) > 1:
        first, last = min(found), max(found)
        raise ParseError(
            f"Это выгрузка за период {first:%d.%m.%Y}–{last:%d.%m.%Y}, а нужны "
            "данные за один день. В Авито выгрузите статистику по дням: "
            "имя файла должно быть вида «Статистика_за_2026-07-23.xlsx»."
        )
    return found.pop()


def parse_price(value: object) -> float | None:
    """«1 400 ₽» и «от 1 400 ₽» -> 1400.0. Неразрывные пробелы внутри числа."""
    if value is None or isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    digits = _PRICE_RE.findall(str(value).replace("\xa0", "").replace(" ", ""))
    return float(digits[0]) if digits else None


def _to_date(value: object) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value).strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_workbook(source, filename: str = "") -> ParsedDay:
    """Читает xlsx (путь или файловый объект) в нормализованный DataFrame."""
    stat_date = date_from_filename(filename)  # период в имени — ошибка сразу
    try:
        raw = pd.read_excel(source, sheet_name=0, engine="openpyxl")
    except Exception as exc:  # noqa: BLE001 — показываем пользователю причину как есть
        raise ParseError(f"Не удалось прочитать файл: {exc}") from exc

    raw.columns = [_norm_header(c) for c in raw.columns]
    known = {src: dst for src, dst in COLUMN_MAP.items() if src in raw.columns}
    df = raw[list(known)].rename(columns=known)

    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        human = [src for src, dst in COLUMN_MAP.items() if dst in missing]
        raise ParseError(
            "Файл не похож на выгрузку Авито — не найдены колонки: "
            + ", ".join(human)
        )

    for col in NUMERIC:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        else:
            df[col] = 0.0

    for col in ("first_published", "unpublished_at"):
        df[col] = df[col].map(_to_date) if col in df.columns else None

    df["price"] = df["price_raw"].map(parse_price) if "price_raw" in df.columns else None
    for col in ("listing_id", "title", "employee", "address", "category",
                "subcategory", "param", "price_raw"):
        if col not in df.columns:
            df[col] = None
        df[col] = df[col].astype("object").where(df[col].notna(), None)

    # Пустые хвостовые строки Excel: без региона и без единого показа.
    df = df[df["region"].notna() | (df["impressions"] > 0)].copy()
    if df.empty:
        raise ParseError("В файле нет строк с данными.")

    return ParsedDay(stat_date=stat_date, rows=df)
