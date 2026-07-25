# s33-modem-mon

Tools for the Arris S33 cable modem, built on its HNAP API.

- `s33 stats` and `s33 reboot` talk to the modem directly.
- `s33 monitor` polls the modem on an interval and stores the results in a
  TimescaleDB database for long-term analysis. This one needs the `monitor`
  extra, which pulls in psycopg2. `s33mon` is an alias for it.

## Install

```bash
uv tool install s33-modem-mon              # CLI only
uv tool install 's33-modem-mon[monitor]'   # CLI plus the polling service
```

## Configuration

Every option can come from a command-line flag, an environment variable, or a
YAML config file, in that order of precedence. The config file lives at
`$XDG_CONFIG_HOME/s33.yml` (usually `~/.config/s33.yml`); point `S33_CONFIG` or
`--config` somewhere else to override that.

```yaml
modem:
  host: 192.168.0.1
  username: admin
  password: mypassword
db:
  host: localhost
  port: 5432
  user: myuser
  password: mypassword
  name: s33mon
monitor:
  interval: 900
```

Every key is optional; anything you leave out falls back to the default shown
above. Because the file holds passwords, keep it to yourself:

```bash
chmod 600 ~/.config/s33.yml
```

| Setting            | Flag              | Environment variable  | Default       |
| ------------------ | ----------------- | --------------------- | ------------- |
| `modem.host`       | `--host`          | `S33_MODEM_HOST`      | 192.168.0.1   |
| `modem.username`   | `-u`, `--username`| `S33_MODEM_USERNAME`  | admin         |
| `modem.password`   | `-p`, `--password`| `S33_MODEM_PASSWORD`  | (required)    |
| `db.host`          | `--db-host`       | `S33_DB_HOST`         | localhost     |
| `db.port`          | `--db-port`       | `S33_DB_PORT`         | 5432          |
| `db.user`          | `--db-user`       | `S33_DB_USER`         | postgres      |
| `db.password`      | `--db-password`   | `S33_DB_PASSWORD`     | (required)    |
| `db.name`          | `--db-name`       | `S33_DB_NAME`         | s33mon        |
| `monitor.interval` | `--interval`      | `S33_INTERVAL`        | 900           |

Passing a password on the command line makes it visible to anyone who can run
`ps`. Prefer the config file or the environment.

## Usage

Get modem stats. Pass one or more HNAP action names to narrow it down; the
default is all of them.

```bash
$ s33 stats
Logging in to s33 modem admin:*************** @ 192.168.0.1...OK
Fetching stats for actions: GetCustomerStatusStartupSequence, GetCustomerStatusConnectionInfo, ...OK
{
  "customer_status_startup_sequence": {
    "downstream_frequency_hertz": 741000000,
    "downstream_comment": "Locked",
    "connectivity_status": "OK",
    "connectivity_comment": "Operational",
    "boot_status": "OK",
    "boot_comment": "Operational",
    "config_file_status": "OK",
    "config_file_comment": "",
    "security_status": "Enabled",
    "security_comment": "BPI+"
  },
  "customer_status_connection_info": {
    "system_uptime_seconds": 602183,
    "system_time_unix": 1771475283,
    "network_access": "Allowed"
  },
  "customer_status_downstream_channel_info": {
    "channels": [
      {
        "channel_id": 31,
        "lock_status": "Locked",
        "modulation": "QAM256",
        "frequency_hz": 741000000,
        "power_dbmv": 11.0,
        "snr_db": 37.0,
        "corrected_count": 0,
        "uncorrectable_count": 0
        ...
```

Reboot the modem:

```bash
$ s33 reboot
Logging in to s33 modem admin:*************** @ 192.168.0.1...OK
Sending reboot command...OK
```

Run the monitor. It creates the database and schema if they don't exist yet.

```bash
$ s33 monitor
2026-02-19 05:54:53,110 INFO s33mon.monitor: Connecting to myuser@localhost:5432/s33mon ...
2026-02-19 05:54:53,157 INFO s33mon.store.timescale: Schema is ready.
2026-02-19 05:54:53,157 INFO s33mon.monitor: Starting poll loop, modem: 192.168.0.1, interval: 900s
2026-02-19 05:54:53,305 INFO httpx: HTTP Request: POST https://192.168.0.1/HNAP1/ "HTTP/1.1 200 OK"
2026-02-19 05:54:53,361 INFO httpx: HTTP Request: POST https://192.168.0.1/HNAP1/ "HTTP/1.1 200 OK"
2026-02-19 05:55:00,120 INFO httpx: HTTP Request: POST https://192.168.0.1/HNAP1/ "HTTP/1.1 200 OK"
2026-02-19 05:55:00,163 INFO s33mon.monitor: Pull #3 stored successfully.
2026-02-19 05:55:00,163 INFO s33mon.monitor: Sleeping 900s ...
```

## Layout

```
src/s33mon/
  models.py    dataclasses for each HNAP action, and their parsers
  hnap.py      HNAP transport: login, authenticated requests, reboot
  config.py    flag/environment/file layering
  ip.py        our public IP, recorded alongside each pull
  monitor.py   the poll loop
  cli.py       the s33 command group
  store/
    schema.sql  table definitions
    timescale.py  connection, schema setup, inserts
```

Adding a new HNAP action means writing one dataclass in `models.py` and listing
it in `ModemStats`. The CLI picks it up automatically. To store it as well, add
a table to `schema.sql` whose columns match the dataclass field names and list
the key in `SINGLE_ROW_STATS`; the insert statements are generated from the
dataclass, and `tests/test_store.py` checks that the two stay in sync.

## Development

```bash
uv sync --all-extras
uv run ruff format . ; uv run ruff check . ; uv run ty check ; uv run pytest
```

## Docker

The image runs the monitor. Configure it with `S33_*` environment variables, or
mount a config file and set `S33_CONFIG`.

```bash
make build
make push
```
