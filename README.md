# PiBoatCore

PiBoatCore is a Raspberry Pi telemetry service for a boat. It polls onboard
sensors, builds a heartbeat payload, and sends it to a central Node.js service.
If Wi-Fi or cellular is down, payloads are stored locally in SQLite and retried
on the next heartbeat.

## Current Sensors

- System health: uptime, CPU load, memory, disk
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
```

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

Copy `systemd/piboatcore.service` to `/etc/systemd/system/piboatcore.service`,
copy your config to `/etc/piboatcore/config.toml`, then enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now piboatcore
```
