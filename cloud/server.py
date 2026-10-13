# RiderGuard backend: subscribes to all ESP32 nodes, logs readings, optionally stores to MongoDB
#   python3 server.py                     -> print only
#   python3 server.py --mongo             -> also store in MongoDB (riderguard.readings)
#   python3 server.py --host 192.168.x.y  -> broker on another laptop
#
# Setup (WSL): sudo apt-get install mosquitto mosquitto-clients
#              pip install paho-mqtt pymongo
# ESP32 sketches publish JSON to riderguard/<node><sensor>, e.g. riderguard/helmetMPU6050
import argparse
import json
import time

import paho.mqtt.client as mqtt

TOPIC = "riderguard/#"


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

    print(f"Received {message.topic}: {data}")

    if userdata["db"] is not None:
        userdata["db"].insert_one({"topic": message.topic, "received_at": time.time(), "data": data})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--mongo", action="store_true")
    args = parser.parse_args()

    db = None
    if args.mongo:
        from pymongo import MongoClient
        db = MongoClient("mongodb://localhost:27017/")["riderguard"]["readings"]

    client = make_client()
    client.user_data_set({"db": db})
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, 1883, 60)
    client.loop_forever()
