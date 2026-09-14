# M7 controlled MacBook cycle — 2026-09-14

## Scope and authorization

The owner permitted one controlled MacBook cycle under the observed VPSUS
policy: ChatGPT uses the VPN exit; `auto.ru` and `avito.ru` are configured as
VPSUS direct-mode exceptions. The owner-local admission record was verified
with `0700/0600` permissions and a one-hour validity window before any
database recovery, Chrome launch or marketplace request. VPSUS was neither
changed nor reconnected.

The runner used the visible, unlocked MacBook session and exactly one cautious
invocation:

```text
scripts/run_monitoring_host_macos.sh --engines auto_ru,avito --pages 1
```

No CAPTCHA bypass, credential entry, external publication, scheduler trigger
or automatic retry occurred.

## Runtime evidence

| Item | Verified result |
| --- | --- |
| Source revision | `48de30f` |
| Host profile | `local_browser` on MacBook |
| VPN admission | accepted before recovery and browser work |
| Host preflight | successful: unlocked GUI, services and loopback readiness |
| Cycle ID | `527c4ad1-20fd-4967-a814-31ce75fea124` |
| Started / finished (UTC) | `2026-09-14T20:18:02Z` / `2026-09-14T20:40:54Z` |
| Runner terminal state | `partial`, exit code `2`, no automatic retry |
| Marketplace technical errors | `0` |
| Links requiring reconciliation | `22` |
| Incomplete direct-card records | `14` |

An earlier attempt stopped before Chrome or marketplace traffic at
`recover_open_cycles`: the Mac runner had inherited the container-oriented
`localhost:5432` DSN instead of the private Docker loopback port from `.env`.
Commit `48de30f` added a host-aware, lock-verified recovery entrypoint. The
fresh run above passed recovery and reached the controlled browser scan.

## Interpretation

This is a valid negative M7 result, not a failed network/VPN result:

- `technical_errors=0`, including zero marketplace and direct-card technical
  errors;
- `links_need_review=22` and `direct_cards_incomplete=14` keep the cycle
  `partial` by design;
- the runner correctly did not conceal those records as absence, sale or a
  completed production acceptance, and correctly did not retry them.

The run demonstrates that the production MacBook entrypoint, VPN admission,
database recovery and cautious browser workflow operate together. It does not
close M7: an operator must reconcile the flagged records and the owner must
complete the remaining M7 sign-off, including the approved control sample and
any separately requested LaunchAgent gate. A larger or retry cycle must be an
explicit owner decision; it is not an automatic response to this `partial`.
