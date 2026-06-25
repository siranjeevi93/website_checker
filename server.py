#!/usr/bin/env python3
"""
Website Monitor MCP Server
--------------------------
Exposes the on-VM website monitor as MCP tools. The actual hourly checks are
performed by monitor.py (cron, every hour); this server manages the site list
and surfaces the results that the cron job records.

Tools:
  add_site / remove_site / list_sites   - manage what gets monitored
  check_now                             - run an immediate on-demand check
  status                                - latest result per site
  history                               - recent check history
  uptime                                - uptime % over recorded history

Transport: stdio (default). Run directly:  ./venv/bin/python server.py
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

import monitor_core as mc

try:
    import alerts
except Exception:
    alerts = None

mcp = FastMCP("website-monitor")


@mcp.tool()
def add_site(url: str, name: str | None = None, category: str = "external",
             verify_tls: bool = True, resolve_ip: str | None = None) -> dict:
    """Add a website to the monitor. `url` may omit the scheme (defaults to
    https). `name` is an optional friendly label. `category` is "internal" or
    "external" (default external) and controls grouping on the dashboard.
    Set `verify_tls=False` for internal hosts with self-signed/private-CA certs
    (e.g. vCenter, .local appliances) so a cert-trust failure isn't a false
    DOWN. Set `resolve_ip` to connect to a fixed IP while sending the original
    hostname as Host header — for hosts with no DNS record on the monitor (a
    no-sudo alternative to /etc/hosts). Idempotent on url. Takes effect on the
    next hourly run; use check_now to test it immediately."""
    entry = mc.add_site(url, name, category, verify_tls, resolve_ip)
    return {"added": entry, "sites": mc.load_sites()}


@mcp.tool()
def remove_site(url_or_name: str) -> dict:
    """Stop monitoring a site, identified by its URL or friendly name."""
    removed = mc.remove_site(url_or_name)
    return {"removed": removed, "sites": mc.load_sites()}


@mcp.tool()
def list_sites() -> list[dict]:
    """List all configured sites."""
    return mc.load_sites()


@mcp.tool()
def check_now(url: str | None = None) -> dict:
    """Run an immediate check (does not wait for the hourly cron). With `url`,
    checks just that URL (it need not be configured). Without `url`, checks all
    configured sites. Results are recorded to status/history like a cron run."""
    if url is not None:
        url = mc._normalize_url(url)
        # honor the configured site's verify_tls/resolve_ip if we monitor it
        site = next((s for s in mc.load_sites() if s["url"] == url), {})
        res = mc.check_url(url, verify_tls=site.get("verify_tls", True),
                           resolve_ip=site.get("resolve_ip"))
        mc.record_result(res)
        return {"results": [res]}
    return {"results": mc.run_all()}


@mcp.tool()
def status(url: str | None = None) -> dict:
    """Latest recorded result for each site (or just `url` if given)."""
    st = mc.load_status()
    if url is not None:
        url = mc._normalize_url(url)
        return {url: st.get(url)}
    return st


@mcp.tool()
def history(url: str | None = None, limit: int = 50) -> list[dict]:
    """Recent check history, newest last. Optionally filter by `url`."""
    if url is not None:
        url = mc._normalize_url(url)
    return mc.load_history(url=url, limit=limit)


@mcp.tool()
def uptime(url: str | None = None) -> dict:
    """Uptime summary (checks, healthy count, uptime %, last status) computed
    over recorded history, per site."""
    if url is not None:
        url = mc._normalize_url(url)
    return mc.uptime_summary(url=url)


@mcp.tool()
def test_alert() -> dict:
    """Send a test alert email to the configured recipients (WM_ALERT_TO).
    Returns the per-recipient delivery result, or a disabled/unavailable note."""
    if alerts is None:
        return {"sent": False, "reason": "alerts module unavailable"}
    if not alerts.alerts_enabled():
        return {"sent": False, "reason": "alerts disabled (WM_ALERT_TO unset)"}
    return alerts.send_test()


if __name__ == "__main__":
    mcp.run()
