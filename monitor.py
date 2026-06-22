#!/usr/bin/env python3
"""
Website Monitor — hourly checker
--------------------------------
Run by cron once an hour (`0 * * * *`). Checks every configured site, records
status + history, and prints a one-line summary to stdout (captured to
monitor.log by the cron entry). Exits 0 always so cron stays quiet; failures
are visible via the recorded status, not via exit codes.
"""
from __future__ import annotations

import sys

import monitor_core as mc


def main() -> int:
    results = mc.run_all()
    if not results:
        print(f"[{mc.now_iso()}] no sites configured; nothing to check")
        return 0
    down = [r for r in results if not r["healthy"]]
    for r in results:
        state = "UP  " if r["healthy"] else "DOWN"
        code = r["status_code"] if r["status_code"] is not None else "-"
        lat = f"{r['latency_ms']}ms" if r["latency_ms"] is not None else "-"
        note = f" ({r['error']})" if r["error"] else ""
        print(f"[{r['checked_at']}] {state} {r['name']} [{code}] {lat}{note}")
    print(
        f"[{mc.now_iso()}] checked {len(results)} site(s), "
        f"{len(down)} down"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
