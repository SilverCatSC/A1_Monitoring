#!/usr/bin/env python3
"""Retired raw browser diagnostic entry point.

The former helper printed full URLs, DOM fragments and VIN candidates while
opening a headless browser outside the MacBook host-runner/VPN admission
boundary.  That is not compatible with the production privacy and provenance
contract, so it intentionally performs no browser or network operation.
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('url', nargs='?')
    parser.add_argument('--timeout', type=int, default=25)
    parser.parse_args()
    print(
        'PROBE_PAGE_REFUSED reason=raw_headless_diagnostic_not_accepted',
        file=sys.stderr,
    )
    return 64


if __name__ == '__main__':
    raise SystemExit(main())
