#!/usr/bin/env python3
"""
Website Monitor — shared core
-----------------------------
Check logic + JSON-backed store, shared by:
  - monitor.py  (hourly cron checker, writes status/history)
  - server.py   (MCP server, reads status/history, manages the site list)

Health rule (chosen at install time): a site is HEALTHY when it returns an HTTP
status < 400 within the timeout. Timeouts, connection errors, and 4xx/5xx are
UNHEALTHY. Latency (ms) and the observed status code are recorded either way.

All state lives next to this file so the project is fully self-contained:
  sites.json     - configured sites           [{"name", "url", "added"}]
  status.json    - latest result per url       {url: <result>}
  history.jsonl  - append-only check history   one <result> JSON per line
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
import urllib3

BASE_DIR = Path(__file__).resolve().parent
SITES_FILE = Path(os.environ.get("WM_SITES", BASE_DIR / "sites.json"))
STATUS_FILE = Path(os.environ.get("WM_STATUS", BASE_DIR / "status.json"))
HISTORY_FILE = Path(os.environ.get("WM_HISTORY", BASE_DIR / "history.jsonl"))

DEFAULT_TIMEOUT = float(os.environ.get("WM_TIMEOUT", "10"))
USER_AGENT = "website-monitor-mcp/1.0"

# Keep history from growing without bound (entries, not bytes).
HISTORY_MAX = int(os.environ.get("WM_HISTORY_MAX", "5000"))


# ---- time helpers -----------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---- atomic JSON store ------------------------------------------------------

def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open() as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp.replace(path)  # atomic on POSIX


# ---- site list --------------------------------------------------------------

def _normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise ValueError("url is empty")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def load_sites() -> list[dict]:
    return _read_json(SITES_FILE, [])


def save_sites(sites: list[dict]) -> None:
    _write_json(SITES_FILE, sites)


VALID_CATEGORIES = ("internal", "external")


def _normalize_category(category: str | None) -> str:
    cat = (category or "external").strip().lower()
    if cat not in VALID_CATEGORIES:
        raise ValueError(
            f"category must be one of {VALID_CATEGORIES}, got {category!r}"
        )
    return cat


def add_site(url: str, name: str | None = None, category: str | None = None,
             verify_tls: bool = True) -> dict:
    url = _normalize_url(url)
    cat = _normalize_category(category)
    sites = load_sites()
    for s in sites:
        if s["url"] == url:
            # idempotent on url; allow updating name/category/verify in place
            if name:
                s["name"] = name
            s["category"] = cat
            s["verify_tls"] = verify_tls
            save_sites(sites)
            return s
    entry = {"name": name or url, "url": url, "category": cat,
             "verify_tls": verify_tls, "added": now_iso()}
    sites.append(entry)
    save_sites(sites)
    return entry


def remove_site(url_or_name: str) -> bool:
    sites = load_sites()
    key = url_or_name.strip()
    try:
        nkey = _normalize_url(key)
    except ValueError:
        nkey = key
    kept = [s for s in sites if s["url"] not in (key, nkey) and s["name"] != key]
    if len(kept) == len(sites):
        return False
    save_sites(kept)
    return True


# ---- the check --------------------------------------------------------------

def check_url(url: str, timeout: float = DEFAULT_TIMEOUT,
              verify_tls: bool = True) -> dict:
    """Perform one HTTP GET and return a structured result dict.

    Set verify_tls=False for internal hosts with self-signed/private-CA certs
    (e.g. vCenter, appliances on .local) so a cert-trust failure does not show
    as a false DOWN. The HTTP status check is unchanged."""
    started = now_iso()
    headers = {"User-Agent": USER_AGENT}
    if not verify_tls:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    try:
        resp = requests.get(
            url, timeout=timeout, headers=headers, allow_redirects=True,
            verify=verify_tls,
        )
        latency_ms = round(resp.elapsed.total_seconds() * 1000, 1)
        healthy = resp.status_code < 400
        return {
            "url": url,
            "checked_at": started,
            "healthy": healthy,
            "status_code": resp.status_code,
            "latency_ms": latency_ms,
            "error": None,
        }
    except requests.exceptions.Timeout:
        return _fail(url, started, f"timeout after {timeout}s")
    except requests.exceptions.ConnectionError as e:
        return _fail(url, started, f"connection error: {_short(e)}")
    except requests.exceptions.RequestException as e:
        return _fail(url, started, f"request error: {_short(e)}")


def _fail(url: str, started: str, msg: str) -> dict:
    return {
        "url": url,
        "checked_at": started,
        "healthy": False,
        "status_code": None,
        "latency_ms": None,
        "error": msg,
    }


def _short(e: Exception) -> str:
    s = str(e)
    return s[:200] + ("…" if len(s) > 200 else "")


# ---- status + history persistence ------------------------------------------

def record_result(result: dict) -> None:
    """Update latest-status map and append to history (with trimming)."""
    status = _read_json(STATUS_FILE, {})
    status[result["url"]] = result
    _write_json(STATUS_FILE, status)

    with HISTORY_FILE.open("a") as f:
        f.write(json.dumps(result) + "\n")
    _trim_history()


def _trim_history() -> None:
    if not HISTORY_FILE.exists():
        return
    with HISTORY_FILE.open() as f:
        lines = f.readlines()
    if len(lines) > HISTORY_MAX:
        with HISTORY_FILE.open("w") as f:
            f.writelines(lines[-HISTORY_MAX:])


def load_status() -> dict:
    return _read_json(STATUS_FILE, {})


def load_history(url: str | None = None, limit: int = 50) -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    out: list[dict] = []
    with HISTORY_FILE.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if url is None or rec.get("url") == url:
                out.append(rec)
    return out[-limit:]


def uptime_summary(url: str | None = None) -> dict:
    """Compute uptime % over recorded history, per url or overall."""
    history = load_history(url=url, limit=HISTORY_MAX)
    by_url: dict[str, list[dict]] = {}
    for rec in history:
        by_url.setdefault(rec["url"], []).append(rec)

    summary = {}
    for u, recs in by_url.items():
        total = len(recs)
        up = sum(1 for r in recs if r.get("healthy"))
        summary[u] = {
            "checks": total,
            "healthy": up,
            "uptime_pct": round(100.0 * up / total, 2) if total else None,
            "last_status": recs[-1] if recs else None,
        }
    return summary


# ---- one full sweep (used by the cron checker) ------------------------------

def run_all(timeout: float = DEFAULT_TIMEOUT) -> list[dict]:
    results = []
    for site in load_sites():
        res = check_url(site["url"], timeout=timeout,
                        verify_tls=site.get("verify_tls", True))
        res["name"] = site.get("name", site["url"])
        res["category"] = site.get("category", "external")
        record_result(res)
        results.append(res)
    return results
