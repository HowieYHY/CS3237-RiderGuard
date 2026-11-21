# RiderGuard live classifier (pattern from Lab 5 server.py --model ml)
#   python3 predict.py                                -> model.joblib, broker on localhost
#   python3 predict.py --model fused.joblib --host 192.168.x.y
# Listens to all three devices, keeps the last bundle["window"] seconds from each and classifies every
# bundle["step"] seconds, using the same features as train.py. Result is published to riderguard/cmd/leftGlove
# and riderguard/cmd/rightGlove (TOPICS.md), echoing that glove's newest seq/t_ms so the ESP32 measures
# round-trip latency as millis() - t_ms.
import argparse
import json
import time
from collections import deque

import joblib
import paho.mqtt.client as mqtt
import pandas as pd

from features import fused_features

CMD_TOPIC = "riderguard/cmd/"
TOPICS = {"riderguard/leftGloveMPU6050": "left", "riderguard/rightGloveMPU6050": "right",
          "riderguard/location": "helmet"}


def make_client():
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    except AttributeError:
        return mqtt.Client()


def on_connect(client, userdata, flags, rc):
    print("Connected with result code: " + str(rc))
    for topic in TOPICS:
        client.subscribe(topic)


def on_message(client, userdata, message):
    try:
        data = json.loads(message.payload.decode("utf-8"))
    except ValueError:
        return
    if not isinstance(data, dict):
        return  # skip malformed readings
    now = time.time()
    data["received_at"] = now  # same clock as server.py, which is what train.py windows on
    bufs, bundle = userdata["bufs"], userdata["bundle"]
    bufs[TOPICS[message.topic]].append(data)
    for buf in bufs.values():
        while buf and buf[0]["received_at"] < now - bundle["window"]:
            buf.popleft()
    if now - userdata["last"] < bundle["step"] or not all(bufs.values()):
        return  # not time yet, or a device has been silent for a whole window
    userdata["last"] = now

    part = {k: pd.DataFrame(list(buf)) for k, buf in bufs.items()}
    x = pd.DataFrame([fused_features(part["left"], part["right"], part["helmet"])])
    label = str(bundle["model"].predict(x)[0])
    print(f"-> {label}")
    for node, dev in (("leftGlove", "left"), ("rightGlove", "right")):
        newest = bufs[dev][-1]
        command = {"action": "none" if label == "safe" else "vibrate", "label": label,
                   "seq": newest.get("seq"), "t_ms": newest.get("t_ms")}
        client.publish(CMD_TOPIC + node, json.dumps(command))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="model.joblib")
    parser.add_argument("--host", default="localhost")
    args = parser.parse_args()

    bundle = joblib.load(args.model)
    print(f"Loaded {args.model}: labels {bundle['labels']}, window {bundle['window']} s, step {bundle['step']} s")

    client = make_client()
    client.user_data_set({"bundle": bundle, "bufs": {dev: deque() for dev in TOPICS.values()}, "last": 0.0})
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, 1883, 60)
    client.loop_forever()
