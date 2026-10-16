import argparse
import json

import paho.mqtt.client as mqtt

from decision import object_hazard, lane_split_ok

DISTANCE_TOPICS = {
    "riderguard/leftGloveMPU6050": "leftGlove",
    "riderguard/rightGloveMPU6050": "rightGlove",
    "riderguard/helmetLIDAR": "helmet",
}
LANE_TOPIC = "riderguard/laneCheck"
FEEDBACK_TOPIC = "riderguard/feedback"


def make_client():
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    except AttributeError:
        return mqtt.Client()


def on_connect(client, userdata, flags, rc):
    print("Connected, rc =", rc)
    for topic in DISTANCE_TOPICS:
        client.subscribe(topic)
    client.subscribe(LANE_TOPIC)


def on_message(client, userdata, message):
    try:
        data = json.loads(message.payload.decode("utf-8"))
    except ValueError:
        print(message.topic, "skipped, payload is not JSON")
        return

    if message.topic in DISTANCE_TOPICS:
        distance = data.get("distance_cm")
        if distance is None or distance < 0:
            print(message.topic, "skipped, no valid distance_cm")
            return
        if object_hazard(distance):
            node = DISTANCE_TOPICS[message.topic]
            command = {"action": "vibrate", "seq": data.get("seq"), "t_ms": data.get("t_ms")}
            client.publish("riderguard/cmd/" + node, json.dumps(command))
            print(message.topic, "seq", data.get("seq"), "distance", distance, "-> vibrate", node)
        else:
            print(message.topic, "seq", data.get("seq"), "distance", distance, "-> safe")
        return

    result = "OK_TO_SPLIT" if lane_split_ok(data["left_cm"], data["right_cm"]) else "NOT_OK"
    client.publish(FEEDBACK_TOPIC, json.dumps({"seq": data.get("seq"), "result": result}))
    print(message.topic, data, "->", result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    args = parser.parse_args()

    client = make_client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.host, 1883, 60)
    client.loop_forever()
