#!/usr/bin/env bash
# Run exactly one cautious monitoring cycle from the signed-in macOS GUI user.
#
# This script deliberately keeps browser automation on the host.  It must not
# be run by root, from a launch daemon, or from a user other than the current
# console user: local_scan.sh opens/reuses a visible, loopback-only Chrome CDP
# session. It never enters recurring mode and deliberately does not retry a
# partial marketplace result in the same invocation.

set -euo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
ARTIFACTS_DIR="$ROOT_DIR/artifacts"
STATUS_PATH="$ARTIFACTS_DIR/monitoring_host_runner_macos_status.json"
LOCK_PATH="$ARTIFACTS_DIR/.monitoring_host_runner_macos.lock"
LOCK_HELPER="$ROOT_DIR/scripts/with_monitoring_host_lock_macos.py"

ENGINES='auto_ru,avito'
PAGES=3
PACE='cautious'
RUNNER_STARTED_AT=''
RUNNER_PHASE='startup'
SCAN_EXIT_CODE=-1
FINAL_STATUS_WRITTEN=0
SIGNALLED=0
LOCK_HELD="${A1_MONITORING_HOST_LOCK_HELD:-0}"
LOCK_FD="${A1_MONITORING_HOST_LOCK_FD:-}"

usage() {
    cat <<'EOF'
Usage: scripts/run_monitoring_host_macos.sh [--engines auto_ru,avito|auto_ru|avito] [--pages 1..10]

Runs one cautious monitoring cycle through the signed-in macOS user's visible
Chrome session. It never enters recurring mode or retries a partial result.
EOF
}

safe_message() {
    # Do not print command output or exception text: this script is also used
    # by launchd, whose logs must not become a source of private data.
    printf '%s\n' "$1" >&2
}

utc_timestamp() {
    /bin/date -u '+%Y-%m-%dT%H:%M:%SZ'
}

parse_arguments() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --engines)
                [[ $# -ge 2 ]] || { usage >&2; exit 64; }
                ENGINES="$2"
                shift 2
                ;;
            --pages)
                [[ $# -ge 2 ]] || { usage >&2; exit 64; }
                PAGES="$2"
                shift 2
                ;;
            --help|-h)
                usage
                exit 0
                ;;
            *)
                usage >&2
                exit 64
                ;;
        esac
    done

    case "$ENGINES" in
        auto_ru,avito|auto_ru|avito) ;;
        *) safe_message 'HOST_RUNNER_REFUSED reason=invalid_engines'; exit 64 ;;
    esac
    if [[ ! "$PAGES" =~ ^([1-9]|10)$ ]]; then
        safe_message 'HOST_RUNNER_REFUSED reason=invalid_pages'
        exit 64
    fi
}

require_console_gui_user() {
    if [[ "$(/usr/bin/uname -s)" != 'Darwin' ]]; then
        safe_message 'HOST_RUNNER_REFUSED reason=macos_required'
        exit 1
    fi
    if [[ "$EUID" -eq 0 ]]; then
        safe_message 'HOST_RUNNER_REFUSED reason=root_not_allowed'
        exit 1
    fi

    CONSOLE_USER="$(/usr/bin/stat -f '%Su' /dev/console 2>/dev/null || true)"
    case "$CONSOLE_USER" in
        ''|root|loginwindow|_mbsetupuser)
            safe_message 'HOST_RUNNER_REFUSED reason=console_gui_user_required'
            exit 1
            ;;
    esac
    CONSOLE_UID="$(/usr/bin/id -u "$CONSOLE_USER" 2>/dev/null || true)"
    if [[ ! "$CONSOLE_UID" =~ ^[0-9]+$ ]] || [[ "$CONSOLE_UID" != "$EUID" ]]; then
        safe_message 'HOST_RUNNER_REFUSED reason=console_user_mismatch'
        exit 1
    fi
    if ! /bin/launchctl print "gui/$CONSOLE_UID" >/dev/null 2>&1; then
        safe_message 'HOST_RUNNER_REFUSED reason=gui_launchd_domain_unavailable'
        exit 1
    fi
    CONSOLE_LOCKED="$(/usr/sbin/ioreg -n Root -d1 -a 2>/dev/null \
        | /usr/bin/plutil -extract IOConsoleLocked raw - 2>/dev/null || true)"
    if [[ "$CONSOLE_LOCKED" != 'false' ]]; then
        safe_message 'HOST_RUNNER_REFUSED reason=screen_locked_or_state_unavailable'
        exit 1
    fi
}

