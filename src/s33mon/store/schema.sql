-- Tables are created in dependency order.
-- No FK constraints to pull because TimescaleDB hypertables can't be FK targets.
--
-- Column names must match the field names of the corresponding dataclass in
-- s33mon.models: the insert layer builds its statements from those fields.
-- tests/test_store.py enforces this.

CREATE TABLE IF NOT EXISTS pull (
    id      BIGSERIAL,
    pulled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, pulled_at)
);

CREATE TABLE IF NOT EXISTS customer_status_startup_sequence (
    pull_id                     BIGINT NOT NULL PRIMARY KEY,
    downstream_frequency_hertz  BIGINT NOT NULL,
    downstream_comment          TEXT   NOT NULL,
    connectivity_status         TEXT   NOT NULL,
    connectivity_comment        TEXT   NOT NULL,
    boot_status                 TEXT   NOT NULL,
    boot_comment                TEXT   NOT NULL,
    config_file_status          TEXT   NOT NULL,
    config_file_comment         TEXT   NOT NULL,
    security_status             TEXT   NOT NULL,
    security_comment            TEXT   NOT NULL
);

CREATE TABLE IF NOT EXISTS customer_status_connection_info (
    pull_id               BIGINT NOT NULL PRIMARY KEY,
    system_uptime_seconds BIGINT NOT NULL,
    system_time_unix      BIGINT NOT NULL,
    network_access        TEXT   NOT NULL
);

CREATE TABLE IF NOT EXISTS downstream_channel (
    pulled_at          TIMESTAMPTZ      NOT NULL,
    pull_id            BIGINT           NOT NULL,
    channel_id         INT              NOT NULL,
    lock_status        TEXT             NOT NULL,
    modulation         TEXT             NOT NULL,
    frequency_hz       BIGINT           NOT NULL,
    power_dbmv         DOUBLE PRECISION NOT NULL,
    snr_db             DOUBLE PRECISION NOT NULL,
    corrected_count    BIGINT           NOT NULL,
    uncorrectable_count BIGINT          NOT NULL,
    PRIMARY KEY (pulled_at, pull_id, channel_id)
);

CREATE TABLE IF NOT EXISTS upstream_channel (
    pulled_at    TIMESTAMPTZ      NOT NULL,
    pull_id      BIGINT           NOT NULL,
    channel_id   INT              NOT NULL,
    lock_status  TEXT             NOT NULL,
    channel_type TEXT             NOT NULL,
    width_hz     BIGINT           NOT NULL,
    frequency_hz BIGINT           NOT NULL,
    power_dbmv   DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (pulled_at, pull_id, channel_id)
);

-- No pull_id. Deduplicated by the modem event time, level, and message.
CREATE TABLE IF NOT EXISTS log_entry (
    first_observed_at TIMESTAMPTZ NOT NULL,
    timestamp_at      TIMESTAMPTZ NOT NULL,
    level             INT         NOT NULL,
    message           TEXT        NOT NULL,
    PRIMARY KEY (timestamp_at, level, message)
);

CREATE INDEX IF NOT EXISTS log_entry_first_observed_at_idx
    ON log_entry (first_observed_at DESC);

CREATE OR REPLACE VIEW modem_event AS
SELECT
    first_observed_at,
    timestamp_at,
    level,
    message,
    CASE
        WHEN lower(message) LIKE 'successful lan webgui login from %' THEN 'collector_login'
        WHEN lower(message) LIKE 'sync timing synchronization failure%' THEN 'sync_failure'
        WHEN lower(message) LIKE '%t3 time%' THEN 't3_timeout'
        WHEN lower(message) LIKE '%t4 time%' THEN 't4_timeout'
        WHEN lower(message) LIKE 'mdd message timeout%' THEN 'mdd_timeout'
        WHEN lower(message) LIKE 'dhcp failed%' THEN 'dhcp_failure'
        WHEN lower(message) LIKE 'dhcp warning%' THEN 'dhcp_warning'
        WHEN lower(message) LIKE 'ds profile assignment change%'
          OR lower(message) LIKE 'us profile assignment change%' THEN 'profile_change'
        ELSE 'other'
    END AS event_type
FROM log_entry;

CREATE TABLE IF NOT EXISTS customer_status_software (
    pull_id             BIGINT NOT NULL PRIMARY KEY,
    spec_version        TEXT   NOT NULL,
    hard_version        TEXT   NOT NULL,
    soft_version        TEXT   NOT NULL,
    mac_address         TEXT   NOT NULL,
    serial_number       TEXT   NOT NULL,
    certificate_status  TEXT   NOT NULL,
    customer_version    TEXT   NOT NULL
);

CREATE TABLE IF NOT EXISTS arris_device_status (
    pull_id                       BIGINT           NOT NULL PRIMARY KEY,
    firmware_version              TEXT             NOT NULL,
    internet_connection_status    TEXT             NOT NULL,
    downstream_frequency_hz       BIGINT           NOT NULL,
    downstream_signal_power_dbmv  DOUBLE PRECISION NOT NULL,
    downstream_signal_snr_db      DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS arris_register_info (
    pull_id       BIGINT NOT NULL PRIMARY KEY,
    mac_address   TEXT   NOT NULL,
    serial_number TEXT   NOT NULL,
    model_name    TEXT   NOT NULL
);

CREATE TABLE IF NOT EXISTS arris_register_status (
    pull_id       BIGINT  NOT NULL PRIMARY KEY,
    ask_me_later  BOOLEAN NOT NULL,
    never_ask     BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS arris_configuration_info (
    pull_id                   BIGINT  NOT NULL PRIMARY KEY,
    downstream_frequency_hz   BIGINT  NOT NULL,
    upstream_channel_id       INT     NOT NULL,
    downstream_plan           TEXT    NOT NULL,
    energy_efficient_ethernet BOOLEAN NOT NULL,
    led_status                BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS own_ip (
    pull_id BIGINT NOT NULL PRIMARY KEY,
    ip      TEXT   NOT NULL
);
