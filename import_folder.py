"""Пакетный импорт дневных выгрузок из папок — альтернатива загрузке в интерфейсе.

Ожидаемая раскладка: папка с подпапками, названными ID кабинетов Авито, внутри —
файлы вида «Статистика_за_2026-07-23 (1).xlsx». Аккаунт находится по ID папки, а
если такого ещё нет — создаётся («Аккаунт 181777530», переименовать можно в
настройках, привязка не потеряется).

    python import_folder.py "new stats"                     # все подпапки-кабинеты
    python import_folder.py "new stats/181777530"           # одна папка, ID из имени
    python import_folder.py "C:/выгрузки/июль" --account 2  # одна папка в аккаунт #2

День берётся из имени файла. Повторный импорт того же дня заменяет прежние строки,
поэтому команду можно запускать сколько угодно раз.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from avito import db
from avito.parser import ParseError, parse_workbook


def _import_folder(engine, folder: Path, account_id: int, account_name: str) -> tuple[int, int]:
    """Импортирует все xlsx из папки. Возвращает (файлов, строк)."""
    files = sorted(folder.glob("*.xlsx"))
    if not files:
        print(f"[пропуск] {folder.name}: нет файлов .xlsx")
        return 0, 0

    print(f"--- {folder.name} -> {account_name} ({len(files)} файлов)")
    imported, rows_total = 0, 0
    for path in files:
        try:
            parsed = parse_workbook(path, path.name)
        except ParseError as exc:
            print(f"  [ошибка]  {path.name}: {exc}")
            continue
        if parsed.stat_date is None:
            print(f"  [пропуск] {path.name}: в имени файла нет даты")
            continue

        count, replaced = db.save_day(engine, account_id, parsed.stat_date,
                                      path.name, parsed.rows)
        imported += 1
        rows_total += count
        action = "заменён " if replaced else "добавлен"
        print(f"  [{action}] {parsed.stat_date:%d.%m.%Y}: строк {count}")
    return imported, rows_total


def _account_folders(root: Path) -> list[Path]:
    """Подпапки, названные ID кабинетов Авито."""
    return sorted(p for p in root.iterdir() if p.is_dir() and any(p.glob("*.xlsx")))


def main(target: str, account_id: int | None) -> int:
    root = Path(target)
    if not root.is_dir():
        print(f"Папки {root} нет.")
        return 1

    engine = db.get_engine()
    accounts = db.list_accounts(engine)
    name_by_id = dict(zip(accounts["id"], accounts["name"]))

    if account_id is not None:
        if account_id not in name_by_id:
            print(f"Аккаунта #{account_id} нет. Доступные: "
                  + ", ".join(f"#{i} {n}" for i, n in name_by_id.items()))
            return 1
        folders = [(root, account_id)]
    elif any(root.glob("*.xlsx")):
        # Одна папка: ID кабинета — её имя.
        folders = [(root, db.account_for_avito_id(engine, root.name))]
    else:
        found = _account_folders(root)
        if not found:
            print(f"В папке {root} нет ни файлов .xlsx, ни подпапок с ними.")
            return 1
        folders = [(f, db.account_for_avito_id(engine, f.name)) for f in found]

    # Аккаунты могли быть созданы по ID папок только что.
    fresh = db.list_accounts(engine)
    name_by_id = dict(zip(fresh["id"], fresh["name"]))

    files_total, rows_total = 0, 0
    for folder, target_account in folders:
        files, rows = _import_folder(engine, folder, target_account,
                                     name_by_id.get(target_account, str(target_account)))
        files_total += files
        rows_total += rows

    bounds = db.date_bounds(engine)
    print(f"\nИтого: файлов {files_total}, строк {rows_total}.")
    if bounds:
        print(f"В базе данные с {bounds[0]:%d.%m.%Y} по {bounds[1]:%d.%m.%Y}.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", nargs="?", default=".",
                        help="папка с подпапками-кабинетами или с файлами одного кабинета")
    parser.add_argument("--account", type=int, default=None,
                        help="номер аккаунта в базе; без него аккаунт ищется по ID папки")
    args = parser.parse_args()
    raise SystemExit(main(args.folder, args.account))
