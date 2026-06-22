# Installation & Deployment

This guide covers a production install on a Linux host: a Python virtualenv, an
hourly cron check, and the web dashboard kept alive across reboots and crashes.

> Tested on Ubuntu 24.04 with Python 3.12. Any Linux with Python ≥ 3.10 and
> `cron` should work.

---

## 1. Prerequisites

- Python 3.10+ (`python3 --version`)
- `pip` + `venv` (`python3 -m venv --help`)
- `cron` (`systemctl is-active cron`)
- Outbound HTTP/HTTPS access to the sites you want to monitor

---

## 2. Install

```bash
# pick an install location
cd ~
git clone https://github.com/siranjeevi93/website_checker.git
cd website_checker

# isolated virtualenv
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# sanity check (uses a throwaway temp store — touches no live data)
./venv/bin/python smoke_test.py
```

---

## 3. Configure sites

Sites are stored in `sites.json` (git-ignored, created on first add). Add them
with the helper or via the MCP `add_site` tool:

```bash
./venv/bin/python -c "import monitor_core as m; \
  m.add_site('https://www.example.com', 'Marketing', 'external'); \
  m.add_site('https://wiki.internal.example.com', 'Wiki', 'internal')"
```

Or copy the sample and edit:

```bash
cp sites.example.json sites.json
```

Run a sweep to populate status:

```bash
./venv/bin/python monitor.py
```

---

## 4. Schedule the hourly check (cron)

Add **one** line to your user crontab — `crontab -e`:

```cron
0 * * * * cd /home/youruser/website_checker && ./venv/bin/python monitor.py >> monitor.log 2>&1   # website-checker: hourly check
```

> Adjust the path to your install. The trailing comment is a marker so the
> entry is easy to find/remove later.

Verify:

```bash
crontab -l | grep website-checker
```

---

## 5. Run the dashboard with boot persistence

`start-dashboard.sh` starts the Flask UI only if it isn't already running, so it
doubles as a boot launcher and a crash-recovery watchdog.

```bash
chmod +x start-dashboard.sh
```

Add **two** lines to `crontab -e`:

```cron
@reboot /home/youruser/website_checker/start-dashboard.sh                 # website-checker: start dashboard on boot
*/2 * * * * /home/youruser/website_checker/start-dashboard.sh             # website-checker: dashboard watchdog
```

Start it now without waiting for a reboot:

```bash
/home/youruser/website_checker/start-dashboard.sh
```

Open **http://<host>:8090/** in a browser.

> **systemd alternative.** If you prefer systemd over cron for the dashboard,
> create a user service running `venv/bin/python dashboard.py` with
> `Restart=always`, and a `*.timer` (`OnCalendar=hourly`) for the check instead
> of the cron line. The cron approach is shown here because it needs no root.

---

## 6. Coexisting with other services on the same host

If the host already runs other apps (including other Flask "webapp" processes):

- The dashboard listens on **`WM_WEB_PORT`** (default `8090`). Change it if 8090
  is taken: `WM_WEB_PORT=8095 ./venv/bin/python dashboard.py`.
- `start-dashboard.sh` matches its own process by the **`dashboard.py`** name
  (via `pgrep`). Keep that filename distinct from other services' process names
  so watchdogs never cross-match. Avoid typing the literal string `dashboard.py`
  in unrelated shell commands while the watchdog runs, or its `pgrep` may match
  your shell.
- Cron edits should be **additive** — append your lines, don't overwrite
  existing ones.

---

## 7. Updating

```bash
cd website_checker
git pull
./venv/bin/pip install -r requirements.txt        # in case deps changed
pkill -f "venv/bin/python dashboard.py"            # stop the old dashboard
./start-dashboard.sh                                # relaunch
```

Runtime state (`sites.json`, `status.json`, `history.jsonl`) is preserved across
updates because it is git-ignored and never overwritten by `git pull`.

---

## 8. Uninstall

```bash
# remove cron lines
crontab -l | grep -v "website-checker:" | crontab -

# stop the dashboard
pkill -f "venv/bin/python dashboard.py"

# remove files
rm -rf ~/website_checker
```

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Dashboard not reachable | `ss -ltn \| grep 8090`; read `dashboard.log` for a startup traceback. |
| Cron check not running | `grep website-checker <(crontab -l)`; confirm `systemctl is-active cron`; check `monitor.log`. |
| All sites show "NO DATA" | No sweep has run yet — run `./venv/bin/python monitor.py` once. |
| A site is wrongly "DOWN" | Confirm outbound network + that the host returns `< 400` within `WM_TIMEOUT`. Raise `WM_TIMEOUT` for slow sites. |
