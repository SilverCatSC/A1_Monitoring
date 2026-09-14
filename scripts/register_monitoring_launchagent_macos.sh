#!/usr/bin/env bash
# Register the macOS host runner as a conservative, per-user LaunchAgent.
#
# The default is plan-only.  --apply writes and bootstraps a LaunchAgent into
# the current console user's GUI domain, but it never invokes a marketplace
# scan at registration time.

set -euo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
LABEL='com.silvercatsc.a1monitoring.interactive-cycle'
RUNNER_PATH="$ROOT_DIR/scripts/run_monitoring_host_macos.sh"
ENGINES='auto_ru,avito'
PAGES=3
AT='09:00'
APPLY=0

usage() {
    cat <<'EOF'
Usage: scripts/register_monitoring_launchagent_macos.sh [--at HH:MM] [--engines auto_ru,avito|auto_ru|avito] [--pages 1..10] [--apply]

Without --apply, prints the per-user LaunchAgent plan and changes nothing.
With --apply, installs the job for the signed-in macOS console user. It does
not run the monitoring cycle during registration.
EOF
}

safe_message() {
    printf '%s\n' "$1" >&2
}

parse_arguments() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --at)
                [[ $# -ge 2 ]] || { usage >&2; exit 64; }
                AT="$2"
                shift 2
                ;;
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
            --apply)
                APPLY=1
                shift
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

    if [[ ! "$AT" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then
        safe_message 'LAUNCHAGENT_REGISTRATION_REFUSED reason=invalid_time'
        exit 64
    fi
    case "$ENGINES" in
        auto_ru,avito|auto_ru|avito) ;;
        *) safe_message 'LAUNCHAGENT_REGISTRATION_REFUSED reason=invalid_engines'; exit 64 ;;
    esac
    if [[ ! "$PAGES" =~ ^([1-9]|10)$ ]]; then
        safe_message 'LAUNCHAGENT_REGISTRATION_REFUSED reason=invalid_pages'
        exit 64
    fi
}

require_console_gui_user() {
    if [[ "$(/usr/bin/uname -s)" != 'Darwin' ]]; then
        safe_message 'LAUNCHAGENT_REGISTRATION_REFUSED reason=macos_required'
        exit 1
    fi
    if [[ "$EUID" -eq 0 ]]; then
        safe_message 'LAUNCHAGENT_REGISTRATION_REFUSED reason=root_not_allowed'
        exit 1
    fi

    CONSOLE_USER="$(/usr/bin/stat -f '%Su' /dev/console 2>/dev/null || true)"
    case "$CONSOLE_USER" in
        ''|root|loginwindow|_mbsetupuser)
            safe_message 'LAUNCHAGENT_REGISTRATION_REFUSED reason=console_gui_user_required'
            exit 1
            ;;
    esac
    CONSOLE_UID="$(/usr/bin/id -u "$CONSOLE_USER" 2>/dev/null || true)"
    if [[ ! "$CONSOLE_UID" =~ ^[0-9]+$ ]] || [[ "$CONSOLE_UID" != "$EUID" ]]; then
        safe_message 'LAUNCHAGENT_REGISTRATION_REFUSED reason=console_user_mismatch'
        exit 1
    fi
    if ! /bin/launchctl print "gui/$CONSOLE_UID" >/dev/null 2>&1; then
        safe_message 'LAUNCHAGENT_REGISTRATION_REFUSED reason=gui_launchd_domain_unavailable'
        exit 1
    fi
    CONSOLE_LOCKED="$(/usr/sbin/ioreg -n Root -d1 -a 2>/dev/null \
        | /usr/bin/plutil -extract IOConsoleLocked raw - 2>/dev/null || true)"
    # The system-wide console flag alone is insufficient on current macOS:
    # the active GUI session may report its own lock independently. Refuse
    # registration unless both signals explicitly say the session is unlocked.
    SESSION_SCREEN_LOCKED="$(/usr/sbin/ioreg -n Root -d1 -a 2>/dev/null \
        | /usr/bin/plutil -extract IOConsoleUsers.0.CGSSessionScreenIsLocked raw - 2>/dev/null || true)"
    if [[ "$CONSOLE_LOCKED" != 'false' || "$SESSION_SCREEN_LOCKED" != 'false' ]]; then
        safe_message 'LAUNCHAGENT_REGISTRATION_REFUSED reason=screen_locked_or_state_unavailable'
        exit 1
    fi

    CONSOLE_HOME="$(/usr/bin/dscl . -read "/Users/$CONSOLE_USER" NFSHomeDirectory 2>/dev/null \
        | /usr/bin/awk '/NFSHomeDirectory:/ { print $2; exit }')"
    if [[ -z "$CONSOLE_HOME" || ! -d "$CONSOLE_HOME" ]]; then
        safe_message 'LAUNCHAGENT_REGISTRATION_REFUSED reason=console_home_unavailable'
        exit 1
    fi
}

