# CS3237-RiderGuard

RiderGuard is an IoT-based motorcyclist hazard warning and accident detection system developed for CS3237 Introduction to Internet-of-Things.

## Project Structure

- `RiderGuard_Left_glove/` — Left glove ESP32 firmware and sensor data publishing via MQTT.
- `RiderGuard_Right_glove/` — Right glove ESP32 firmware and sensor data publishing via MQTT.
- `cloud/` — MQTT server, dashboard, and Mosquitto broker configuration.

## Running the Cloud System

### Prerequisites

- WSL Ubuntu
- Mosquitto MQTT broker
- Python dependencies for the cloud server and dashboard
- MongoDB
- ESP32 gloves configured to connect to the MQTT broker

### Terminal 1 — MQTT Broker

```bash
cd /mnt/c/Users/LENOVO/CS3237-RiderGuard
sudo systemctl stop mosquitto
mosquitto -c cloud/mosquitto.conf -v
```

Keep this terminal open to monitor MQTT connections and incoming messages.

### Terminal 2 — Python MQTT Server

```bash
cd /mnt/c/Users/LENOVO/CS3237-RiderGuard
python3 cloud/server.py --mongo
```

Keep this terminal open to monitor the MQTT subscription and sensor data processing.

### Terminal 3 — Dashboard

```bash
cd /mnt/c/Users/LENOVO/CS3237-RiderGuard
sudo systemctl start mongod
python3 cloud/dashboard.py
```

Open the dashboard in your browser:

http://localhost:3237

### Start the ESP32 Gloves

Once the MQTT broker and Python server are running, power on or reset both ESP32 gloves. Check the broker logs and dashboard for incoming sensor readings.

### MQTT Topics

- **Left glove:** `riderguard/leftGloveMPU6050`
- **Right glove:** `riderguard/rightGloveMPU6050`

### Troubleshooting

- If port 1883 is already in use, check whether another Mosquitto instance is running.
- If the dashboard reports a database connection error, check that MongoDB is running.
- Ensure both ESP32 devices connect to the correct Wi-Fi network and MQTT broker address.
