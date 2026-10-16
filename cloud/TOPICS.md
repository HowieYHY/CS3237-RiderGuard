# RiderGuard MQTT topics

Broker: Mosquitto on port 1883 (see `mosquitto.conf`). All payloads are JSON. `server.py` subscribes to `riderguard/#` and stores every message in MongoDB.

## Sensor data (ESP32 → cloud)

| Topic | Fields |
|---|---|
| `riderguard/leftGloveMPU6050` | `ax ay az` (m/s²), `gx gy gz` (deg/s), `distance_cm`, `bpm`, `avg_bpm`, `motor` |
| `riderguard/rightGloveMPU6050` | `ax ay az` (m/s²), `gx gy gz` (deg/s), `distance_cm`, `temperature_c`, `temperature_valid`, `led` |
| `riderguard/helmetMPU6050` | `ax ay az` (m/s²), `gx gy gz` (**rad/s** — Adafruit lib; should be deg/s to match gloves) |
| `riderguard/helmetGPS` | `latitude`, `longitude`, `speed_kmph` (`null` if unavailable) |
| `riderguard/helmetLIDAR` | currently raw hex bytes (stored as `{"raw": ...}`); proposed: `distance_cm` |

**To add on every sensor message** (needed for latency + aligning nodes):
- `seq` — counter, +1 per message
- `t_ms` — `millis()` when the reading was taken

## Labels (label tool → cloud)

- Topic: `riderguard/label`
- Payload: `{"label": "abrupt_turn"}` (plain text `abrupt_turn` also works); `{"label": null}` or empty stops labelling
- Publish with **retain = true**, so a restarted `server.py` picks up the current label.

`server.py` tags every stored reading with the current label; label messages themselves are not stored.

## Commands (cloud → ESP32) — draft, to agree with the decision-service owner

- Topic: `riderguard/cmd/<node>` (`leftGlove`, `rightGlove`, `helmet`)
- Payload: `{"action": "vibrate", "seq": <seq>, "t_ms": <t_ms>}` — `seq`/`t_ms` echoed from the triggering reading, so the ESP32 computes round-trip latency as `millis() - t_ms` without clock sync.
- Commands are stored like readings, giving a history of warnings for the dashboard.