select_python() {
    if [[ -x "$ROOT_DIR/.venv312/bin/python" ]]; then
        PYTHON="$ROOT_DIR/.venv312/bin/python"
    elif [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
        PYTHON="$ROOT_DIR/.venv/bin/python"
    else
        safe_message 'HOST_RUNNER_REFUSED reason=python_environment_missing'
        exit 1
    fi
}

select_compose() {
    if command -v docker-compose >/dev/null 2>&1; then
        COMPOSE=(docker-compose)
        return
    fi
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        COMPOSE=(docker compose)
        return
    fi
    safe_message 'HOST_RUNNER_REFUSED reason=docker_compose_unavailable'
    exit 1
}

write_status() {
    local state="$1"
    local phase="$2"
    local scan_exit_code="$3"
    local finished="$4"
    local temporary_path finished_value

    case "$state" in
        starting|running|succeeded|partial|failed|interrupted) ;;
        *) return 1 ;;
    esac
    case "$finished" in
        true|false) ;;
        *) return 1 ;;
    esac

    /bin/mkdir -p "$ARTIFACTS_DIR"
    temporary_path="$(/usr/bin/mktemp "$ARTIFACTS_DIR/.monitoring_host_runner_macos_status.XXXXXX")"
    finished_value=false
    [[ "$finished" == true ]] && finished_value=true

    {
        printf '{'
        printf '"schema_version":1,'
        printf '"runner":"macos_interactive_host",'
        printf '"execution_model":"one_cycle_per_invocation",'
        printf '"state":"%s",' "$state"
        printf '"phase":"%s",' "$phase"
        printf '"started_at_utc":"%s",' "$RUNNER_STARTED_AT"
        printf '"updated_at_utc":"%s",' "$(utc_timestamp)"
        printf '"engines":"%s",' "$ENGINES"
        printf '"pages":%s,' "$PAGES"
        printf '"pace":"%s",' "$PACE"
        printf '"lock_mode":"kernel_fcntl"'
        if [[ "$scan_exit_code" -ge 0 ]]; then
            printf ',"scan_exit_code":%s' "$scan_exit_code"
        fi
        if [[ "$finished_value" == true ]]; then
            printf ',"finished_at_utc":"%s"' "$(utc_timestamp)"
        fi
        printf '}\n'
    } >"$temporary_path"
    /bin/chmod 600 "$temporary_path"
    /bin/mv -f "$temporary_path" "$STATUS_PATH"
    safe_message "HOST_RUNNER_STATUS state=$state phase=$phase"
}

cleanup() {
    local exit_code="$?" terminal_state
    trap - EXIT
    set +e
    if [[ "$LOCK_HELD" == '1' && "$FINAL_STATUS_WRITTEN" -eq 0 ]]; then
        terminal_state='failed'
        [[ "$SIGNALLED" -eq 1 ]] && terminal_state='interrupted'
        write_status "$terminal_state" "$RUNNER_PHASE" "$SCAN_EXIT_CODE" true || \
            safe_message 'HOST_RUNNER_STATUS_WRITE_FAILED'
    fi
    exit "$exit_code"
}

trap cleanup EXIT
trap 'SIGNALLED=1; exit 130' INT
trap 'SIGNALLED=1; exit 143' TERM
trap 'SIGNALLED=1; exit 129' HUP

run_quietly() {
    "$@" >/dev/null 2>&1
}