xml_escape() {
    local value="$1"
    value=${value//&/\&amp;}
    value=${value//</\&lt;}
    value=${value//>/\&gt;}
    value=${value//\"/\&quot;}
    value=${value//\'/\&apos;}
    printf '%s' "$value"
}

write_plist() {
    local destination="$1"
    local hour minute
    hour="${AT%%:*}"
    minute="${AT##*:}"

    cat >"$destination" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$(xml_escape "$LABEL")</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$(xml_escape "$RUNNER_PATH")</string>
    <string>--engines</string>
    <string>$(xml_escape "$ENGINES")</string>
    <string>--pages</string>
    <string>$(xml_escape "$PAGES")</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$(xml_escape "$ROOT_DIR")</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>$((10#$hour))</integer>
    <key>Minute</key>
    <integer>$((10#$minute))</integer>
  </dict>
  <key>RunAtLoad</key>
  <false/>
  <key>KeepAlive</key>
  <false/>
  <key>StandardOutPath</key>
  <string>$(xml_escape "$STDOUT_LOG")</string>
  <key>StandardErrorPath</key>
  <string>$(xml_escape "$STDERR_LOG")</string>
</dict>
</plist>
EOF
}

print_plan() {
    printf '%s\n' 'LAUNCHAGENT_REGISTRATION_PLAN_ONLY: pass --apply to install without starting a scan.'
    printf 'label=%s\n' "$LABEL"
    printf 'domain=gui/%s\n' "$CONSOLE_UID"
    printf 'plist=%s\n' "$PLIST_PATH"
    printf 'daily_time=%s\n' "$AT"
    printf 'run_at_load=false\n'
    printf 'keep_alive=false\n'
    printf 'automatic_retry=false\n'
    printf 'invocation=one_cautious_visible_chrome_cycle\n'
    printf 'registration_starts_marketplace_scan=false\n'
}

main() {
    parse_arguments "$@"
    require_console_gui_user
    if [[ ! -x "$RUNNER_PATH" ]]; then
        safe_message 'LAUNCHAGENT_REGISTRATION_REFUSED reason=runner_missing_or_not_executable'
        exit 1
    fi

    AGENTS_DIR="$CONSOLE_HOME/Library/LaunchAgents"
    PLIST_PATH="$AGENTS_DIR/$LABEL.plist"
    LOG_DIR="$ROOT_DIR/artifacts/launchagent"
    STDOUT_LOG="$LOG_DIR/monitoring_host_runner_macos.stdout.log"
    STDERR_LOG="$LOG_DIR/monitoring_host_runner_macos.stderr.log"

    if [[ "$APPLY" -eq 0 ]]; then
        print_plan
        return 0
    fi

    # Do not displace a loaded job or overwrite a plist another invocation may
    # own. Replacing a LaunchAgent can terminate an active visible-browser run.
    if /bin/launchctl print "gui/$CONSOLE_UID/$LABEL" >/dev/null 2>&1 || \
        [[ -e "$PLIST_PATH" ]]; then
        safe_message 'LAUNCHAGENT_REGISTRATION_REFUSED reason=existing_launchagent_requires_explicit_retirement'
        exit 1
    fi

    /bin/mkdir -p "$AGENTS_DIR" "$LOG_DIR"
    /bin/chmod 700 "$LOG_DIR"
    /usr/bin/touch "$STDOUT_LOG" "$STDERR_LOG"
    /bin/chmod 600 "$STDOUT_LOG" "$STDERR_LOG"

    temporary_plist="$(/usr/bin/mktemp "$AGENTS_DIR/.${LABEL}.XXXXXX")"
    if ! write_plist "$temporary_plist" || ! /usr/bin/plutil -lint "$temporary_plist" >/dev/null 2>&1; then
        /bin/rm -f "$temporary_plist" >/dev/null 2>&1 || true
        safe_message 'LAUNCHAGENT_REGISTRATION_FAILED phase=plist_validation'
        exit 1
    fi
    /bin/chmod 600 "$temporary_plist"

    # link(2) refuses an existing destination, preserving the fail-closed
    # no-replacement rule even if another registration races this process.
    if ! /bin/ln "$temporary_plist" "$PLIST_PATH"; then
        /bin/rm -f "$temporary_plist" >/dev/null 2>&1 || true
        safe_message 'LAUNCHAGENT_REGISTRATION_FAILED phase=publish_plist'
        exit 1
    fi
    /bin/rm -f "$temporary_plist" >/dev/null 2>&1 || true
    if ! /bin/launchctl bootstrap "gui/$CONSOLE_UID" "$PLIST_PATH" >/dev/null 2>&1; then
        # The earlier no-replacement guard proves this file was published by
        # this invocation. Removing it permits a later safe retry without
        # touching any pre-existing LaunchAgent definition.
        /bin/rm -f "$PLIST_PATH" >/dev/null 2>&1 || true
        safe_message 'LAUNCHAGENT_REGISTRATION_FAILED phase=bootstrap'
        exit 1
    fi

    safe_message 'LAUNCHAGENT_REGISTRATION_APPLIED registration_starts_marketplace_scan=false'
    print_plan
}

main "$@"
