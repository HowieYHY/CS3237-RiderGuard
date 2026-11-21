# RiderGuard live classifier (pattern from Lab 5 server.py --model ml)
#   python3 predict.py                                -> model.joblib, broker on localhost
#   python3 predict.py --model fused.joblib --host 192.168.x.y
#   python3 predict.py --model model.json             -> numpy-only model from export_model.py (no scikit-learn)
# On the phone, rider_dashboard.py imports Predictor and runs it inside the app instead of this script.
# Listens to all three devices, keeps the last bundle["window"] seconds from each and classifies every
# bundle["step"] seconds, using the same features as train.py. Result is published to riderguard/cmd/leftGlove
# and riderguard/cmd/rightGlove (TOPICS.md), echoing that glove's newest seq/t_ms so the ESP32 measures
# round-trip latency as millis() - t_ms.
import argparse
import json
import time
from collections import deque

import numpy as np
import paho.mqtt.client as mqtt
import pandas as pd

from features import fused_features

CMD_TOPIC = "riderguard/cmd/"
TOPICS = {"riderguard/leftGloveMPU6050": "left", "riderguard/rightGloveMPU6050": "right",
          "riderguard/location": "helmet"}
NO_ALERT = {"safe", "uneven_road"}  # labels that do not vibrate the gloves (same as the dashboard)
# Stillness rule: gloves at rest (stopped at a light, on a table) were never in the training data, so the model
# guesses. If BOTH gloves barely rotate and barely shake, call it safe without asking the model.
# Training data never gets this still: the stillest riding window of any class is ~19 deg/s.
STILL_GYRO_DPS = 10   # mean rotation speed per glove
STILL_ACC_STD = 0.5   # spread of acceleration magnitude per glove (m/s^2)
# Training data was recorded with gloves every ~0.5 s and the helmet every 1 s. The devices now publish every
# 100 ms, so the classifier keeps one reading per this many seconds per device (slightly under, to allow for
# jitter) and the windows look like the training data. The dashboard still gets every reading.
TRAIN_SPACING_S = {"left": 0.45, "right": 0.45, "helmet": 0.95}


def is_still(f):
    return all(f[f"{s}_gyr_mag_mean"] < STILL_GYRO_DPS and f[f"{s}_acc_mag_std"] < STILL_ACC_STD for s in ("L", "R"))


class NumpySVM:
    """train.py's pipeline (median imputer -> standard scaler -> RBF SVC) re-run with numpy only, from the
    model.json written by export_model.py. Used on the phone, where scikit-learn cannot be installed.
    Multi-class SVC predicts by one-vs-one voting, like libsvm; ties go to the earlier class."""

    def __init__(self, m):
        self.features, self.classes = m["features"], m["classes"]
        self.median, self.mean, self.scale = (np.array(m[k]) for k in ("median", "mean", "scale"))
        self.sv, self.coef, self.icpt = (np.array(m[k]) for k in ("support_vectors", "dual_coef", "intercept"))
        self.gamma = m["gamma"]
        self.start = np.concatenate([[0], np.cumsum(m["n_support"])])

    def predict(self, x):
        v = x.reindex(columns=self.features).to_numpy(dtype=float)[0]
        v = (np.where(np.isnan(v), self.median, v) - self.mean) / self.scale
        k = np.exp(-self.gamma * ((self.sv - v) ** 2).sum(axis=1))
        n, s, votes, p = len(self.classes), self.start, np.zeros(len(self.classes), int), 0
        for i in range(n):
            for j in range(i + 1, n):
                d = (self.coef[j - 1, s[i]:s[i + 1]] @ k[s[i]:s[i + 1]]
                     + self.coef[i, s[j]:s[j + 1]] @ k[s[j]:s[j + 1]] + self.icpt[p])
                votes[i if d > 0 else j] += 1
                p += 1
        return [self.classes[int(np.argmax(votes))]]


def load_bundle(path):
    """model.json (phone, numpy only) or model.joblib (laptop, scikit-learn) -> bundle dict."""
    if path.endswith(".json"):
        with open(path) as f:
            m = json.load(f)
        return {"model": NumpySVM(m), "window": m["window"], "step": m["step"], "labels": m["labels"]}
    import joblib
    return joblib.load(path)


class Predictor:
    """Keeps the last bundle["window"] s of readings per device and classifies every bundle["step"] s."""

    def __init__(self, bundle):
        self.bundle = bundle
        self.reset()

    def reset(self):
        self.bufs = {dev: deque() for dev in TOPICS.values()}
        self.kept_at = {dev: float("-inf") for dev in TOPICS.values()}
        self.newest = {dev: None for dev in TOPICS.values()}  # newest reading, kept or not, for the seq echo
        self.last = 0.0

    def add(self, topic, data, now):
        """Feed one reading. Returns [(cmd_topic, command)] when a prediction is due, else []."""
        if topic not in TOPICS or not isinstance(data, dict):
            return []
        bufs, bundle, dev = self.bufs, self.bundle, TOPICS[topic]
        reading = dict(data, received_at=now)
        self.newest[dev] = reading
        if now - self.kept_at[dev] >= TRAIN_SPACING_S[dev]:
            bufs[dev].append(reading)
            self.kept_at[dev] = now
        for buf in bufs.values():
            while buf and buf[0]["received_at"] < now - bundle["window"]:
                buf.popleft()
        if now - self.last < bundle["step"] or not all(bufs.values()):
            return []  # not time yet, or a device has been silent for a whole window
        self.last = now

        part = {k: pd.DataFrame(list(buf)) for k, buf in bufs.items()}
        f = fused_features(part["left"], part["right"], part["helmet"])
        label = "safe" if is_still(f) else str(bundle["model"].predict(pd.DataFrame([f]))[0])
        print(f"-> {label}")
        out = []
        for node, dev in (("leftGlove", "left"), ("rightGlove", "right")):
            newest = self.newest[dev]
            out.append((CMD_TOPIC + node, {"action": "none" if label in NO_ALERT else "vibrate", "label": label,
                                           "seq": newest.get("seq"), "t_ms": newest.get("t_ms")}))
        return out


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
    for topic, command in userdata.add(message.topic, data, time.time()):
        client.publish(topic, json.dumps(command))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="model.joblib")
    parser.add_argument("--host", default="localhost")
    args = parser.parse_args()

    bundle = load_bundle(args.model)
    print(f"Loaded {args.model}: labels {bundle['labels']}, window {bundle['window']} s, step {bundle['step']} s")

    client = make_client()
    client.user_data_set(Predictor(bundle))
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, 1883, 60)
    client.loop_forever()
