import argparse
import json
import random
import statistics
import time

import paho.mqtt.client as mqtt

SENSOR_TOPIC = "riderguard/helmetLIDAR"
CMD_TOPIC = "riderguard/cmd/helmet"

start = time.perf_counter()
latencies = []


def millis():
    return int((time.perf_counter() - start) * 1000)


def make_client():
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    except AttributeError:
        return mqtt.Client()


def on_connect(client, userdata, flags, rc):
    client.subscribe(CMD_TOPIC)


def on_message(client, userdata, message):
    command = json.loads(message.payload.decode("utf-8"))
    ms = millis() - command["t_ms"]
    latencies.append(ms)
    print(f"seq {command['seq']}: {command['action']} round trip {ms} ms")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--n", type=int, default=20)
    args = parser.parse_args()
    if args.host not in ("localhost", "127.0.0.1"):
        print("WARNING: fake data goes to a shared broker. Make sure server.py --mongo is not running.")

    client = make_client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, 1883, 60)
    client.loop_start()
    time.sleep(1)

    for seq in range(args.n):
        msg = {"seq": seq, "t_ms": millis(), "distance_cm": random.randint(50, 190)}
        client.publish(SENSOR_TOPIC, json.dumps(msg))
        time.sleep(0.5)

    time.sleep(1)
    if latencies:
        s = sorted(latencies)
        print(f"\nreceived {len(latencies)}/{args.n}")
        print(f"mean {statistics.mean(s):.1f} ms, median {statistics.median(s):.1f} ms, max {s[-1]} ms")
    else:
        print("no replies received")
    client.loop_stop()
