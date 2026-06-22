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


def _emit_alert(kind: str, send_fn) -> None:
    """Run an alert sender, log the outcome, never raise."""
    try:
        res = send_fn()
        if res.get("sent"):
            vias = ", ".join(
                f"{r['domain']}→{r['via']}" for r in res.get("results", []) if r.get("sent")
            )
            print(f"[{mc.now_iso()}] {kind} emailed ({vias})")
        elif alerts.alerts_enabled():
            print(f"[{mc.now_iso()}] {kind} NOT sent: {res}")
    except Exception as e:  # noqa: BLE001
        print(f"[{mc.now_iso()}] {kind} error: {e!r}")


def main() -> int:
    # Capture prior health BEFORE the sweep so we can detect down->up recoveries
    # (run_all overwrites status.json as it checks).
    prev_healthy = {u: v.get("healthy") for u, v in mc.load_status().items()}

    results = mc.run_all()
    if not results:
        print(f"[{mc.now_iso()}] no sites configured; nothing to check")
        return 0
    down = [r for r in results if not r["healthy"]]
    recovered = [r for r in results
                 if r["healthy"] and prev_healthy.get(r["url"]) is False]
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

    # Email alerts (wrapped so a mail failure can never break the sweep):
    #  - down alert for any currently-down sites (policy: every hour while down)
    #  - recovery alert once for each site that transitioned down -> up
    if alerts is not None:
        if down:
            _emit_alert("down-alert", lambda: alerts.send_down_alert(down, len(results)))
        if recovered:
            _emit_alert("recovery", lambda: alerts.send_recovery_alert(recovered, len(results)))

    return 0


if __name__ == "__main__":
    sys.exit(main())
