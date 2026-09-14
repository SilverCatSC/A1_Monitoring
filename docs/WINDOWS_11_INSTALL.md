# A1 Monitoring: Windows 11 setup/readiness handoff

Дата: 14.09.2026. Версия staged-сборки: 0.14.0. Windows/MSI — deliberately
fail-closed резервный handoff, который не является живым host этой сборки.
Компьютер: MSI, Intel Core Ultra 5 125U, 16 ГБ RAM, Windows 11 x64.

Основной production host — MacBook / macOS. Эта инструкция нужна только если
владелец отдельно вернёт Windows в эксплуатационный scope.

**Статус:** MacBook — единственный accepted host path. Windows scanner/full/watch/
host runner и регистрация Task Scheduler с `-Apply` намеренно отказываются и не
могут создать marketplace cycle; Windows raw probe также отказан. Успешные тесты
или setup на MSI не меняют это правило. Эта инструкция сохраняет только
setup/readiness/dashboard и offline-AI handoff, а не production-готовность или
M7 requirement для MacBook.

## 1. Что и где работает

| Компонент | Назначение | Где работает |
| --- | --- | --- |
| Python 3.12 и Playwright | Установка и локальная QA без marketplace cycle | Windows, отдельное окружение `.venv312` |
| Google Chrome | Локальная диагностика; scanner intentionally refuses | Windows, отдельный профиль мониторинга |
| FastAPI, Jinja2 | Локальный дашборд | Docker |
| PostgreSQL 16 | История машин, ссылок, проверок и замечаний | Docker volume |
| Backup | Резервные копии базы | Отдельный Docker volume |
| llama.cpp + Qwen3.5-9B | Офлайн-анализ уже сохранённых артефактов | Windows CPU |
| Hermes | Проверка данных и снимков по одному автомобилю | Нативный Windows-процесс |
| Ouroboros | Независимый аудит покрытия и доказательств Hermes | Нативный Windows-процесс |

Обычные адреса: дашборд `http://127.0.0.1:18000/api/v1/dashboard`, база
`127.0.0.1:15433`, управление Chrome `127.0.0.1:19222`.
Не открывайте эти порты в интернет. Единый скрипт намеренно не держит одновременно
Chrome, Docker и модель: это ключевое ограничение для компьютера с 16 ГБ RAM.

## 2. Подготовка Windows

1. Установите обновления Windows и включите аппаратную виртуализацию, если Docker
   сообщает о её отсутствии.
