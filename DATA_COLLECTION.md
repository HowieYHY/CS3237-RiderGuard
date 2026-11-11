# RiderGuard data collection guide

How to set up the gloves, helmet and laptop, record labelled riding data, and export it as CSV.
Latency testing is described separately in [latency_tools/LATENCY.md](latency_tools/LATENCY.md).

## What's involved

```
Left glove  ─┐
Right glove ─┼─ Wi-Fi ─► Mosquitto broker (laptop) ─► cloud/server.py ─► MongoDB
Helmet      ─┘                    ▲
                    latency_tools/label_tagger.py  (sets the current label)
```
`server.py` stores every message with the label that is active at that moment. `cloud/export.py` turns the stored data into CSV files.

| Device | Sketch | Topic | Rate |
|---|---|---|---|
| Left glove | `RiderGuard_Left_glove/RiderGuard_Left_glove.ino` | `riderguard/leftGloveMPU6050` | ~2 / s |
| Right glove | `RiderGuard_Right_glove/RiderGuard_Right_glove.ino` | `riderguard/rightGloveMPU6050` | ~1 / s |
| Helmet | `RiderGuard_Helmet/RiderGuard_Helmet.ino` | `riderguard/location` | 1 / s |

---

## 1. One-time setup

### Code
```
git clone https://github.com/HowieYHY/CS3237-RiderGuard.git
cd CS3237-RiderGuard
git checkout HowieYHY-data-labelling
```

### Python (Windows)
```
pip install paho-mqtt pymongo pandas scikit-learn joblib matplotlib flask
```

### MongoDB
Install MongoDB Community Server (Windows) and leave "Install as a Service" ticked. It then runs in the background automatically.

### Mosquitto broker (WSL Ubuntu)
```
sudo apt-get install mosquitto mosquitto-clients
sudo systemctl disable --now mosquitto   # we start it ourselves with the repo config
```
So that the ESP32s can reach a broker running inside WSL, create `C:\Users\<you>\.wslconfig` with:
```
[wsl2]
networkingMode=mirrored
```
then run `wsl --shutdown` once.

### Arduino IDE
- Board package: **esp32** by Espressif (Boards Manager).
- Libraries (Library Manager unless noted):
  - **ESP32MQTTClient** (all devices)
  - **OneWire** and **DallasTemperature** (right glove)
  - **TinyGPSPlus** by Mikal Hart (helmet)
  - **LDS** by kaiaai (helmet). Not in Library Manager: download the ZIP from https://github.com/kaiaai/LDS and use *Sketch → Include Library → Add .ZIP Library*.

## 2. Network and firmware

All devices and the laptop must be on the **same network**. A phone hotspot works best (campus Wi-Fi usually blocks device-to-device traffic).

The sketches in the repo contain **placeholders**, not real network details. Fill them in yourself before uploading, and don't commit your filled-in values.

1. Connect the laptop to the hotspot and run `ipconfig`. Note the Wi-Fi **IPv4 Address**.
2. In **each** of the three sketches, replace the placeholders near the top:

   | Placeholder | Replace with | Example |
   |---|---|---|
   | `YOUR_WIFI_SSID` | hotspot / Wi-Fi name | `"MyHotspot"` |
   | `YOUR_WIFI_PASSWORD` | its password | `"secret123"` |
   | `YOUR_IP_ADDRESS` | laptop IPv4 from step 1 (keep `mqtt://` and `:1883`) | `"mqtt://<laptop IPv4>:1883"` |

   ```cpp
   const char *ssid = "YOUR_WIFI_SSID";
   const char *pass = "YOUR_WIFI_PASSWORD";        // right glove: *password
   char *server = "mqtt://YOUR_IP_ADDRESS:1883";
   ```
3. Upload each sketch to its device. Serial Monitor (115200) shows the ESP32's IP once Wi-Fi connects and `MQTT CONNECTED!` once it reaches the broker.

If the laptop's IP changes (different network, reconnect), update `server` in all three and re-upload.
To keep your values out of git:
```
git update-index --skip-worktree RiderGuard_Left_glove/RiderGuard_Left_glove.ino RiderGuard_Right_glove/RiderGuard_Right_glove.ino RiderGuard_Helmet/RiderGuard_Helmet.ino
```
(undo with `--no-skip-worktree` before pulling changes to those files).

## 3. Start the system (each session)

Open terminals in the repo folder.

| Terminal | Command | Expected |
|---|---|---|
| 1 Broker | `wsl -d Ubuntu -- mosquitto -c cloud/mosquitto.conf -v` (run from the repo folder) | `mosquitto version ... running` |
| 2 Server | `python cloud/server.py --mongo` | `Connected with result code: 0` |
| 3 Labels | `python latency_tools/label_tagger.py` | `label>` prompt |

Power on the devices. Terminal 1 shows `New connection from <ip>` for each one, and terminal 2 prints `Received riderguard/... [None]: {...}` for each topic.

Optional dashboard: `python cloud/dashboard.py` → http://localhost:3237

## 4. Record labelled data

Record with **all three devices on** for every run: the model uses both gloves and the helmet together.

Classes:

| Label | Ride |
|---|---|
| `safe` | normal riding: normal turns, normal braking, steady speed. **Needed** – without it the model can only answer with a hazard |
| `abrupt_turn` | sharp, sudden turn (`abrupt_left_turn` / `abrupt_right_turn` are also accepted and counted as `abrupt_turn`) |
| `hardbrake` | hard braking. Start the label just **before** braking so the speed drop is inside the run |
| `uneven_road` | riding over an uneven surface |
| `speeding` | riding fast |
| `unsafe_following_distance` | riding close behind someone/something in front |

In terminal 3, alternate the actions. A number after the label stops it automatically after that many seconds:
```
label> safe 20
label> abrupt_turn 10
label> speeding 15
label> hardbrake 10
label> safe 20
...
label> q                quit (always use q, never just close the window)
```
- Aim for **at least 6 runs per class**, `safe` included. The model splits train/test by run, so many short runs are better than a few long ones, and each run should be **at least 6 s** (windows are 4 s).
- A run-order suffix is fine: `uneven_road_1`, `uneven_road_2` are two runs of `uneven_road` (training strips `_<number>`).
- During a run do **only** that action. Ctrl+C ends a timed run early.
- Spell labels as above. A typo becomes a new class.
- Check terminal 2 now and then: readings from **every** device show the label (e.g. `[speeding]`) while a run is active, and `[None]` between runs. A run where the helmet was silent can't be used: run boundaries are taken from the helmet's data.
- If the tagger window was closed without `q`, start it again and type `q`. Otherwise the last label stays active.

Data recorded before 11 Nov 2026 used the old labels (`safe`, `abrupt_turn`, `hard_break`) and is outdated – don't train on it.

## 5. Export to CSV

```
python cloud/export.py --labelled --out exports/<date>
```
e.g. `python cloud/export.py --labelled --out exports/2026-11-20`

Creates one CSV per device:
```
exports/2026-11-20/
  leftGloveMPU6050.csv    received_at, label, seq, t_ms, ax, ay, az, gx, gy, gz, distance_cm, motor
  rightGloveMPU6050.csv   received_at, label, seq, t_ms, ax, ..., distance_cm, temperature_c, temperature_valid, led
  location.csv            received_at, label, seq, t_ms, gps_status, ..., nearest_distance_cm, nearest_angle_deg
```
- `received_at` is the laptop's Unix time, `label` is the action, the rest is the device's message.
- `--labelled` keeps only readings recorded during a run. Leave it out to export everything. `--label safe` exports one label.
- The export includes all labelled data in your MongoDB, not only today's. Send the CSV files to the team; don't commit them.

## 6. Train a model (optional check)

One model for all three devices. Give each session's three CSVs (left glove, right glove, helmet) with `--session`; repeat `--session` to combine sessions:
```
python ml/train.py --session exports/<date>/leftGloveMPU6050.csv exports/<date>/rightGloveMPU6050.csv exports/<date>/location.csv --window 4 --step 1 --out model.joblib
```
e.g. two sessions:
```
python ml/train.py ^
  --session exports/2026-11-20/leftGloveMPU6050.csv exports/2026-11-20/rightGloveMPU6050.csv exports/2026-11-20/location.csv ^
  --session exports/2026-11-27/leftGloveMPU6050.csv exports/2026-11-27/rightGloveMPU6050.csv exports/2026-11-27/location.csv ^
  --out model.joblib
```
Prints windows per class, grouped cross-validation accuracy, test accuracy and a confusion matrix.
- `--window` / `--step` are in **seconds** (default 4 / 1): every window takes each device's readings from the same 4 s, matched by `received_at`.
- Labelled runs and their labels come from `location.csv`; the gloves' readings are matched to them by time, so a typo in a glove file's `label` column doesn't matter.
- Features per window: both gloves' IMU statistics and ultrasonic distance, helmet GPS speed (incl. change over the window) and LiDAR nearest distance. GPS position is not used.
- `--classes safe speeding hardbrake` trains on a subset.

Live prediction with the trained model (replies to both gloves): `python ml/predict.py --model model.joblib`

## 7. Shut down

`q` in the label tagger, then Ctrl+C in terminal 2 and terminal 1. Data stays in MongoDB (`riderguard.readings`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ConnectionRefusedError` from a Python script | Broker not running (terminal 1) |
| No `New connection` from a device | Wrong SSID/password/IP in the sketch, laptop on a different network, or Windows Firewall: in admin PowerShell `New-NetFirewallRule -DisplayName "Mosquitto 1883" -Direction Inbound -Protocol TCP -LocalPort 1883 -Action Allow` |
| Data stops mid-session | Laptop switched networks (e.g. back to campus Wi-Fi). Reconnect, restart terminal 2 |
| Arduino `<name>.h: No such file` | Install that library (section 1) |
| Arduino `Server.h` missing | ESP32 board package updated during compile. Restart the IDE |
| Glove IMU values exactly `0.0` | I2C dropout. Reseat SDA (21) / SCL (22) wires |
| Helmet `lidar_status: "NO SCAN DATA"` | LiDAR not sending. Check its wiring/power |
