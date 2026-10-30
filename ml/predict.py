# RiderGuard live classifier (pattern from Lab 5 server.py --model ml)
#   python3 predict.py                               -> left glove, model.joblib, broker on localhost
#   python3 predict.py --topic riderguard/rightGloveMPU6050 --host 192.168.x.y
# Keeps the last bundle["window"] readings and classifies every bundle["step"] new readings,
# using the same features as train.py. Result is published to riderguard/cmd/<node> (TOPICS.md),
# echoing the newest reading's seq/t_ms so the ESP32 measures round-trip latency as millis() - t_ms.
import argparse
import json
from collections import deque

import joblib
import paho.mqtt.client as mqtt
import pandas as pd

from features import AXES, window_features

CMD_TOPIC = "riderguard/cmd/"


def make_client():
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    except AttributeError:
        return mqtt.Client()


def on_connect(client, userdata, flags, rc):
    print("Connected with result code: " + str(rc))
    client.subscribe(userdata["topic"])


def on_message(client, userdata, message):
    try:
        data = json.loads(message.payload.decode("utf-8"))
        userdata["buf"].append([float(data[a]) for a in AXES])
    except (ValueError, KeyError, TypeError):
        return  # skip malformed readings

    userdata["count"] += 1
    bundle = userdata["bundle"]
    if len(userdata["buf"]) < bundle["window"] or userdata["count"] % bundle["step"]:
        return

    x = pd.DataFrame([window_features(pd.DataFrame(list(userdata["buf"]), columns=AXES))])
    label = str(bundle["model"].predict(x)[0])
    print(f"{message.topic} seq {data.get('seq')} -> {label}")
    # Reply on every prediction, echoing seq/t_ms of the newest reading so the ESP32 can time the round trip
    command = {"action": "none" if label == "safe" else "vibrate", "label": label,
               "seq": data.get("seq"), "t_ms": data.get("t_ms")}
    client.publish(userdata["cmd_topic"], json.dumps(command))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", default="riderguard/leftGloveMPU6050")
    parser.add_argument("--model", default="model.joblib")
    parser.add_argument("--host", default="localhost")
    args = parser.parse_args()

    bundle = joblib.load(args.model)
    print(f"Loaded {args.model}: labels {bundle['labels']}, window {bundle['window']}, step {bundle['step']}")

    client = make_client()
    node = args.topic.split("/")[-1].replace("MPU6050", "")  # riderguard/leftGloveMPU6050 -> leftGlove
    client.user_data_set({"topic": args.topic, "cmd_topic": CMD_TOPIC + node, "bundle": bundle, "count": 0,
                          "buf": deque(maxlen=bundle["window"])})
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, 1883, 60)
    client.loop_forever()