2. Установите Docker Desktop для Windows x64, выберите **WSL 2 и Linux containers**,
   запустите Docker Desktop. При необходимости установки WSL выполните `wsl --install`
   в терминале администратора, перезагрузите ПК и снова откройте Docker Desktop.
   Актуальные требования и лицензия: [Docker Desktop для Windows](https://docs.docker.com/desktop/setup/install/windows-install/).
   Условия бесплатного использования зависят от организации; не считайте Docker
   Desktop автоматически бесплатным для любой коммерческой компании.
3. Установите Python **3.12 x64**, включая `pip` и Python Launcher; проверьте
   `py -3.12 --version`. Не выбирайте другую ветку Python только потому, что она новее.
   [Установка Python на Windows](https://docs.python.org/3.12/using/windows.html).
4. Установите Google Chrome и Git for Windows из официальных дистрибутивов.
5. Установите PowerShell 7. Команда из документации Microsoft:

   ```powershell
   winget install --id Microsoft.PowerShell --source winget
   ```

   Откройте **PowerShell 7** в Windows Terminal, а не старый Windows PowerShell 5.1.
   [Инструкция Microsoft](https://learn.microsoft.com/en-us/powershell/scripting/install/install-powershell-on-windows).

Дальнейшие команды выполняются в локальном PowerShell на MSI. Если используемый
«терминал Bitrix» является SSH-сеансом сервера, он не подходит для управления
видимым Chrome на вашем ПК. Сам Bitrix не нужен для локального запуска.

Проверка перед установкой проекта:

```powershell
$PSVersionTable.PSVersion
py -3.12 --version
git --version
docker compose version
docker info --format '{{.OSType}}'
```

Последняя команда должна вывести `linux`. Если какая-либо команда не работает,
сначала устраните эту ошибку. Первичная установка требует интернета для пакетов
и Docker-образов; это не автономный offline-установщик.

## 3. Получение файлов проекта

Рабочая папка: `C:\work\A1_Monitoring`.

Источник установки — [репозиторий A1_Monitoring](https://github.com/SilverCatSC/A1_Monitoring),
ветка `main` с согласованным commit SHA. Канонический рабочий каталог на Mac —
`/Users/filaret/Desktop/Monitoring`; прежнее имя каталога — только compatibility
symlink и не является источником для новой Windows-установки. Зафиксируйте commit
SHA в протоколе MSI-приёмки до clone, чтобы не принимать произвольный будущий `main`.

**Не переносите для новой установки** `.venv312` с Mac, `.env`, `artifacts`,
профиль Chrome, cookies и дампы через публичный репозиторий. `.venv312` создаётся
заново на Windows. Новая установка создаёт новую базу — история с Mac сама не
появится. Перенос существующей истории требует отдельного приватного backup/restore.

Для новой Windows-установки используйте:

```powershell
New-Item -ItemType Directory -Force C:\work | Out-Null
Set-Location C:\work
git clone https://github.com/SilverCatSC/A1_Monitoring.git
Set-Location C:\work\A1_Monitoring
# При наличии согласованного SHA: git checkout <approved-commit-sha>
git status --short
```

`git status --short` перед началом должен быть пуст. Не запускайте clone поверх уже
существующей папки и не переносите в Git `.env`, Chrome profile, cookies, evidence
или dump.

## 4. Первичная настройка

В PowerShell 7:

```powershell
Set-Location C:\work\A1_Monitoring
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup_windows.ps1
```

Разрешение запуска действует только в текущем окне терминала. Скрипт создаёт
`.venv312`, устанавливает зависимости, создаёт `.env`, если его ещё нет,
запускает базу, приложение и backup. Затем проверяет зависимости и страницы приложения.
Ненулевой код команды останавливает установку. Итог полного успеха — `WINDOWS_SETUP_OK`.
Существующая `.env` не перезаписывается. Не меняйте `DB_PASSWORD` у уже созданной базы
без согласованной процедуры смены пароля.

Основные параметры новой установки:

```dotenv
APP_BIND_PORT=18000
DB_BIND_PORT=15433
APP_TZ=Europe/Moscow
SCHEDULER_ENABLED=false
AUTH_ENABLED=false
```

Отключённая авторизация допустима только при доступе через `127.0.0.1`.
Источник задаётся в `SOURCE_GOOGLE_SHEET_EXPORT_URL`. Импорт читает таблицу;
не обновляет её через Apps Script и не записывает обратно. Проверьте актуальность
исходной вкладки до живого запуска. Не публикуйте `.env` и полный журнал настройки.

`SCHEDULER_ENABLED` допускает только `false`: `true` отвергается во всех
окружениях. Контейнер не умеет управлять видимым Chrome и не является заменой
MacBook host runner.

Параметр `-SkipDocker` предназначен только для частичной подготовки Python:
он **не запускает приложение** и не даёт готовый мониторинг.

## 5. Тестирование без площадок

```powershell
.\scripts\start_windows.ps1 -OpenDashboard
.\.venv312\Scripts\python.exe -m pytest -q
.\.venv312\Scripts\python.exe -m ruff check src tests scripts
.\.venv312\Scripts\python.exe scripts/doctor.py --http
.\.venv312\Scripts\python.exe scripts/check_ui.py
```

Выполняйте команды по очереди; продолжайте после успешного завершения.
Ожидается: тесты без ошибок, `All checks passed!`, `LOCAL_READY`, `UI_OK`.
Последняя проверка снимает локальный интерфейс в `artifacts/ui_0_8/`, не обращается
к площадкам и не записывает фиктивные результаты мониторинга в рабочую базу.

**Это ещё не проверка размещения автомобилей.** Для неё нужен следующий этап.

## 5.1. Установка локального AI-контура

После успешной установки приложения:

```powershell
.\scripts\install_ai_tools_windows.ps1
.\scripts\download_local_model_windows.ps1
```

Первая команда устанавливает закреплённую ревизию Hermes, Ouroboros 0.54.1 и
официальную CPU-сборку llama.cpp. Вторая загружает Qwen3.5-9B Q4_K_M и vision-проектор;
потребуется несколько гигабайт трафика и свободного места. Файлы можно приватно
перенести с Mac в `artifacts\models`, но нельзя помещать их в Git.

Hermes имеет нативный Windows-установщик, однако сам проект помечает этот режим как
раннюю beta. Поэтому успешная установка на Mac не является приёмкой Windows.

## 6. Marketplace cycle на Windows заблокирован

Не запускайте `local_scan_windows.ps1`, `run_full_monitoring_windows.ps1`,
`-Watch`, `run_monitoring_host_windows.ps1` или Windows raw probe: они
intentionally fail-closed. Не создавайте на MSI VPN evidence, Task Scheduler
или новый marketplace cycle как обход этого ограничения. Контракт live cycle,
VPN admission и retry относится только к MacBook host runner.

## 7. Где результаты и что проверить вручную

| Результат | Место |
| --- | --- |
| Текущий прогресс, итог запуска | Дашборд → Обзор |
| Машина, цена, ссылка, номер страницы | Автомобили / Размещения |
| Возможная перевыкладка, новая ссылка | Сверка ссылок |
| Предыдущие страницы и проверки, выходные | История проверок / Статистика |
| Ошибки карточек от менеджеров | Замечания |
| Лог команды | `artifacts/manual_scan_*.log` |
| Снимки и технические доказательства | `artifacts/evidence/` |
| История данных | PostgreSQL, Docker volume; не внутри папки исходников |

Для приёмки выберите несколько машин, включая VLE и новые автомобили. Сверьте:
фактическая география **Москва, 0 км** на обеих площадках; новые Auto.ru общим
списком; корректная ссылка; цена с разделением тысяч; номер страницы из первых
трёх; снимок карточки именно этой машины. Проверьте, что карточки рекомендаций
и соседних городов не засчитываются как основной результат.

Кандидаты на новую ссылку требуют осмысленного подтверждения; совпадения модели
и цены недостаточно. Старый номер страницы не должен переноситься на новый ID объявления.

## 8. Повседневная readiness-диагностика и остановка

Приложение:

```powershell
.\scripts\start_windows.ps1 -OpenDashboard
```

Для остановки локальных сервисов при необходимости:

```powershell
docker compose stop
```

Данные сохраняются. **Не выполняйте `docker compose down -v`**: это удаляет volumes.
Перед обновлением и переносом сделайте проверенную резервную копию базы. Новый образ
применяет миграции автоматически; рабочую базу не подменяйте тестовой.

### Windows Task Scheduler и host runner

Windows host runner, scanner/full/watch и
`register_monitoring_task_windows.ps1 -Apply` специально отказываются. Не существует допустимого Task Scheduler
fallback и не нужно создавать его вручную. Любое будущее изменение требует нового
решения владельца, отдельной архитектурной проверки и live acceptance с нуля.

## 9. Если что-то не работает

| Симптом | Следующее действие |
| --- | --- |
| `py -3.12` не найден | Установить Python 3.12 x64 с Launcher; открыть новый терминал |
| Ошибка скачивания пакета | Сохранить имя пакета и ошибку; не обновлять весь lock наугад |
| Docker daemon недоступен | Запустить Docker Desktop, проверить WSL2 / Linux containers |
| `FAIL · Google Chrome` | Проверить установку Chrome для этого пользователя или всего ПК |
| Порт занят | Проверить конфликт; согласованно изменить `.env`, не пароль существующей базы |
| `LOCAL_SCAN_PARTIAL` | Смотреть Сверку ссылок, прогресс и лог; не трактовать как отсутствие всех машин |
| Зависает видимый Chrome | Не запускать второй цикл; проверить сообщение в браузере и журнал |
| Дашборд показывает старую дату | Локальные тесты не обновляют мониторинг; проверить дату последнего живого цикла |

Для диагностики контейнера: `docker compose ps`, `docker compose logs --tail 100 app`.
Перед пересылкой журналов удалите секреты, персональные и внутренние данные.

## 10. GitHub и Bitrix — отдельная приёмка

Установка не публикует данные в GitHub и не отправляет сообщения в Bitrix.
Имеющиеся экспорт/публикация — заготовки, не часть принятого
локального запуска. **Не запускайте `publish_pages.ps1 -Push` для рабочих данных:**
текущий экспорт копирует внутренние страницы и ещё не гарантирует исключение VIN,
замечаний менеджеров и других непубличных сведений. Сначала нужен безопасный
публичный набор полей и отдельная проверка HTML.

Читайте также: [README](archive/2026-09-10/README.md), [стек](STACK.md),
[локальное окружение](LOCAL_ENVIRONMENT.md), [план MSI/AI](archive/MSI_AGENT_PUBLICATION_PLAN.md),
[правила продукта](archive/2026-09-10/BIBLE.md).
