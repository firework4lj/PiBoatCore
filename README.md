# PiBoatCore

PiBoatCore is a Raspberry Pi telemetry service for a boat. It polls onboard
sensors, builds a heartbeat payload, and sends it to a central Node.js service.
If Wi-Fi or cellular is down, payloads are stored locally in SQLite and retried
on the next heartbeat.

## Current Sensors

- System health: uptime, CPU load, memory, disk
- SIM7600 modem/GNSS health: optional adapter for signal, registration, operator, and GPS
- GPS: mock adapter for development
- Bilge: mock adapter for development
- Battery state of charge: mock adapter for development

The sensor layer is intentionally small: add a new adapter under
`pi_boat_core/sensors/`, return a JSON-serializable dictionary, then register it
in `pi_boat_core/service.py`.

## Pi Service

```bash
cd PiBoatCore
python3 -m venv .venv
source .venv/bin/activate
python -m pi_boat_core
```

Configuration is read from `config.toml`. Start from the example:

```bash
cp config.example.toml config.toml
python -m pi_boat_core --config config.toml
```

## Central Server

The Node.js receiver lives in the sibling `PiBoatServer` repo. Once that server
is running, set the URL in `config.toml`:

```toml
[server]
url = "http://localhost:3000/api/heartbeat"
payload_format = "compact"
```

`payload_format = "compact"` sends a small versioned CSV payload in `{"t":"..."}`
and lets PiBoatServer expand it back into normal stored telemetry. Use
`payload_format = "full"` while debugging if you want to send the verbose JSON
object.

## Waveshare SIM7600X

Let Linux own the cellular data connection. PiBoatCore only reads modem health
and GNSS position over the modem's AT command serial port.

Install the optional serial dependency:

```bash
python -m pip install ".[modem]"
```

Enable the adapter in `config.toml`:

```toml
[sim7600]
enabled = true
port = "/dev/ttyUSB2"
baudrate = 115200
timeout_seconds = 2
enable_gnss = true
max_attempts = 2
retry_delay_seconds = 1
```

The exact serial port can vary by Pi image and modem mode. On the Pi, check:

```bash
ls /dev/ttyUSB*
```

Use the port that responds to AT commands. For many SIM7600 setups, that is
`/dev/ttyUSB2`, but confirm on the actual device.

The adapter retries transient serial/AT failures and reports modem errors inside
the `sim7600` sensor block. The rest of the telemetry service keeps running and
will continue to queue heartbeats if the network connection is unavailable.

## Example Heartbeat

```json
{
  "boat_id": "my-boat",
  "device_id": "raspberry-pi-bridge",
  "sequence": 42,
  "sent_at": "2026-04-28T12:00:00Z",
  "status": "ok",
  "sensors": {
    "gps": { "status": "ok", "latitude": 37.7749, "longitude": -122.4194 },
    "bilge": { "status": "ok", "active": false, "water_detected": false },
    "battery_soc": { "status": "ok", "percent": 87.5 },
    "system": { "status": "ok", "uptime_seconds": 1234.5 }
  }
}
```

## systemd

Use systemd to start PiBoatCore automatically when the Raspberry Pi boots.

Install the app and config:

```bash
cd ~/PiBoatCore
python3 -m venv .venv
source .venv/bin/activate
python -m pip install ".[modem]"

sudo mkdir -p /etc/piboatcore
sudo cp config.toml /etc/piboatcore/config.toml
sudo mkdir -p /var/lib/piboatcore
sudo chown "$USER":"$USER" /var/lib/piboatcore
```

In `/etc/piboatcore/config.toml`, use a spool path outside the repo:

```toml
[storage]
spool_db_path = "/var/lib/piboatcore/spool.db"
```

Install the service:

```bash
sudo cp systemd/piboatcore.service /etc/systemd/system/piboatcore.service
```

Edit `/etc/systemd/system/piboatcore.service` so `User`, `WorkingDirectory`,
and `ExecStart` match the Pi. For example, if the repo lives at
`/home/lukas/PiBoatCore`:

```ini
User=lukas
WorkingDirectory=/home/lukas/PiBoatCore
ExecStart=/home/lukas/PiBoatCore/.venv/bin/python -m pi_boat_core --config /etc/piboatcore/config.toml
```

Enable and start it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now piboatcore
```

Check logs:

```bash
sudo systemctl status piboatcore
journalctl -u piboatcore -f
```
