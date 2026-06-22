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

try:
    import alerts
except Exception:  # alerts are optional; never let an import error stop checks
    alerts = None


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

    # Email alert for any currently-down sites (policy: every hour while down).
    # Wrapped so a mail failure can never break the monitoring sweep.
    if down and alerts is not None:
        try:
            res = alerts.send_down_alert(down, len(results))
            if res.get("sent"):
                vias = ", ".join(
                    f"{r['domain']}→{r['via']}" for r in res.get("results", []) if r.get("sent")
                )
                print(f"[{mc.now_iso()}] alert emailed ({vias})")
            elif alerts.alerts_enabled():
                print(f"[{mc.now_iso()}] alert NOT sent: {res}")
        except Exception as e:  # noqa: BLE001
            print(f"[{mc.now_iso()}] alert error: {e!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
