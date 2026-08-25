# Changelog

## Unreleased

### Added

- Log rows now store `first_observed_at` from the pull timestamp. Existing rows
  keep null because their observation time is unknown.
- A YAML config file at `$XDG_CONFIG_HOME/s33.yml` (override with `--config` or
  `S33_CONFIG`) holding modem, database, and monitor settings. Command-line
  flags win over environment variables, which win over the file. Every key is
  optional. The file is warned about if it is readable by other users, since it
  holds passwords.
- `s33 monitor`, replacing the separate `s33mon` program. `s33mon` remains as an
  alias for it.
- A test suite covering the response parsers, config precedence, and the
  agreement between `schema.sql` and the stat dataclasses.

### Changed

- New databases now use a regular PostgreSQL `log_entry` table. Previously,
  setup converted this table to a TimescaleDB hypertable.
- Both commands now share one option set, so `--host`, `--username`, and
  `--password` work everywhere. The monitor's old `--modem-host`,
  `--modem-username`, and `--modem-password` spellings still work.
- psycopg2 moved to an optional `monitor` extra. `pip install s33-modem-mon`
  no longer needs libpq; use `s33-modem-mon[monitor]` to run the poll loop.
- The package is built with hatchling instead of legacy setuptools, so builds
  no longer leave `build/` and `src/*.egg-info/` behind.
- Success messages print `OK` rather than an emoji.

### Fixed

- Database inserts are generated from the stat dataclasses instead of being
  transcribed by hand, so a field can no longer silently disagree with its
  column.