acquire_kernel_lock_and_reexec() {
    if [[ "$LOCK_HELD" == '1' ]]; then
        if [[ ! "$LOCK_FD" =~ ^[0-9]+$ ]]; then
            safe_message 'HOST_RUNNER_REFUSED reason=invalid_lock_context'
            exit 1
        fi
        return
    fi
    if [[ ! -x "$LOCK_HELPER" ]]; then
        safe_message 'HOST_RUNNER_REFUSED reason=lock_helper_missing'
        exit 1
    fi

    # The helper holds a non-blocking kernel fcntl lock and execs this script.
    # It is released automatically when the last lock-holding process exits;
    # unlike a PID directory, no time-based stale-lock deletion is required.
    exec "$PYTHON" "$LOCK_HELPER" --lock-path "$LOCK_PATH" -- \
        "$ROOT_DIR/scripts/run_monitoring_host_macos.sh" "$@"
}

main() {
    parse_arguments "$@"
    require_console_gui_user
    cd "$ROOT_DIR"
    RUNNER_STARTED_AT="$(utc_timestamp)"
    select_python
    acquire_kernel_lock_and_reexec "$@"
    write_status starting preflight "$SCAN_EXIT_CODE" false
    select_compose

    RUNNER_PHASE='services'
    write_status running "$RUNNER_PHASE" "$SCAN_EXIT_CODE" false
    if ! run_quietly "${COMPOSE[@]}" up -d app db backup; then
        safe_message 'HOST_RUNNER_FAILED phase=services'
        return 1
    fi

    RUNNER_PHASE='readiness'
    write_status running "$RUNNER_PHASE" "$SCAN_EXIT_CODE" false
    if ! run_quietly "$PYTHON" scripts/doctor.py --http --wait; then
        safe_message 'HOST_RUNNER_FAILED phase=readiness'
        return 1
    fi

    RUNNER_PHASE='recover_open_cycles'
    write_status running "$RUNNER_PHASE" "$SCAN_EXIT_CODE" false
    if ! run_quietly "$PYTHON" -m app.cli recover-open-cycles; then
        safe_message 'HOST_RUNNER_FAILED phase=recover_open_cycles'
        return 1
    fi

    # Docker/readiness/recovery can take long enough for the user to lock the
    # screen or switch console sessions. Revalidate at the actual browser
    # boundary so a scheduled invocation never opens Chrome invisibly.
    RUNNER_PHASE='browser_preflight'
    write_status running "$RUNNER_PHASE" "$SCAN_EXIT_CODE" false
    require_console_gui_user

    RUNNER_PHASE='scan'
    write_status running "$RUNNER_PHASE" "$SCAN_EXIT_CODE" false
    if "$ROOT_DIR/scripts/local_scan.sh" --engines "$ENGINES" --pages "$PAGES" --pace "$PACE" \
        >/dev/null 2>&1; then
        SCAN_EXIT_CODE=0
    else
        SCAN_EXIT_CODE=$?
    fi

    if [[ "$SCAN_EXIT_CODE" -eq 0 ]]; then
        write_status succeeded scan_finished "$SCAN_EXIT_CODE" true
        FINAL_STATUS_WRITTEN=1
        safe_message 'HOST_RUNNER_OK'
        return 0
    fi
    if [[ "$SCAN_EXIT_CODE" -eq 2 ]]; then
        # A partial scan goes to review; re-sending marketplace traffic in the
        # same invocation would hide a meaningful technical or review signal.
        write_status partial scan_finished "$SCAN_EXIT_CODE" true
        FINAL_STATUS_WRITTEN=1
        safe_message 'HOST_RUNNER_PARTIAL no_automatic_retry=true'
        return 2
    fi

    write_status failed scan_finished "$SCAN_EXIT_CODE" true
    FINAL_STATUS_WRITTEN=1
    safe_message 'HOST_RUNNER_FAILED phase=scan_finished'
    return 1
}

main "$@"
