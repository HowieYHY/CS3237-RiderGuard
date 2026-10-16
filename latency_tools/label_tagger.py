import argparse
import json
import time

import paho.mqtt.client as mqtt

TOPIC = "riderguard/label"


def make_client():
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    except AttributeError:
        return mqtt.Client()


def send(client, label, state):
    client.publish(TOPIC, json.dumps({"label": label, "state": state, "t": time.time()}))
    print(f"  sent: {label} {state}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    args = parser.parse_args()

    client = make_client()
    client.connect(args.host, 1883, 60)
    client.loop_start()

    print("Type a label (e.g. safe, abrupt_turn, object_close) and press Enter to START it.")
    print("Press Enter on an empty line to STOP. Type q to quit.")
    current = None
    while True:
        text = input("label> ").strip()
        if current:
            send(client, current, "stop")
            current = None
        if text == "q":
            break
        if text:
            send(client, text, "start")
            current = text

    time.sleep(0.5)
    client.loop_stop()
