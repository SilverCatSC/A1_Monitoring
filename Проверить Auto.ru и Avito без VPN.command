#!/usr/bin/env bash
# One explicitly labeled local diagnostic. Never changes VPN settings.
set -uo pipefail
umask 077

ROOT_DIR='/Users/filaret/Desktop/Monitoring'
if [[ ! -x "$ROOT_DIR/scripts/run_monitoring_host_macos.sh" ]]; then
  printf 'Не найден проект Monitoring.\n' >&2
  read -r _unused
  exit 1
fi
cd "$ROOT_DIR" || exit 1

run_stamp="$(/bin/date '+%Y%m%d_%H%M%S')"
run_dir="$ROOT_DIR/artifacts/diagnostic_no_vpn/$run_stamp"
/bin/mkdir -p -m 700 "$run_dir" || exit 1
run_log="$run_dir/host_runner.log"

printf 'Полный диагностический цикл Auto.ru и Avito без VPN: реестр, каталог продавца, ID, поиск и карточки.\n'
printf 'Ускоренный режим: программные паузы сокращены в 5 раз; объём проверки сохранён.\n'
printf 'Перед запуском отключите VPN в INCY и других клиентах. Проверка не меняет сетевые настройки.\n'
printf 'При CAPTCHA пройдите проверку в окне Chrome программы. Ожидание без таймера; остановка — Ctrl+C.\n'
printf 'Журнал: %s\n\n' "$run_log"

"$ROOT_DIR/scripts/run_monitoring_host_macos.sh" \
  --diagnostic-no-vpn --engines auto_ru,avito --pages 5 \
  --pace fast \
  --captcha-wait-seconds -1 2>&1 | /usr/bin/tee "$run_log"
pipeline_status=("${PIPESTATUS[@]}")
run_exit=${pipeline_status[0]}
if [[ "${pipeline_status[1]}" -ne 0 ]]; then
  printf 'Журнал не удалось сохранить.\n' >&2
  run_exit=1
fi

if [[ "$run_exit" -eq 0 || "$run_exit" -eq 2 ]]; then
  progress_path="$ROOT_DIR/artifacts/evidence/scan_progress.json"
  if [[ -f "$progress_path" ]]; then
    /bin/cp "$progress_path" "$run_dir/progress.json"
    cycle_id="$("$ROOT_DIR/.venv312/bin/python" -c \
      'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("cycle_id", ""))' \
      "$progress_path" 2>/dev/null)"
    if [[ "$cycle_id" =~ ^[[:xdigit:]]{8}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{12}$ ]]; then
      status_url="http://127.0.0.1:18000/api/v1/status/cycles/$cycle_id"
      scan_url="http://127.0.0.1:18000/api/v1/status/scans/latest?cycle_id=$cycle_id"
      /usr/bin/curl -fsS --max-time 10 "$status_url" -o "$run_dir/cycle_status.json" || true
      /usr/bin/curl -fsS --max-time 10 "$scan_url" -o "$run_dir/scan_status.json" || true
      printf '\nРезультат этого цикла: %s\n' "$status_url"
      printf 'Проверки площадок: %s\n' "$scan_url"
      if "$ROOT_DIR/.venv312/bin/python" \
          "$ROOT_DIR/scripts/write_local_diagnostic_report.py" "$run_dir"; then
        /usr/bin/open -a 'Google Chrome' "$run_dir/result.html" || true
      else
        printf 'Краткий отчёт не создан; откройте ссылки выше или JSON-файлы.\n'
        /usr/bin/open -a 'Google Chrome' "$status_url" "$scan_url" || true
      fi
    else
      printf '\nID нового цикла не найден; проверьте журнал и ход проверки.\n'
    fi
  fi
fi

printf '\nКод завершения: %s (0 — завершено, 2 — частично, другое — ошибка).\n' "$run_exit"
printf 'Все сохранённые файлы: %s\n' "$run_dir"
/usr/bin/open "$run_dir" || true
printf 'Нажмите Enter, чтобы закрыть окно.\n'
read -r _unused
exit "$run_exit"
