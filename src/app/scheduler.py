"""Retired container scheduler boundary.

Marketplace cycles are accepted only through the reviewed MacBook interactive
host runner.  This module intentionally exposes no runnable background worker
until a separately approved deployment contract exists.
"""
from __future__ import annotations


def start_scheduler() -> None:
    raise RuntimeError(
        'container scheduler is not accepted; use the reviewed MacBook interactive host runner'
    )
