import argparse
import json
import random
import time

import paho.mqtt.client as mqtt

TOPIC = "riderguard/rightGloveMPU6050"
CMD_TOPIC = "riderguard/cmd/rightGlove"

start = time.perf_counter()


def millis():
    return int((time.perf_counter() - start) * 1000)


def make_client():
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    except AttributeError:
        return mqtt.Client()


def fake_reading(seq):
    distance = round(random.uniform(10, 200), 2)
    return {
        "seq": seq,
        "t_ms": millis(),
        "ax": round(random.gauss(0, 1.5), 3),
        "ay": round(random.gauss(0, 1.5), 3),
        "az": round(random.gauss(9.8, 1.5), 3),
        "gx": round(random.gauss(0, 20), 3),
        "gy": round(random.gauss(0, 20), 3),
        "gz": round(random.gauss(0, 20), 3),
        "distance_cm": distance,
        "temperature_c": round(random.uniform(28, 33), 2),
        "temperature_valid": True,
        "led": "ON" if distance < 30 else "OFF",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--n", type=int, default=20)
    args = parser.parse_args()

    def on_connect(client, userdata, flags, rc):
        client.subscribe(CMD_TOPIC)

    def on_message(client, userdata, message):
        command = json.loads(message.payload.decode("utf-8"))
        print(f"  warning for seq {command['seq']}: {command['action']}, round trip {millis() - command['t_ms']} ms")

    client = make_client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, 1883, 60)
    client.loop_start()
    time.sleep(1)
    for i in range(args.n):
        msg = fake_reading(i)
        client.publish(TOPIC, json.dumps(msg))
        print(i, msg)
        time.sleep(0.5)
    time.sleep(0.5)
    client.loop_stop()
