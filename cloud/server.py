# RiderGuard backend: subscribes to all ESP32 nodes, logs readings, optionally stores to MongoDB
#   python3 server.py                     -> print only
#   python3 server.py --mongo             -> also store in MongoDB (riderguard.readings)
#   python3 server.py --host 192.168.x.y  -> broker on another laptop
#   python3 server.py --user U --password P -> broker with password_file enabled (see mosquitto.conf)
#
# Setup (WSL): sudo apt-get install mosquitto mosquitto-clients
#              pip install paho-mqtt pymongo
# ESP32 sketches publish JSON to riderguard/<node><sensor>, e.g. riderguard/helmetMPU6050
# Label tool publishes {"label": ...} to riderguard/label; readings are tagged with the current label
# Topic/payload spec: TOPICS.md
import argparse
import json
import time

import paho.mqtt.client as mqtt

TOPIC = "riderguard/#"
LABEL_TOPIC = "riderguard/label"


def make_client():
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    except AttributeError:
        return mqtt.Client()


def on_connect(client, userdata, flags, rc):
    print("Connected with result code: " + str(rc))
    client.subscribe(TOPIC)


def on_message(client, userdata, message):
    payload = message.payload.decode("utf-8")
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        data = {"raw": payload}  # e.g. helmet LiDAR raw hex

    if message.topic == LABEL_TOPIC:
        # accepts {"label": "safe"} or plain text "safe"; null/empty clears the label
        userdata["label"] = (data.get("label", data.get("raw")) if isinstance(data, dict) else data) or None
        print(f"Label set to: {userdata['label']}")
        return

    print(f"Received {message.topic} [{userdata['label']}]: {data}")

    if userdata["db"] is not None:
        userdata["db"].insert_one({"topic": message.topic, "received_at": time.time(),
                                   "label": userdata["label"], "data": data})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--mongo", action="store_true")
    parser.add_argument("--user")
    parser.add_argument("--password")
    args = parser.parse_args()

    db = None
    if args.mongo:
        from pymongo import MongoClient
        db = MongoClient("mongodb://localhost:27017/")["riderguard"]["readings"]

    client = make_client()
    if args.user:
        client.username_pw_set(args.user, args.password)
    client.user_data_set({"db": db, "label": None})
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, 1883, 60)
    client.loop_forever()
