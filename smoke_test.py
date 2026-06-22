#!/usr/bin/env python3
"""
Smoke test for the website monitor. Uses a temporary, isolated store so it
never touches the live sites.json / status.json / history.jsonl. Performs one
real HTTP check against a stable public endpoint, so it needs outbound network.
Run:  ./venv/bin/python smoke_test.py
"""
import os
import tempfile
from pathlib import Path

# Point the store at a throwaway dir BEFORE importing the core module.
_tmp = Path(tempfile.mkdtemp(prefix="wm-smoke-"))
os.environ["WM_SITES"] = str(_tmp / "sites.json")
os.environ["WM_STATUS"] = str(_tmp / "status.json")
os.environ["WM_HISTORY"] = str(_tmp / "history.jsonl")

import monitor_core as mc  # noqa: E402


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        raise SystemExit(f"smoke test failed: {label}")


def main():
    print("website-monitor smoke test")

    # site list management
    mc.add_site("example.com", "Example")
    mc.add_site("example.com", "Example")  # idempotent
    sites = mc.load_sites()
    check("add_site normalizes scheme + dedupes", len(sites) == 1
          and sites[0]["url"] == "https://example.com")

    # a real check (network)
    res = mc.check_url("https://example.com")
    check("check_url returns structured result", "healthy" in res and "latency_ms" in res)
    check("example.com is reachable + healthy", res["healthy"] is True)
    print(f"        -> status={res['status_code']} latency={res['latency_ms']}ms")

    # bad host fails cleanly (no exception)
    bad = mc.check_url("https://this-host-does-not-exist.invalid")
    check("unreachable host -> healthy False with error", bad["healthy"] is False and bad["error"])

    # record + read back
    mc.record_result(res)
    check("status persisted", mc.load_status().get("https://example.com") is not None)
    check("history persisted", len(mc.load_history()) >= 1)
    up = mc.uptime_summary()
    check("uptime summary computed", up["https://example.com"]["uptime_pct"] is not None)

    # full sweep over configured sites
    results = mc.run_all()
    check("run_all checks configured sites", len(results) == 1)

    mc.remove_site("Example")
    check("remove_site by name works", mc.load_sites() == [])

    print("ALL PASS")


if __name__ == "__main__":
    main()
