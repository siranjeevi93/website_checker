# Architecture

Website Checker is deliberately small: one shared core module, three entry
points (MCP server, cron checker, web dashboard), and a JSON-file store. There
is no database, queue, or background daemon beyond the optional dashboard
process.

## Component overview

```mermaid
flowchart TB
    subgraph clients["Clients"]
        MC["MCP client<br/>Claude Code / Desktop"]
        BR["Web browser"]
    end

    subgraph app["Website Checker (one host, one venv)"]
        direction TB
        SRV["<b>server.py</b><br/>MCP server · stdio"]
        DASH["<b>dashboard.py</b><br/>Flask UI · :8090"]
        MON["<b>monitor.py</b><br/>hourly sweep"]
        CORE["<b>monitor_core.py</b><br/>check logic + store API"]
        ALERT["<b>alerts.py</b><br/>direct-to-MX email"]

        subgraph store["JSON store"]
            direction LR
            S1[("sites.json")]
            S2[("status.json")]
            S3[("history.jsonl")]
        end
    end

    CRON{{"cron<br/>0 * * * *"}}
    TARGETS["Monitored sites<br/>internal + external"]

    MC -->|JSON-RPC over stdio| SRV
    BR -->|HTTP| DASH
    CRON -->|triggers| MON

    SRV --> CORE
    DASH --> CORE
    MON --> CORE

    CORE -->|HTTP GET| TARGETS
    CORE -->|read/write| S1
    CORE -->|write| S2
    CORE -->|append| S3
    DASH -.read.-> S2

    MON -->|down sites| ALERT
    ALERT -->|SMTP :25 + STARTTLS| MAIL["Recipient MX<br/>(direct, no relay)"]
```

| Component | Role | Reads | Writes |
|-----------|------|-------|--------|
| `monitor_core.py` | The engine: HTTP check, health rule, JSON store API, uptime math. | all | all |
| `server.py` | MCP server (stdio). Manage sites + expose results as tools. | store | `sites.json` |
| `monitor.py` | Run by cron every hour. Performs a full sweep. | `sites.json` | `status.json`, `history.jsonl` |
| `dashboard.py` | Flask web UI on `:8090`. Two panes (Internal / External). | store | (only via "Check now") |
| `alerts.py` | Emails down-alerts directly to the recipient's MX (SMTP :25 + STARTTLS). Opt-in via `WM_ALERT_TO`. | `alert.env` | — |
| `start-dashboard.sh` | Idempotent launcher used by `@reboot` + watchdog cron. | — | — |

## The health check

```mermaid
flowchart TD
    A["check_url(url)"] --> B["HTTP GET<br/>timeout=WM_TIMEOUT, follow redirects"]
    B -->|response| C{"status < 400?"}
    C -->|yes| D["healthy = true"]
    C -->|no| E["healthy = false"]
    B -->|timeout| F["healthy = false<br/>error = 'timeout'"]
    B -->|conn error| G["healthy = false<br/>error = 'connection error'"]
    D --> H["record: status_code, latency_ms, checked_at"]
    E --> H
    F --> H
    G --> H
    H --> I["status.json (latest)<br/>+ append history.jsonl"]
```

A result is a flat dict:

```json
{
  "url": "https://www.example.com",
  "checked_at": "2026-01-01T12:00:00+00:00",
  "healthy": true,
  "status_code": 200,
  "latency_ms": 87.7,
  "error": null,
  "name": "Marketing",
  "category": "external"
}
```

## Hourly sweep sequence

```mermaid
sequenceDiagram
    participant Cron as cron (hourly)
    participant Mon as monitor.py
    participant Core as monitor_core
    participant Net as Target sites
    participant Disk as JSON store

    Cron->>Mon: run monitor.py
    Mon->>Core: run_all()
    loop each configured site
        Core->>Net: HTTP GET (timeout 10s)
        Net-->>Core: status / latency / error
        Core->>Disk: update status.json + append history.jsonl
    end
    Core-->>Mon: results[]
    Mon->>Mon: print one-line summary → monitor.log
```

The dashboard never needs to talk to the checker: it simply reads the same
`status.json` / `history.jsonl` that the sweep just wrote. This shared-file
design is why there are no sockets, locks, or IPC between the three entry
points — the filesystem is the integration point. Writes are atomic
(write-to-temp + `rename`), so a reader never sees a half-written file.

## Why JSON files instead of a database

- **Zero ops** — nothing to install, secure, or back up beyond copying a folder.
- **Transparent** — `cat status.json` tells you everything.
- **Right-sized** — a single host monitoring tens of sites hourly produces
  trivial data volumes; history is capped at `WM_HISTORY_MAX` entries.

If you outgrow this (hundreds of sites, sub-minute intervals, multi-host), the
clean seam is `monitor_core.py`'s store functions — swap them for a real
datastore without touching the three entry points.
