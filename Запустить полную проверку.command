#!/usr/bin/env bash
# One explicit, foreground MacBook cycle for both marketplaces.
set -uo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$ROOT_DIR" || exit 1

run_stamp="$(/bin/date '+%Y%m%d_%H%M%S')"
run_log="$ROOT_DIR/artifacts/live_both_${run_stamp}.log"
/bin/mkdir -p "$ROOT_DIR/artifacts" || exit 1

printf 'Полная проверка Auto.ru и Avito. VPSUS оставьте включённым.\n'
printf 'Журнал запуска: %s\n' "$run_log"
printf 'Ход проверки: http://127.0.0.1:18000/api/v1/status/scans/progress\n\n'

"$ROOT_DIR/scripts/run_monitoring_host_macos.sh" \
  --engines auto_ru,avito --pages 3 --captcha-wait-seconds 180 \
  2>&1 | /usr/bin/tee "$run_log"
pipeline_status=("${PIPESTATUS[@]}")
run_exit=${pipeline_status[0]}
if [[ "${pipeline_status[1]}" -ne 0 ]]; then
  printf 'Не удалось сохранить журнал запуска.\n' >&2
  run_exit=1
fi

printf '\nКод завершения: %s (0 — завершено, 2 — частично, другое — ошибка).\n' "$run_exit"
printf 'Результат: http://127.0.0.1:18000/api/v1/dashboard/listings\n'
printf 'Журнал: %s\n' "$run_log"
printf 'Нажмите Enter, чтобы закрыть окно.\n'
read -r _unused
exit "$run_exit"
