#!/data/data/com.termux/files/usr/bin/bash
# RiderGuard phone launcher: broker + dashboard, then opens the page.
# Copy to ~/.shortcuts/ to get a Termux:Widget home-screen button.
RG_DIR="${RG_DIR:-$HOME/riderguard}"
cd "$RG_DIR" || { echo "No RiderGuard folder at $RG_DIR"; exit 1; }

termux-wake-lock                                   # stop Android killing the server when the screen is off
pgrep -x mosquitto >/dev/null || mosquitto -c mosquitto.conf -d
pkill -f rider_dashboard.py 2>/dev/null           # restart cleanly if it was already running

echo "Phone IP addresses (the hotspot one goes in the ESP32 sketches as mqtt://<ip>:1883):"
ifconfig 2>/dev/null | grep -o 'inet [0-9.]*' | grep -v '127.0.0.1'

python rider_dashboard.py &
sleep 3
termux-open-url http://localhost:3238
wait
