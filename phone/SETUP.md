# RiderGuard on an Android phone

The phone is the hotspot, MQTT broker, server, model and dashboard. No laptop needed.

## One-time setup
1. Install **Termux** (and **Termux:Widget** for a home-screen button).
2. In Termux:
   ```
   pkg update
   pkg install python mosquitto unzip
   pip install flask paho-mqtt pandas
   termux-setup-storage
   ```
   `termux-setup-storage` opens "All files access": turn it on for Termux.
   scikit-learn is not needed on the phone (it has no Termux build). The model runs from `model.json` with numpy.
3. Put `riderguard_phone.zip` in the phone's Download folder, then in Termux:
   ```
   unzip -o ~/storage/downloads/riderguard_phone.zip -d ~ && rm -rf ~/riderguard && mv ~/riderguard_phone ~/riderguard
   mkdir -p ~/.shortcuts && cp ~/riderguard/start.sh ~/.shortcuts/RiderGuard && chmod +x ~/.shortcuts/RiderGuard
   ```
4. Add the Termux:Widget widget to the home screen. It shows a **RiderGuard** button.
5. Android settings > Apps > Termux > Battery > **Unrestricted**.
6. Firmware: in all three sketches set the WiFi to this phone's hotspot and the server to
   `mqtt://<phone hotspot IP>:1883`. `start.sh` prints the phone's IPs. On newer Android the hotspot IP can
   change when the hotspot is turned off and on, so check it before each demo.

## Every ride
1. Turn on the hotspot, then tap the **RiderGuard** widget (or run `bash ~/riderguard/start.sh`).
   The dashboard opens at http://localhost:3238.
2. Power on the helmet and gloves, tap **Start trip**.
3. The Connecting screen shows the server, model, each device and GPS. It switches to the ride view once all
   three devices are sending (or tap Start anyway).
4. Tap **End trip** for the score, the route coloured by safe / hazardous riding and the hazard timeline.
   Past trips are listed on the start screen.

Trips are saved in `~/riderguard/trips/` (one `.jsonl` per trip).
A laptop on the same hotspot can also open the dashboard at `http://<phone hotspot IP>:3238`, e.g. for a projector.

## Files
| File | What it does |
|---|---|
| `rider_dashboard.py` | Flask dashboard + MQTT subscriber + trip recording |
| `predict.py`, `features.py` | Live classifier (same features as training) |
| `model.json` | Trained model for the phone. After retraining on the laptop run `python export_model.py model.joblib model.json` and copy it over |
| `export_model.py` | Laptop only: converts `model.joblib` to `model.json` and checks both predict the same |
| `mosquitto.conf` | Broker config: port 1883, open to the hotspot |
| `start.sh` | Starts broker + dashboard and opens the page |
| `templates/`, `static/` | Dashboard page and background photo |
