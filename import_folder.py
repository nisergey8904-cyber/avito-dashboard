"""Пакетный импорт выгрузок из папки — альтернатива загрузке через интерфейс.

    python import_folder.py "C:/путь/к/папке"

Аккаунт определяется по номеру в скобках в имени файла: без скобок -> «Аккаунт 1»,
«(1)» -> «Аккаунт 2» и так далее. Соответствие можно переопределить словарём
ACCOUNT_BY_SUFFIX ниже.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from avito import db
from avito.parser import ParseError, parse_workbook

ACCOUNT_BY_SUFFIX = {"": 1, "1": 2, "2": 3, "3": 4}

_SUFFIX_RE = re.compile(r"\((\d+)\)\s*$")


def suffix_of(path: Path) -> str:
    match = _SUFFIX_RE.search(path.stem)
    return match.group(1) if match else ""


def main(folder: str) -> int:
    directory = Path(folder)
    files = sorted(directory.glob("*.xlsx"))
    if not files:
        print(f"В папке {directory} нет файлов .xlsx")
        return 1

    engine = db.get_engine()
    accounts = db.list_accounts(engine)
    name_by_id = dict(zip(accounts["id"], accounts["name"]))

    for path in files:
        suffix = suffix_of(path)
        account_id = ACCOUNT_BY_SUFFIX.get(suffix)
        if account_id is None or account_id not in name_by_id:
            print(f"[пропуск] {path.name}: не удалось определить аккаунт")
            continue
        try:
            parsed = parse_workbook(path, path.name)
        except ParseError as exc:
            print(f"[ошибка]  {path.name}: {exc}")
            continue

        _, replaced = db.save_upload(engine, account_id, parsed.period_start,
                                     parsed.period_end, path.name, parsed.rows)
        action = "заменено" if replaced else "добавлено"
        print(f"[{action}] {path.name} -> {name_by_id[account_id]}, "
              f"{parsed.period_start}..{parsed.period_end}, строк: {len(parsed.rows)}")
    return 0


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    raise SystemExit(main(target))
