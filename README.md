# s33-modem-mon

Tools for the S33 modem.

1. A simple command-line tool to interact with the modem using its HNAP API.

   - Get modem stats:

     ```bash
     $ s33 stats --password mypassword
     Logging in to s33 modem admin:*************** @ 192.168.0.1...✅
     Fetching stats for actions: GetCustomerStatusStartupSequence, GetCustomerStatusConnectionInfo, GetCustomerStatusDownstreamChannelInfo, GetCustomerStatusUpstreamChannelInfo, GetCustomerStatusLog, GetCustomerStatusLogXXX, GetCustomerStatusSoftware, GetCustomerStatusXXX, GetArrisDeviceStatus, GetArrisRegisterInfo, GetArrisRegisterStatus, GetArrisConfigurationInfo, GetArrisXXX...✅
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

    - Reboot the modem:

      ```bash
      $ s33 reboot --password mypassword
      Logging in to s33 modem admin:*************** @ 192.168.0.1...✅
      Sending reboot command...✅
      ```

2. A background service to continuously read stats from the modem and insert
   them into a TimescaleDB database for long-term monitoring and analysis.

   ```bash
   $ s33mon --modem-password mypassword --db-host localhost --db-user myuser --db-password mypassword
   2026-02-19 05:54:53,110 INFO s33mon.mon: Connecting to myuser@localhost:5432/s33mon ...
   2026-02-19 05:54:53,157 INFO s33mon.mon: Schema is ready.
   2026-02-19 05:54:53,157 INFO s33mon.mon: Starting poll loop — modem: 192.168.0.1, interval: 900s
   2026-02-19 05:54:53,305 INFO httpx: HTTP Request: POST https://192.168.0.1/HNAP1/ "HTTP/1.1 200 OK"
   2026-02-19 05:54:53,361 INFO httpx: HTTP Request: POST https://192.168.0.1/HNAP1/ "HTTP/1.1 200 OK"
   2026-02-19 05:55:00,120 INFO httpx: HTTP Request: POST https://192.168.0.1/HNAP1/ "HTTP/1.1 200 OK"
   2026-02-19 05:55:00,163 INFO s33mon.mon: Pull #3 stored successfully.
   2026-02-19 05:55:00,163 INFO s33mon.mon: Sleeping 900s ...
   ```
