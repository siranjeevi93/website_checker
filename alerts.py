#!/usr/bin/env python3
"""
Website Monitor — email alerts (direct-to-MX, no relay/MTA required)
--------------------------------------------------------------------
Sends a down-alert email by talking SMTP directly to the recipient domain's
MX on port 25, with opportunistic STARTTLS. This needs no local mail server
(Postfix/sendmail) and no SMTP relay — it works wherever outbound :25 to the
recipient's MX is allowed and that MX accepts the host's mail.

Configuration is read from the environment, optionally seeded from a local
`alert.env` file (KEY=VALUE lines) so no addresses live in the repo:

  WM_ALERT_TO        comma-separated recipients. EMPTY => alerts disabled.
  WM_ALERT_FROM      envelope/From address (default website-monitor@<fqdn>)
  WM_SMTP_HOST       optional smarthost; bypasses MX lookup if set
  WM_SMTP_TIMEOUT    per-connection timeout, seconds (default 30)
  WM_ALERT_ENV       path to the env file (default ./alert.env)

CLI:
  python alerts.py --test     send a test email to WM_ALERT_TO
"""
from __future__ import annotations

import os
import re
import socket
import ssl
import smtplib
import subprocess
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = Path(os.environ.get("WM_ALERT_ENV", BASE_DIR / "alert.env"))


def _load_env_file() -> None:
    """Seed os.environ from alert.env (existing env vars win)."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_env_file()


def _recipients() -> list[str]:
    raw = os.environ.get("WM_ALERT_TO", "").strip()
    return [r.strip() for r in raw.split(",") if r.strip()]


def _sender() -> str:
    return os.environ.get("WM_ALERT_FROM", "website-monitor@" + socket.getfqdn())


def _timeout() -> float:
    return float(os.environ.get("WM_SMTP_TIMEOUT", "30"))


def alerts_enabled() -> bool:
    return bool(_recipients())


# ---- MX resolution (no external deps; shells out to nslookup) ---------------

def _resolve_mx(domain: str) -> list[str]:
    override = os.environ.get("WM_SMTP_HOST", "").strip()
    if override:
        return [override]
    try:
        out = subprocess.run(
            ["nslookup", "-query=mx", domain],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        out = ""
    mxs: list[tuple[int, str]] = []
    for m in re.finditer(r"mail exchanger\s*=\s*(\d+)\s+(\S+?)\.?\s*$", out, re.M):
        mxs.append((int(m.group(1)), m.group(2)))
    mxs.sort()
    hosts = [h for _, h in mxs]
    return hosts or [domain]  # fallback: try the domain's A record


# ---- low-level send ---------------------------------------------------------

def _opportunistic_ctx() -> ssl.SSLContext:
    # Port-25 MX delivery uses opportunistic TLS — encrypt if offered, but do
    # not hard-fail on cert chain (hosts often lack a CA bundle). This mirrors
    # how real MTAs deliver between mail servers.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _send_to_domain(domain: str, rcpts: list[str], msg_bytes: bytes,
                    from_addr: str) -> tuple[bool, str | None, str | None]:
    ctx = _opportunistic_ctx()
    last_err = None
    for host in _resolve_mx(domain):
        try:
            s = smtplib.SMTP(host, 25, timeout=_timeout())
            try:
                s.ehlo(socket.getfqdn())
                if s.has_extn("starttls"):
                    s.starttls(context=ctx)
                    s.ehlo(socket.getfqdn())
                s.sendmail(from_addr, rcpts, msg_bytes)
            finally:
                try:
                    s.quit()
                except Exception:
                    pass
            return True, host, None
        except Exception as e:  # try next MX
            last_err = repr(e)
    return False, None, last_err


def _deliver(subject: str, body: str) -> dict:
    rcpts = _recipients()
    if not rcpts:
        return {"sent": False, "reason": "no recipients (WM_ALERT_TO unset)"}
    from_addr = _sender()

    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = ", ".join(rcpts)
    msg["Subject"] = subject
    msg["Date"] = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    msg.set_content(body)
    raw = msg.as_bytes()

    # group recipients by domain (each domain has its own MX)
    by_domain: dict[str, list[str]] = {}
    for r in rcpts:
        by_domain.setdefault(r.rsplit("@", 1)[-1].lower(), []).append(r)

    results = []
    ok_any = False
    for domain, drcpts in by_domain.items():
        ok, host, err = _send_to_domain(domain, drcpts, raw, from_addr)
        ok_any = ok_any or ok
        results.append({"domain": domain, "recipients": drcpts,
                        "sent": ok, "via": host, "error": err})
    return {"sent": ok_any, "subject": subject, "results": results}


# ---- public API -------------------------------------------------------------

def send_down_alert(down: list[dict], total: int) -> dict:
    """Email a consolidated alert listing every currently-down site.
    Called once per sweep; no-op if alerts are disabled or nothing is down."""
    if not down:
        return {"sent": False, "reason": "nothing down"}
    if not alerts_enabled():
        return {"sent": False, "reason": "alerts disabled (WM_ALERT_TO unset)"}

    names = ", ".join(d.get("name", d["url"]) for d in down)
    subject = f"[website-monitor] {len(down)} site(s) DOWN: {names}"

    lines = [
        f"{len(down)} of {total} monitored site(s) are DOWN as of "
        f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}.",
        "",
    ]
    for d in down:
        code = d.get("status_code") if d.get("status_code") is not None else "-"
        err = f" — {d['error']}" if d.get("error") else ""
        cat = d.get("category", "?")
        lines.append(f"  ✗ {d.get('name', d['url'])}  [{cat}]")
        lines.append(f"      {d['url']}")
        lines.append(f"      HTTP {code}{err}")
        lines.append(f"      checked: {d.get('checked_at', '?')}")
        lines.append("")
    lines.append("-- website-monitor (10.x host) · hourly check")
    return _deliver(subject, "\n".join(lines))


def send_test() -> dict:
    return _deliver(
        "[website-monitor] test alert",
        "This is a test email from the website monitor's alert system.\n"
        "If you received this, down-alerts are configured correctly.\n",
    )


if __name__ == "__main__":
    if "--test" in sys.argv:
        if not alerts_enabled():
            print("alerts disabled: set WM_ALERT_TO (env or alert.env)")
            sys.exit(1)
        import json
        print(json.dumps(send_test(), indent=2))
    else:
        print("usage: python alerts.py --test")
