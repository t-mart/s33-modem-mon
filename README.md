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
  interval: 300
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
| `monitor.interval` | `--interval`      | `S33_INTERVAL`        | 300           |

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
2026-02-19 05:54:53,157 INFO s33mon.monitor: Starting poll loop, modem: 192.168.0.1, interval: 300s
2026-02-19 05:54:53,305 INFO httpx: HTTP Request: POST https://192.168.0.1/HNAP1/ "HTTP/1.1 200 OK"
2026-02-19 05:54:53,361 INFO httpx: HTTP Request: POST https://192.168.0.1/HNAP1/ "HTTP/1.1 200 OK"
2026-02-19 05:55:00,120 INFO httpx: HTTP Request: POST https://192.168.0.1/HNAP1/ "HTTP/1.1 200 OK"
2026-02-19 05:55:00,163 INFO s33mon.monitor: Pull #3 stored successfully.
2026-02-19 05:55:00,163 INFO s33mon.monitor: Sleeping 300s ...
```

## Modem events

The default pull interval is five minutes. Explicit configuration values override this default.
Previously, the default interval was fifteen minutes.

A failed public IP lookup no longer prevents storage of modem data.
The collector logs a warning and omits the IP for that pull.

The `modem_event` view classifies stored log messages without changes to their timestamps or original text.
The monitor creates or updates this view at startup.

Import `dashboards/s33-modem.json` after the monitor updates the schema.
The dashboard separates modem events, public IP history, clock drift, and channel data into sections.
The header uses two rows and includes the current public IP and clock drift.
The reboot table estimates restart times from collection time and modem uptime.
It lists reboots first detected within the selected range, without markers.

Event counts use the first observation time, not the modem event time.
Each display bucket counts records first observed within that bucket, by event type.
Buckets without pulls remain gaps. Historical records without observation times do not contribute to these counts.
Log limits and identical records can hide events, so counts do not establish outage duration.

The event table retains both timestamps and includes unrecognized messages as `other`.
Clock drift compares collection time with modem time after time-zone conversion.
Values use minutes with two decimals, instead of whole hours.
Positive values mean that the modem clock trails collection time.
The collector interprets modem timestamps as fixed UTC-5, without daylight saving.
Previously, `America/New_York` added a false hour of drift during daylight saving.
For existing databases, follow the [timestamp repair procedure](scripts/README.md) before the updated collector starts.

Codeword rates use stacked bars with a duration-weighted average for each channel, instead of lines with separate totals.
Counter decreases do not contribute to these rates.
Channel bonding history shows missing and unlocked channel counts on separate timeline rows, instead of overlapping expected, present, and locked lines.
Zero means complete. Negative missing counts indicate extra channels. Empty display buckets remain gaps.

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

Set `S33_TEST_DSN` to a TimescaleDB connection string to include the dashboard SQL tests.
These tests use read-only transactions and synthetic rows. Without this variable, pytest skips the dashboard SQL tests.

## Docker

The image runs the monitor. Configure it with `S33_*` environment variables, or
mount a config file and set `S33_CONFIG`.

```bash
make build
make push
```

Pushes to `master` now publish the Docker image with the `latest` tag.
Previously, publication required `make push`.

Configure these repository settings under **Settings > Secrets and variables > Actions**:

| Type | Name | Value |
| ---- | ---- | ----- |
| Secret | `DOCKERHUB_TOKEN` | Docker Hub personal access token with Read & Write permission |
| Variable | `DOCKERHUB_USERNAME` | `tmmrtn` |
| Variable | `DOCKERHUB_REPOSITORY` | `tmmrtn/s33-modem-mon` |

Use a [Docker Hub personal access token](https://docs.docker.com/security/access-tokens/), not the account password.
