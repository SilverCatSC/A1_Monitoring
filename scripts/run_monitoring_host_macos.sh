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
VPN_ADMISSION_DIR="$ARTIFACTS_DIR/vpn_admission"
VPN_POLICY_PATH="$VPN_ADMISSION_DIR/policy.json"

ENGINES='auto_ru,avito'
PAGES=3
PACE='cautious'
CAPTCHA_WAIT_SECONDS=180
CAPTCHA_WAIT_WAS_SET=0
PREFLIGHT_ONLY=0
ENGINES_WAS_SET=0
PAGES_WAS_SET=0
RETRY_CYCLE_ID=''
WITH_PLACEMENT_ID=0
RUNNER_STARTED_AT=''
RUNNER_PHASE='startup'
SCAN_EXIT_CODE=-1
FINAL_STATUS_WRITTEN=0
SIGNALLED=0
LOCK_HELD=0
LOCK_CONTEXT_REQUESTED="${A1_MONITORING_HOST_LOCK_HELD:-}"
LOCK_FD="${A1_MONITORING_HOST_LOCK_FD:-}"

usage() {
    cat <<'EOF'
Usage: scripts/run_monitoring_host_macos.sh [--preflight]
       scripts/run_monitoring_host_macos.sh [--engines auto_ru,avito|auto_ru|avito] [--pages 1..10] [--captcha-wait-seconds 0..600] [--placement-identity]
       scripts/run_monitoring_host_macos.sh --retry-cycle <completed-partial-or-failed-cycle-uuid>

Runs one cautious monitoring cycle through the signed-in macOS user's visible
Chrome session. It never enters recurring mode or retries a partial result.

--retry-cycle starts one new, explicit retry through the same host, VPN and
visible-Chrome gates. It retains only the prior cycle's provenance; it is not
an automatic retry and cannot be combined with --preflight.

--preflight performs no monitoring cycle: it checks the unlocked signed-in GUI
session, holds the same host lock, starts app/db/backup, and waits for HTTP
readiness. It does not open Chrome, recover cycles, or contact marketplaces.
It cannot be combined with scan parameters.

--placement-identity is a compatibility flag; ID link synchronization now runs
before search in every full cycle.

--captcha-wait-seconds sets a bounded wait for a person to clear Auto.ru CAPTCHA
in the visible private Chrome tab. Default: 180; 0 disables the wait.
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
                ENGINES_WAS_SET=1
                shift 2
                ;;
            --pages)
                [[ $# -ge 2 ]] || { usage >&2; exit 64; }
                PAGES="$2"
                PAGES_WAS_SET=1
                shift 2
                ;;
            --captcha-wait-seconds)
                [[ $# -ge 2 ]] || { usage >&2; exit 64; }
                CAPTCHA_WAIT_SECONDS="$2"
                CAPTCHA_WAIT_WAS_SET=1
                shift 2
                ;;
            --preflight)
                if [[ "$PREFLIGHT_ONLY" -eq 1 ]]; then
                    safe_message 'HOST_RUNNER_REFUSED reason=duplicate_preflight'
                    exit 64
                fi
                PREFLIGHT_ONLY=1
                shift
                ;;
            --placement-identity)
                if [[ "$WITH_PLACEMENT_ID" -eq 1 ]]; then
                    safe_message 'HOST_RUNNER_REFUSED reason=duplicate_placement_identity'
                    exit 64
                fi
                WITH_PLACEMENT_ID=1
                shift
                ;;
            --retry-cycle)
                [[ $# -ge 2 ]] || { usage >&2; exit 64; }
                [[ -z "$RETRY_CYCLE_ID" ]] || {
                    safe_message 'HOST_RUNNER_REFUSED reason=duplicate_retry_cycle'
                    exit 64
                }
                RETRY_CYCLE_ID="$2"
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
    if [[ ! "$CAPTCHA_WAIT_SECONDS" =~ ^[0-9]{1,3}$ ]] || (( CAPTCHA_WAIT_SECONDS > 600 )); then
        safe_message 'HOST_RUNNER_REFUSED reason=invalid_captcha_wait_seconds'
        exit 64
    fi
    if [[ "$PREFLIGHT_ONLY" -eq 1 ]] && \
        { [[ "$ENGINES_WAS_SET" -eq 1 ]] || [[ "$PAGES_WAS_SET" -eq 1 ]] || \
          [[ "$CAPTCHA_WAIT_WAS_SET" -eq 1 ]] || \
          [[ "$WITH_PLACEMENT_ID" -eq 1 ]] || \
          [[ -n "$RETRY_CYCLE_ID" ]]; }; then
        safe_message 'HOST_RUNNER_REFUSED reason=preflight_does_not_accept_scan_parameters'
        exit 64
    fi
    if [[ -n "$RETRY_CYCLE_ID" ]] && \
        [[ ! "$RETRY_CYCLE_ID" =~ ^[[:xdigit:]]{8}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{12}$ ]]; then
        safe_message 'HOST_RUNNER_REFUSED reason=invalid_retry_cycle_id'
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
    # IOConsoleLocked can remain "false" while the current GUI session itself
    # is locked. On this macOS version the per-session lock key is *absent*
    # when unlocked and becomes "true" when the screen is locked. Require an
    # explicit active, completed console session, then allow only absent/false
    # or fail closed: a run must never open Chrome behind the lock screen.
    CONSOLE_SESSION_ACTIVE="$(/usr/sbin/ioreg -n Root -d1 -a 2>/dev/null \
        | /usr/bin/plutil -extract IOConsoleUsers.0.kCGSSessionOnConsoleKey raw - 2>/dev/null || true)"
    CONSOLE_SESSION_LOGGED_IN="$(/usr/sbin/ioreg -n Root -d1 -a 2>/dev/null \
        | /usr/bin/plutil -extract IOConsoleUsers.0.kCGSessionLoginDoneKey raw - 2>/dev/null || true)"
    SESSION_SCREEN_LOCKED="$(/usr/sbin/ioreg -n Root -d1 -a 2>/dev/null \
        | /usr/bin/plutil -extract IOConsoleUsers.0.CGSSessionScreenIsLocked raw - 2>/dev/null || true)"
    if [[ "$CONSOLE_LOCKED" != 'false' || "$CONSOLE_SESSION_ACTIVE" != 'true' || \
          "$CONSOLE_SESSION_LOGGED_IN" != 'true' || \
          ( "$SESSION_SCREEN_LOCKED" != '' && "$SESSION_SCREEN_LOCKED" != 'false' ) ]]; then
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

prepare_vpn_admission_directory() {
    "$PYTHON" -m app.service.vpn_admission --prepare-directory "$VPN_ADMISSION_DIR"
}

require_vpn_operational_policy() {
    "$PYTHON" -m app.service.vpn_admission --operational-policy "$VPN_POLICY_PATH"
}

write_status() {
    local state="$1"
    local phase="$2"
    local scan_exit_code="$3"
    local finished="$4"
    local temporary_path finished_value execution_model run_kind

    case "$state" in
        starting|running|succeeded|partial|failed|interrupted|\
        preflight_succeeded|preflight_failed|preflight_interrupted) ;;
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
    if [[ "$PREFLIGHT_ONLY" -eq 1 ]]; then
        run_kind='preflight'
        execution_model='readiness_only_no_cycle'
    else
        run_kind='full_scan'
        execution_model='one_cycle_per_invocation'
    fi

    {
        printf '{'
        printf '"schema_version":1,'
        printf '"runner":"macos_interactive_host",'
        printf '"run_kind":"%s",' "$run_kind"
        printf '"execution_model":"%s",' "$execution_model"
        if [[ "$PREFLIGHT_ONLY" -eq 1 ]]; then
            printf '"preflight_only":true,'
        fi
        printf '"state":"%s",' "$state"
        printf '"phase":"%s",' "$phase"
        printf '"started_at_utc":"%s",' "$RUNNER_STARTED_AT"
        printf '"updated_at_utc":"%s",' "$(utc_timestamp)"
        if [[ "$PREFLIGHT_ONLY" -eq 0 ]]; then
            printf '"engines":"%s",' "$ENGINES"
            printf '"pages":%s,' "$PAGES"
            printf '"pace":"%s",' "$PACE"
            if [[ -n "$RETRY_CYCLE_ID" ]]; then
                printf '"cycle_mode":"controlled_retry",'
            else
                printf '"cycle_mode":"ordinary",'
            fi
        fi
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
        if [[ "$PREFLIGHT_ONLY" -eq 1 ]]; then
            terminal_state='preflight_failed'
            [[ "$SIGNALLED" -eq 1 ]] && terminal_state='preflight_interrupted'
        else
            terminal_state='failed'
            [[ "$SIGNALLED" -eq 1 ]] && terminal_state='interrupted'
        fi
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
    # The helper is the only authority that may mark inherited lock context.
    # An ambient environment variable alone is never proof of a held lock: a
    # direct invocation must either validate the inherited descriptor or be
    # re-execed through the helper to acquire one.
    if [[ -n "$LOCK_CONTEXT_REQUESTED" || -n "$LOCK_FD" ]]; then
        if [[ "$LOCK_CONTEXT_REQUESTED" != '1' || ! "$LOCK_FD" =~ ^[0-9]+$ ]]; then
            safe_message 'HOST_RUNNER_REFUSED reason=invalid_lock_context'
            exit 1
        fi
        if ! "$PYTHON" "$LOCK_HELPER" --lock-path "$LOCK_PATH" \
            --verify-inherited-fd "$LOCK_FD" >/dev/null 2>&1; then
            safe_message 'HOST_RUNNER_REFUSED reason=invalid_lock_context'
            exit 1
        fi
        LOCK_HELD=1
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

    # The readiness-only preflight deliberately does not create or read a VPN
    # admission record. A full run prepares only the empty private directory
    # before Docker can touch it; the actual record remains owner-authored.
    if [[ "$PREFLIGHT_ONLY" -eq 0 ]]; then
        RUNNER_PHASE='vpn_admission_directory'
        write_status running "$RUNNER_PHASE" "$SCAN_EXIT_CODE" false
        if ! prepare_vpn_admission_directory; then
            safe_message 'HOST_RUNNER_REFUSED reason=vpn_admission_directory'
            return 1
        fi
    fi
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

    if [[ "$PREFLIGHT_ONLY" -eq 1 ]]; then
        # A preflight is intentionally a readiness-only proof. Recheck the
        # console state after Docker/readiness work, but do not recover old
        # cycles, invoke Chrome, create a cycle, or contact a marketplace.
        RUNNER_PHASE='console_recheck'
        write_status running "$RUNNER_PHASE" "$SCAN_EXIT_CODE" false
        require_console_gui_user

        RUNNER_PHASE='preflight_finished'
        write_status preflight_succeeded "$RUNNER_PHASE" "$SCAN_EXIT_CODE" true
        FINAL_STATUS_WRITTEN=1
        safe_message 'HOST_RUNNER_PREFLIGHT_OK no_cycle_created=true'
        return 0
    fi

    # This is a local-only, fail-closed operational policy and VPN status check. It
    # runs after readiness but before recovery, Chrome, or a DB-writing cycle,
    # and does not claim that marketplace egress or M7 acceptance is verified.
    RUNNER_PHASE='vpn_operational_admission'
    write_status running "$RUNNER_PHASE" "$SCAN_EXIT_CODE" false
    if ! require_vpn_operational_policy; then
        safe_message 'HOST_RUNNER_REFUSED reason=vpn_operational_admission_required'
        return 1
    fi

    RUNNER_PHASE='recover_open_cycles'
    write_status running "$RUNNER_PHASE" "$SCAN_EXIT_CODE" false
    # The Docker database is exposed to the host on the private loopback port
    # from .env.  Do not inherit the container-oriented localhost:5432 DSN.
    if ! A1_MONITORING_HOST_RUNNER_CONTEXT=1 \
        run_quietly "$PYTHON" "$ROOT_DIR/scripts/recover_open_cycles.py"; then
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
    local scan_args=(--engines "$ENGINES" --pages "$PAGES" --pace "$PACE"
        --captcha-wait-seconds "$CAPTCHA_WAIT_SECONDS")
    if [[ "$WITH_PLACEMENT_ID" -eq 1 ]]; then
        scan_args+=(--placement-identity)
    fi
    if [[ -n "$RETRY_CYCLE_ID" ]]; then
        scan_args+=(--retry-cycle "$RETRY_CYCLE_ID")
    fi
    if A1_MONITORING_HOST_RUNNER_CONTEXT=1 \
        "$ROOT_DIR/scripts/local_scan.sh" "${scan_args[@]}" \
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
