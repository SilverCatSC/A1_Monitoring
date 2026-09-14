# Программный стек

Текущая staged-версия — 0.14.0. Реализованный и технически проверенный контур —
Python 3.12 / macOS / ARM64, контейнеры Linux ARM64. MacBook Pro M2 Pro с 16 ГБ
назначен основным production host, хотя его живые M7-gates ещё не закрыты;
[запуск на Mac](MACOS_INSTALL.md). Windows 11 на MSI — резервный, непринятый
handoff. Его видимый Chrome допустим только через `scripts/*_windows.ps1` в
интерактивной сессии, не через container scheduler.

| Назначение | Технология / проверенная версия |
| --- | --- |
| Язык | Python 3.12.14 в текущем .venv312 |
| Backend | FastAPI 0.141.1, Uvicorn 0.52.4 |
| Шаблоны | Jinja2 3.1.6, HTML5, CSS, browser JavaScript |
| Браузер | Playwright 1.62.0 + установленный Chrome через CDP |
| HTML parsing | BeautifulSoup 4.15.0 |
| Импорт | pandas 3.0.5, HTTPX 0.28.1 |
| Данные | PostgreSQL 16, SQLAlchemy 2.0.52, psycopg2-binary 2.9.12 |
| Миграции | Alembic 1.19.1; stage head 20260914_0011 |
| Тестирование | pytest 9.1.1, Ruff 0.16.6 |
| Поставка | Docker Compose, Git, requirements.lock |
| Основной host | macOS MacBook, интерактивный Chrome + будущий per-user LaunchAgent, не container scheduler |
| Резервный Windows-handoff | PowerShell 7, Docker Desktop/WSL2, Git for Windows, Chrome, interactive host-runner |
| Публичный отчёт | Статический HTML/CSS/JS, GitHub Pages workflow |
| Bitrix24 | Входящий webhook, `scripts/bitrix_publish.ps1`, dry-run по умолчанию |
| Локальная инференс-модель | Qwen3.5-9B Q4_K_M + mmproj-F16 (текст, reasoning, изображения), llama.cpp/Metal, loopback `127.0.0.1:18080` |
| QA-агент мониторинга | Hermes Agent 0.21.1, проектный профиль без инструментов и без cloud fallback |
| Инженерный агент | Q00/Ouroboros 0.54.1 + Hermes, отдельный HOME и отдельные git-worktree |

requirements.lock фиксирует установленные зависимости и тестовый инструментарий.
Selenium встречается в старом локальном окружении, но не используется рабочим
worker и не входит в новый lock. Node.js не требуется для работы приложения.
Образы PostgreSQL/Playwright закреплены версиями, но пока не immutable digest.
GitHub Pages не заменяет FastAPI. Планируется безопасный статический экспорт на Mac
и передача ссылки в Bitrix; текущая заготовка экспорта внутренних страниц не прошла
приёмку для публикации рабочих данных.
