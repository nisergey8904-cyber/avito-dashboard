# Размещение проекта на GitHub

Инструкция под конкретную машину: Git 2.55 установлен, Credential Manager
настроен (пароль вводить не придётся — откроется окно браузера), `gh` CLI нет,
поэтому репозиторий создаём через сайт.

Все команды выполняются в терминале **из папки проекта**. Открыть его проще
всего так: в проводнике зайти в `C:\Projects\stats2`, щёлкнуть правой кнопкой по
пустому месту → «Open Git Bash here» (или «Git Bash Here»).

---

## Шаг 1. Представиться Git

Без этого первый коммит не пройдёт — сейчас имя и почта не заданы. Выполняется
один раз на компьютере, для всех проектов:

```bash
git config --global user.name "Сергей"
git config --global user.email "nisergey8904@gmail.com"
```

Почта должна совпадать с той, на которую регистрируется аккаунт GitHub — иначе
коммиты не будут связаны с вашим профилем.

Заодно, чтобы Git не ломал кириллицу в именах файлов при выводе:

```bash
git config --global core.quotepath false
```

## Шаг 2. Завести аккаунт на GitHub

Если аккаунта нет — [github.com/signup](https://github.com/signup). Почту
подтвердить обязательно, иначе не получится создавать репозитории.

## Шаг 3. Создать пустой приватный репозиторий

1. Откройте [github.com/new](https://github.com/new).
2. **Repository name**: `avito-dashboard` (или любое имя латиницей).
3. **Description**: можно оставить пустым.
4. Выберите **Private** — это принципиально. В репозитории будет код, читающий
   вашу коммерческую статистику; публичным его делать нельзя.
5. **Ничего не отмечайте** в блоке «Initialize this repository with»: ни README,
   ни .gitignore, ни лицензию. Репозиторий должен быть пустым, иначе при первой
   отправке возникнет конфликт.
6. Нажмите **Create repository**.

Откроется страница с подсказками и адресом вида
`https://github.com/ВАШ_ЛОГИН/avito-dashboard.git` — он понадобится на шаге 6.

## Шаг 4. Создать локальный репозиторий и первый коммит

```bash
cd /c/Projects/stats2
git init
git branch -M main
git add -A
```

## Шаг 5. Проверить, что данные не попадут на GitHub

Это самый важный шаг — выполните его до отправки.

```bash
git status --short
```

Ожидаемый вывод — ровно 19 файлов кода:

```
A  .gitignore
A  .streamlit/config.toml
A  .streamlit/secrets.toml.example
A  Dockerfile
A  GITHUB.md
A  README.md
A  app.py
A  avito/__init__.py
A  avito/db.py
A  avito/metrics.py
A  avito/parser.py
A  avito/ui/__init__.py
A  avito/ui/calls.py
A  avito/ui/common.py
A  avito/ui/dashboard.py
A  avito/ui/settings.py
A  avito/ui/upload.py
A  docker-compose.yml
A  import_folder.py
A  requirements.txt
```

**Файлов `.xlsx`, папки `data/` и `secrets.toml` в списке быть не должно.**
Если что-то из этого появилось — не отправляйте, сначала разберитесь с
`.gitignore` (команда `git check-ignore -v ИМЯ_ФАЙЛА` покажет, какое правило
сработало или не сработало).

Убедившись, делаем коммит:

```bash
git commit -m "Дашборд статистики Авито"
```

## Шаг 6. Связать с GitHub и отправить

Подставьте свой логин:

```bash
git remote add origin https://github.com/ВАШ_ЛОГИН/avito-dashboard.git
git push -u origin main
```

При первой отправке откроется окно браузера с предложением войти в GitHub —
подтвердите вход. Логин и пароль в терминале вводить не нужно, Credential
Manager запомнит доступ.

## Шаг 7. Убедиться, что на GitHub только код

Обновите страницу репозитория. Проверьте глазами: в списке файлов нет ни одного
`.xlsx`, нет папки `data`, нет `secrets.toml`. Есть `secrets.toml.example` — это
правильно, там только шаблон без настоящих паролей.

---

## Как отправлять изменения потом

После любых правок в коде:

```bash
cd /c/Projects/stats2
git add -A
git status --short      # снова убедиться, что данных нет
git commit -m "Короткое описание, что изменили"
git push
```

Загруженные выгрузки и введённые звонки в Git не попадают и не должны — они
живут в базе, а не в репозитории.

---

## Если что-то пошло не так

**`Author identity unknown`** — не выполнен шаг 1.

**`remote origin already exists`** — адрес уже привязан. Посмотреть текущий:
`git remote -v`. Заменить: `git remote set-url origin НОВЫЙ_АДРЕС`.

**`failed to push some refs` / `rejected`** — репозиторий на GitHub создан не
пустым (с README). Проще всего удалить его на сайте (Settings → внизу Delete
this repository) и создать заново без галочек, как в шаге 3.

**`Support for password authentication was removed`** — Git просит пароль в
терминале вместо окна браузера. Включите менеджер учётных данных:
`git config --global credential.helper manager` и повторите отправку.

**Случайно отправили файл с данными** — недостаточно удалить его следующим
коммитом, он останется в истории. Нужно переписать историю
(`git filter-repo`) или, что быстрее и надёжнее, удалить репозиторий на GitHub
и создать заново с исправленным `.gitignore`.

---

## Что дальше

Репозиторий готов для публикации на Streamlit Community Cloud — порядок описан
в [README.md](README.md), раздел «Публикация: Streamlit Community Cloud».
Не забудьте про внешнюю базу: без неё данные в облаке будут пропадать при
каждом перезапуске приложения.
