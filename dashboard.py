#!/usr/bin/env python3
"""
Website Monitor — dashboard
---------------------------
A read-only Flask page that shows the latest check results in two panes:
Internal on the left, External on the right. Reads the same store the hourly
cron writes (status.json / sites.json), so it always reflects the most recent
sweep.

  GET  /            HTML dashboard (auto-refreshes every 60s)
  GET  /api/status  JSON: sites + latest results, grouped by category
  POST /check       run an immediate sweep, then redirect back to /

Listens on 0.0.0.0:8090 (port 8080 is the ova-deployer webapp — left alone).
Process is named dashboard.py (not webapp.py) so it never collides with the
ova-deployer watchdog's pgrep pattern.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from flask import Flask, jsonify, redirect, render_template_string

import monitor_core as mc

app = Flask(__name__)
PORT = int(os.environ.get("WM_WEB_PORT", "8090"))
# Sub-title shown under the dashboard heading. Override per-deployment so the
# repo carries no environment-specific (internal) host details.
MONITOR_LABEL = os.environ.get("WM_MONITOR_LABEL", "Hourly availability monitoring")

CATEGORIES = ("internal", "external")


def _ago(iso: str | None) -> str | None:
    """Human 'time since' for an ISO timestamp."""
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    delta = datetime.now(timezone.utc) - then
    secs = int(delta.total_seconds())
    if secs < 0:
        secs = 0
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h {secs % 3600 // 60}m ago"
    return f"{secs // 86400}d ago"


def _fmt_when(iso: str | None) -> str | None:
    """Compact display: 'Jun 22 09:25 UTC'."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return dt.strftime("%b %d %H:%M UTC")


def _grouped() -> dict:
    """Merge configured sites with their latest status, grouped by category."""
    status = mc.load_status()
    uptime = mc.uptime_summary()
    groups: dict[str, list[dict]] = {c: [] for c in CATEGORIES}
    for site in mc.load_sites():
        url = site["url"]
        cat = site.get("category", "external")
        if cat not in groups:
            groups[cat] = []
        last = status.get(url)
        up = uptime.get(url, {})
        checked_at = last.get("checked_at") if last else None
        groups[cat].append({
            "name": site.get("name", url),
            "url": url,
            "healthy": last.get("healthy") if last else None,
            "status_code": last.get("status_code") if last else None,
            "latency_ms": last.get("latency_ms") if last else None,
            "checked_at": checked_at,
            "checked_when": _fmt_when(checked_at),
            "checked_ago": _ago(checked_at),
            "error": last.get("error") if last else None,
            "uptime_pct": up.get("uptime_pct"),
            "checks": up.get("checks", 0),
        })
    for c in groups:
        groups[c].sort(key=lambda s: s["name"].lower())
    return groups


def _counts(items: list[dict]) -> dict:
    up = sum(1 for s in items if s["healthy"] is True)
    down = sum(1 for s in items if s["healthy"] is False)
    return {"total": len(items), "up": up, "down": down}


PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="60">
  <title>Website Monitor</title>
  <style>
    :root { color-scheme: dark; }
    * { box-sizing: border-box; }
    body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
           margin: 0; background: radial-gradient(1200px 600px at 50% -10%, #1a2030, #0d0f14 60%);
           color: #e7e9ee; min-height: 100vh; }
    header { padding: 22px 32px; border-bottom: 1px solid #232834;
             display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 14px; }
    .brand { display: flex; align-items: center; gap: 12px; }
    .brand h1 { font-size: 20px; margin: 0; font-weight: 650; letter-spacing: .2px; }
    .brand .dot { width: 10px; height: 10px; border-radius: 50%; background: #2ecc71;
                  box-shadow: 0 0 0 4px rgba(46,204,113,.15); }
    .sub { color: #8b93a7; font-size: 13px; margin-top: 3px; }
    .actions { display: flex; align-items: center; gap: 16px; }
    .total { font-size: 13px; color: #b6bdcc; }
    .total b.ok { color: #2ecc71; } .total b.bad { color: #ff6b6d; }
    form { margin: 0; }
    button { background: #2b6cff; color: #fff; border: 0; padding: 9px 18px;
             border-radius: 9px; font-size: 14px; font-weight: 600; cursor: pointer; }
    button:hover { background: #1f5bdb; }

    main { padding: 26px 32px 40px; max-width: 1400px; margin: 0 auto;
           display: grid; grid-template-columns: 1fr 1fr; gap: 22px; align-items: start; }
    @media (max-width: 860px) { main { grid-template-columns: 1fr; } }

    .pane { background: rgba(20,24,32,.6); border: 1px solid #232834; border-radius: 16px;
            padding: 18px 18px 22px; }
    .pane-head { display: flex; align-items: center; justify-content: space-between;
                 padding: 4px 6px 14px; border-bottom: 1px solid #232834; margin-bottom: 16px; }
    .pane-title { font-size: 13px; text-transform: uppercase; letter-spacing: .12em;
                  font-weight: 700; display: flex; align-items: center; gap: 9px; }
    .tag { font-size: 11px; padding: 2px 8px; border-radius: 999px; font-weight: 700; }
    .tag.internal { background: rgba(108,152,255,.16); color: #8fb0ff; }
    .tag.external { background: rgba(186,140,255,.16); color: #c4a3ff; }
    .pane-counts { font-size: 12px; color: #9aa2b4; }
    .pane-counts b.ok { color: #2ecc71; } .pane-counts b.bad { color: #ff6b6d; }

    .card { background: #161a22; border: 1px solid #242a36; border-radius: 13px;
            padding: 15px 16px; border-left: 4px solid #4b5161; margin-bottom: 12px; }
    .card:last-child { margin-bottom: 0; }
    .card.up { border-left-color: #2ecc71; }
    .card.down { border-left-color: #ff4d4f; }
    .card.unknown { border-left-color: #f0ad4e; }
    .crow { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
    .name { font-weight: 650; font-size: 15px; }
    .pill { font-size: 11px; font-weight: 800; padding: 3px 10px; border-radius: 999px; letter-spacing: .03em; }
    .pill.up { background: rgba(46,204,113,.16); color: #2ecc71; }
    .pill.down { background: rgba(255,77,79,.16); color: #ff6b6d; }
    .pill.unknown { background: rgba(240,173,78,.16); color: #f0ad4e; }
    .url { color: #8b93a7; font-size: 12.5px; word-break: break-all; margin: 7px 0 12px; }
    .url a { color: #6ea8ff; text-decoration: none; }
    .url a:hover { text-decoration: underline; }
    .stats { display: flex; gap: 16px; font-size: 12.5px; color: #aeb6c6; flex-wrap: wrap; }
    .stats b { color: #e7e9ee; font-weight: 650; }
    .err { color: #ff9a9c; font-size: 12px; margin-top: 9px; background: rgba(255,77,79,.08);
           padding: 6px 9px; border-radius: 7px; }
    .checked { display: flex; align-items: center; gap: 7px; margin-top: 11px;
               padding-top: 10px; border-top: 1px solid #20252f; font-size: 12px; color: #8b93a7; }
    .checked .clock { opacity: .7; }
    .checked b { color: #c5ccda; font-weight: 600; }
    .empty { color: #6b7280; font-style: italic; padding: 10px 6px; }
    footer { color: #5b6172; font-size: 12px; padding: 0 32px 30px; max-width: 1400px; margin: 0 auto; }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <span class="dot"></span>
      <div>
        <h1>Website Monitor</h1>
        <div class="sub">{{ monitor_label }} · auto-refreshes every 60s</div>
      </div>
    </div>
    <div class="actions">
      <span class="total">
        <b class="ok">{{ grand.up }} up</b> · <b class="bad">{{ grand.down }} down</b> · {{ grand.total }} total
      </span>
      <form method="post" action="/check"><button type="submit">↻ Check now</button></form>
    </div>
  </header>

  <main>
    {% for cat in categories %}
      <section class="pane">
        <div class="pane-head">
          <span class="pane-title"><span class="tag {{ cat }}">{{ cat }}</span> Sites</span>
          <span class="pane-counts">
            {{ counts[cat].total }} site{{ '' if counts[cat].total == 1 else 's' }} ·
            <b class="ok">{{ counts[cat].up }}↑</b> <b class="bad">{{ counts[cat].down }}↓</b>
          </span>
        </div>
        {% if groups[cat] %}
          {% for s in groups[cat] %}
            {% set state = 'up' if s.healthy else ('down' if s.healthy is not none else 'unknown') %}
            <div class="card {{ state }}">
              <div class="crow">
                <span class="name">{{ s.name }}</span>
                <span class="pill {{ state }}">{{ 'UP' if state=='up' else ('DOWN' if state=='down' else 'NO DATA') }}</span>
              </div>
              <div class="url"><a href="{{ s.url }}" target="_blank" rel="noopener">{{ s.url }}</a></div>
              <div class="stats">
                <span>HTTP <b>{{ s.status_code if s.status_code is not none else '—' }}</b></span>
                <span>Latency <b>{{ ('%.0f ms' % s.latency_ms) if s.latency_ms is not none else '—' }}</b></span>
                <span>Uptime <b>{{ ('%.1f%%' % s.uptime_pct) if s.uptime_pct is not none else '—' }}</b></span>
                <span>Checks <b>{{ s.checks }}</b></span>
              </div>
              {% if s.error %}<div class="err">{{ s.error }}</div>{% endif %}
              <div class="checked">
                <span class="clock">🕓</span>
                {% if s.checked_when %}
                  Last checked <b>{{ s.checked_when }}</b> · {{ s.checked_ago }}
                {% else %}
                  Not checked yet
                {% endif %}
              </div>
            </div>
          {% endfor %}
        {% else %}
          <div class="empty">No {{ cat }} sites configured.</div>
        {% endif %}
      </section>
    {% endfor %}
  </main>
  <footer>website-monitor-mcp · add sites with the add_site MCP tool (category: internal | external)</footer>
</body>
</html>
"""


@app.route("/")
def index():
    groups = _grouped()
    counts = {c: _counts(groups.get(c, [])) for c in CATEGORIES}
    grand = {
        "total": sum(c["total"] for c in counts.values()),
        "up": sum(c["up"] for c in counts.values()),
        "down": sum(c["down"] for c in counts.values()),
    }
    return render_template_string(
        PAGE, groups=groups, counts=counts, grand=grand,
        categories=CATEGORIES, monitor_label=MONITOR_LABEL,
    )


@app.route("/api/status")
def api_status():
    return jsonify(_grouped())


@app.route("/check", methods=["POST"])
def check():
    mc.run_all()
    return redirect("/")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
