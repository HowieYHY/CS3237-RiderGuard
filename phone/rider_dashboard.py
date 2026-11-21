# RiderGuard phone app: dashboard + MQTT subscriber + live classifier + trip recording in one process.
# Pattern: Lecture 7 testsite.py (Flask) + mqtt_listen.py (paho), Lab 5 server.py --model.
#   mosquitto -c mosquitto.conf -d     (start.sh does both lines)
#   python rider_dashboard.py          -> open http://localhost:3238 and tap Start trip
# Start trip connects to the broker, loads nothing new (model is loaded at launch) and starts recording.
# Live data goes MQTT -> memory -> /api/state, no database. Every message of a trip is also appended to
# trips/<trip id>.jsonl, which the ride summary and trip history read back.
import argparse
import bisect
import glob
import json
import math
import os
import re
import threading
import time
from datetime import datetime

import paho.mqtt.client as mqtt
from flask import Flask, jsonify, render_template, request

from predict import Predictor, load_bundle, make_client

app = Flask(__name__)
HERE = os.path.dirname(os.path.abspath(__file__))
TRIP_DIR = os.path.join(HERE, "trips")

HELMET = "riderguard/location"
GLOVES = {"left": "riderguard/leftGloveMPU6050", "right": "riderguard/rightGloveMPU6050"}
NODE_TOPICS = {"Helmet": HELMET, "Left glove": GLOVES["left"], "Right glove": GLOVES["right"]}
CMD_PREFIX = "riderguard/cmd/"
CMD_SOURCE = {"helmet": HELMET, "leftGlove": GLOVES["left"], "rightGlove": GLOVES["right"]}
NODE_NAMES = {"helmet": "helmet", "leftGlove": "left glove", "rightGlove": "right glove"}
TOPIC_NODE = {GLOVES["left"]: "left glove", GLOVES["right"]: "right glove"}
LABEL_TOPIC = "riderguard/label"

WINDOW_S = 300
OFFLINE_AFTER_S = 5
HAZARD_HOLD_S = 3
CHART_S = 30
OBJECT_WARN_M = 2
FRONT_ANGLE_DEG = 0
FRONT_HALF_WIDTH_DEG = 45
MAX_SPEED_KMPH = 120
MAX_RESPONSE_MS = 5000
LATENCY_TARGET_MS = 200
SPEED_LIMIT_KMPH = 18
FOLLOW_GAP_S = 2
MOVING_KMPH = 3
MIN_MOVE_M = 3
EVENT_GAP_S = 3
PENALTY = {"Hardbrake": 8, "Unsafe following distance": 6, "Near object": 6, "Speeding": 6, "Abrupt turn": 5}
DEFAULT_PENALTY = 5
SCORE_PER_S = 600
ALERT_TOPICS = re.compile("^riderguard/(cmd/|location$)")  # lane-split alerts removed: glove distance is not used

SEVERITY = {"safe": 0, "uneven_road": 0, "abrupt_turn": 1, "speeding": 1, "hardbrake": 2, "unsafe_following_distance": 2}
LABEL_NAMES = {"safe": "Safe riding", "abrupt_turn": "Abrupt turn", "hardbrake": "Hardbrake", "speeding": "Speeding",
               "unsafe_following_distance": "Unsafe following distance", "uneven_road": "Uneven road"}
LABEL_ALIASES = {"uneven": "uneven_road", "hard_break": "hardbrake", "hard_brake": "hardbrake",
                 "abrupt_left_turn": "abrupt_turn", "abrupt_right_turn": "abrupt_turn"}
NO_ALERT = {"safe", "uneven_road"}
LABEL_KIND = {"speeding": "speed", "unsafe_following_distance": "follow"}

# Shared between the MQTT thread (writes) and Flask requests (read), guarded by lock
lock = threading.Lock()
trip = {"id": None, "start": None, "file": None, "docs": []}
broker = {"host": "localhost", "client": None, "connected": False}
predictor = None
model_labels = None


# ---------------- MQTT (runs only while a trip is active) ----------------

def on_connect(client, userdata, flags, rc):
    print("Connected with result code: " + str(rc))
    broker["connected"] = rc == 0
    client.subscribe("riderguard/#")


def on_disconnect(client, userdata, rc):
    broker["connected"] = False


def on_message(client, userdata, message):
    if message.topic == LABEL_TOPIC:
        return
    payload = message.payload.decode("utf-8", errors="replace")
    try:
        data = json.loads(payload)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        data = {"raw": payload}
    now = time.time()
    doc = {"topic": message.topic, "received_at": now, "data": data}
    with lock:
        if trip["id"] is None:
            return
        trip["docs"].append(doc)
        trip["file"].write(json.dumps(doc) + "\n")
    if predictor is not None:
        for topic, command in predictor.add(message.topic, data, now):
            client.publish(topic, json.dumps(command))  # comes back on riderguard/# and is recorded like a reading


def start_mqtt():
    client = make_client()
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.connect_async(broker["host"], 1883, 60)  # keeps retrying in the background until mosquitto is up
    client.loop_start()
    broker["client"] = client


def stop_mqtt():
    client = broker["client"]
    if client is not None:
        client.loop_stop()
        client.disconnect()
    broker.update(client=None, connected=False)


# ---------------- Helpers (unchanged from the Mongo version) ----------------

def num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def data_of(doc):
    return doc["data"] if doc and isinstance(doc.get("data"), dict) else {}


def base_label(label):
    if label is None:
        return None
    key = re.sub(r"_\d+$", "", str(label).strip().lower())
    key = re.sub(r"^\d+", "", key).strip().replace(" ", "_")
    return LABEL_ALIASES.get(key, key)


def label_name(label):
    return LABEL_NAMES.get(label, str(label).replace("_", " ").capitalize())


def follow_gap(speed, front_m):
    if speed is None or front_m is None or speed < MOVING_KMPH:
        return None
    gap = front_m / (speed / 3.6)
    return gap if gap < FOLLOW_GAP_S else None


def front_distance_m(d):
    distance_cm, angle = num(d.get("nearest_distance_cm")), num(d.get("nearest_angle_deg"))
    if distance_cm is None or distance_cm <= 0 or angle is None or angle_gap(angle, FRONT_ANGLE_DEG) > FRONT_HALF_WIDTH_DEG:
        return None
    return distance_cm / 100


def clock(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def angle_gap(a, b):
    return abs((a - b + 180) % 360 - 180)


def metres(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(h))


def bearing(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return round(math.degrees(math.atan2(y, x)) % 360)


def ride_summary(helmet_docs, now):
    track, track_t, speeds, distance, heading = [], [], [], 0.0, None
    for doc in helmet_docs:
        d = data_of(doc)
        speed = num(d.get("speed_kmph"))
        if speed is not None:
            speeds.append([round(doc["received_at"] - now, 1), round(speed, 1)])
        lat, lon = num(d.get("latitude")), num(d.get("longitude"))
        if lat is None or lon is None or d.get("location_valid") is False:
            continue
        point = (lat, lon)
        if track:
            step = metres(track[-1], point)
            if step < MIN_MOVE_M:
                continue
            distance += step
            heading = bearing(track[-1], point)
        track.append(point)
        track_t.append(doc["received_at"])
    altitude = next((a for a in (num(data_of(d).get("altitude_m")) for d in reversed(helmet_docs)) if a is not None), None)
    return {"track": [list(p) for p in track], "track_t": track_t, "speeds": speeds, "distance_km": round(distance / 1000, 2),
            "heading": heading, "altitude": None if altitude is None else round(altitude),
            "top_speed": max((s for _, s in speeds), default=None)}


def alert_kinds(doc):
    d = data_of(doc)
    if doc["topic"] == HELMET:
        speed, kinds = num(d.get("speed_kmph")), []
        if speed is not None and speed > SPEED_LIMIT_KMPH:
            kinds.append(("speed", "Speeding"))
        if follow_gap(speed, front_distance_m(d)) is not None:
            kinds.append(("follow", "Unsafe following distance"))
        return kinds
    label = base_label(d.get("label"))
    if label is not None:
        return [] if label in NO_ALERT else [(LABEL_KIND.get(label, "riding"), label_name(label))]
    return [("object", "Near object")] if d.get("action") == "vibrate" else []


def group_events(docs):
    """Alert-worthy readings -> merged events, newest first."""
    events, open_events, last_follow = [], {}, None
    for doc in docs:
        if not ALERT_TOPICS.match(doc["topic"]):
            continue
        t = doc["received_at"]
        for kind in alert_kinds(doc):
            if kind[0] == "follow":
                last_follow = t
            elif kind[0] == "object" and last_follow is not None and t - last_follow <= EVENT_GAP_S:
                continue
            add_to_event(events, open_events, kind, doc)
    out = []
    for e in reversed(events):
        nodes = "both gloves" if {"left glove", "right glove"} <= e["nodes"] else " + ".join(sorted(e["nodes"]))
        out.append({"kind": e["kind"], "title": e["title"], "text": f"{e['title']} · {nodes}" if nodes else e["title"],
                    "at": e["start"], "time": clock(e["start"]), "dur": round(e["end"] - e["start"], 1)})
    return out


def add_to_event(events, open_events, kind, doc):
    t = doc["received_at"]
    event = open_events.get(kind)
    if event is None or t - event["end"] > EVENT_GAP_S:
        event = {"kind": kind[0], "title": kind[1], "start": t, "end": t, "nodes": set()}
        events.append(event)
        open_events[kind] = event
    event["end"] = t
    if doc["topic"].startswith(CMD_PREFIX):
        node = doc["topic"][len(CMD_PREFIX):]
        event["nodes"].add(NODE_NAMES.get(node, node))
    elif doc["topic"] in TOPIC_NODE:
        event["nodes"].add(TOPIC_NODE[doc["topic"]])


def gyro_magnitude(d):
    axes = [num(d.get(a)) for a in ("gx", "gy", "gz")]
    return None if None in axes else round(math.sqrt(sum(v * v for v in axes)), 1)


def response_ms(cmd, by_topic):
    source = CMD_SOURCE.get(cmd["topic"][len(CMD_PREFIX):])
    seq = data_of(cmd).get("seq")
    for doc in reversed(by_topic.get(source, [])):
        if doc["received_at"] > cmd["received_at"]:
            continue
        if seq is not None and data_of(doc).get("seq") != seq:
            continue
        ms = round((cmd["received_at"] - doc["received_at"]) * 1000)
        return ms if ms <= MAX_RESPONSE_MS else None
    return None


# ---------------- Live state ----------------

def build_state(now):
    with lock:
        active, trip_id, start = trip["id"] is not None, trip["id"], trip["start"]
        all_docs = list(trip["docs"])
    docs = all_docs[bisect.bisect_left(all_docs, now - WINDOW_S, key=lambda d: d["received_at"]):]
    by_topic = {}
    for doc in docs:
        by_topic.setdefault(doc["topic"], []).append(doc)

    def fresh(topic, within=OFFLINE_AFTER_S):
        last = by_topic.get(topic, [None])[-1]
        return last if last and now - last["received_at"] <= within else None

    nodes = []
    for name, topic in NODE_TOPICS.items():
        last = by_topic.get(topic, [None])[-1]
        age = round(now - last["received_at"], 1) if last else None
        nodes.append({"name": name, "online": age is not None and age <= OFFLINE_AFTER_S, "age": age})
    ride_s = round(now - start) if active else 0

    helmet = data_of(fresh(HELMET))
    distance_cm, angle = num(helmet.get("nearest_distance_cm")), num(helmet.get("nearest_angle_deg"))
    nearest = None
    if distance_cm is not None and distance_cm > 0:
        in_front = angle is not None and angle_gap(angle, FRONT_ANGLE_DEG) <= FRONT_HALF_WIDTH_DEG
        nearest = {"m": round(distance_cm / 100, 2), "angle": angle, "in_front": in_front,
                   "close": in_front and distance_cm / 100 < OBJECT_WARN_M}
    front_m = nearest["m"] if nearest and nearest["in_front"] else None

    gloves = {}
    for side, topic in GLOVES.items():
        d = data_of(fresh(topic))
        gap = num(d.get("distance_cm"))
        gloves[side] = {"online": bool(d), "gap_cm": round(gap) if gap is not None and gap > 0 else None,
                        "alert": d.get("motor") == "ON" or d.get("led") == "ON",
                        "motion": [[round(doc["received_at"] - now, 1), g] for doc in by_topic.get(topic, [])
                                   if now - doc["received_at"] <= CHART_S
                                   and (g := gyro_magnitude(data_of(doc))) is not None]}

    cmds = [d for d in docs if d["topic"].startswith(CMD_PREFIX)]
    predictions = {}
    for cmd in cmds:
        label = base_label(data_of(cmd).get("label"))
        if label is not None and now - cmd["received_at"] <= OFFLINE_AFTER_S:
            predictions[cmd["topic"][len(CMD_PREFIX):]] = label
    riding = max(predictions.values(), key=lambda l: SEVERITY.get(l, 1), default=None)
    # Newest prediction per glove only (no hold), so the status drops back to safe as soon as the model does
    riding_recent = [label for label in predictions.values() if label not in NO_ALERT]
    helmet_warned = any(c["topic"] == CMD_PREFIX + "helmet" and data_of(c).get("action") == "vibrate"
                        and now - c["received_at"] <= HAZARD_HOLD_S for c in cmds)

    times = [ms for ms in (response_ms(c, by_topic) for c in cmds[-20:]) if ms is not None]
    events = group_events(all_docs)

    speed = num(helmet.get("speed_kmph"))
    speeding = speed is not None and speed > SPEED_LIMIT_KMPH
    gap_s = follow_gap(speed, front_m)

    if not active:
        status = ("idle", "Ready to ride", "Tap Start trip")
    elif not broker["connected"]:
        status = ("idle", "Starting server", "Connecting to the MQTT broker on this phone")
    elif not any(n["online"] for n in nodes):
        status = ("idle", "No live data", "Waiting for helmet and gloves")
    elif gap_s is not None:
        status = ("danger", "Unsafe following distance",
                  f"{front_m:.1f} m ahead · {gap_s:.1f} s gap · keep {FOLLOW_GAP_S} s")
    elif (nearest and nearest["close"]) or helmet_warned:
        status = ("danger", "Near object", f"{front_m:.1f} m in front · slow down" if front_m else "Slow down")
    elif riding_recent:
        worst = max(riding_recent, key=lambda l: SEVERITY.get(l, 1))
        status = ("warning", label_name(worst), "Ride smoothly and keep your distance")
    elif speeding:
        status = ("warning", "Speeding", f"{round(speed)} km/h · limit {SPEED_LIMIT_KMPH:g} km/h")
    else:
        status = ("safe", "All clear", "No hazards detected")

    ride = ride_summary([d for d in all_docs if d["topic"] == HELMET], now)
    ride["speeds"] = [p for p in ride["speeds"] if p[0] >= -WINDOW_S]
    ride.pop("track_t")

    lat, lon = num(helmet.get("latitude")), num(helmet.get("longitude"))
    return {
        "trip": {"active": active, "id": trip_id},
        "broker": broker["connected"],
        "model": model_labels,
        "status": {"level": status[0], "title": status[1], "detail": status[2]},
        "recording": None,
        "speed": None if speed is None else round(speed),
        "speed_pct": 0 if speed is None else min(speed, MAX_SPEED_KMPH) / MAX_SPEED_KMPH,
        "nearest": nearest,
        "lidar_status": helmet.get("lidar_status"),
        "riding": None if riding is None else {"key": riding, "name": label_name(riding),
                                               "level": "safe" if riding in NO_ALERT else "warning"},
        "predictions": {NODE_NAMES.get(node, node): label_name(label) for node, label in predictions.items()},
        "gloves": gloves,
        "speeding": speeding,
        "follow_gap_s": None if gap_s is None else round(gap_s, 1),
        "location": None if lat is None or lon is None else {
            "lat": lat, "lon": lon, "fix": helmet.get("gps_status") == "FIX", "satellites": helmet.get("satellites")},
        "gps_fix": helmet.get("gps_status") == "FIX",
        "satellites": helmet.get("satellites"),
        "nodes": nodes,
        "latency": {"last": times[-1] if times else None,
                    "avg": round(sum(times) / len(times)) if times else None,
                    "max": max(times) if times else None, "target": LATENCY_TARGET_MS},
        "events": events[:15],
        "alert_count": len(events),
        "ride_s": ride_s,
        "ride": ride,
        "thresholds": {"warn_m": OBJECT_WARN_M,
                       "follow_s": FOLLOW_GAP_S, "speed_limit": SPEED_LIMIT_KMPH, "front_deg": FRONT_ANGLE_DEG},
    }


# ---------------- Trips (files) and post-trip analysis ----------------

def trip_path(trip_id, ext=".jsonl"):
    return os.path.join(TRIP_DIR, os.path.basename(trip_id) + ext)


def load_trip(trip_id):
    docs = []
    with open(trip_path(trip_id)) as f:
        for line in f:
            try:
                docs.append(json.loads(line))
            except ValueError:
                pass  # half-written last line if the app was killed mid-trip
    return docs


def route_segments(track, track_t, events):
    """Split the GPS track into runs of safe / hazardous riding. A segment is hazardous when its time
    falls inside an event (padded by 1 s); the kind of the overlapping event decides its colour."""
    spans = [(e["at"] - 1, e["at"] + e["dur"] + 1, e["kind"]) for e in events]
    segments, safe_m, total_m = [], 0.0, 0.0
    for i in range(1, len(track)):
        t = (track_t[i - 1] + track_t[i]) / 2
        kind = next((k for a, b, k in spans if a <= t <= b), None)
        step = metres(track[i - 1], track[i])
        total_m += step
        safe_m += step if kind is None else 0
        if segments and segments[-1]["kind"] == kind:
            segments[-1]["points"].append(track[i])
        else:
            segments.append({"kind": kind, "points": [track[i - 1], track[i]]})
    return segments, (round(100 * safe_m / total_m) if total_m else None)


def build_summary(docs):
    if not docs:
        return None
    start, end = docs[0]["received_at"], docs[-1]["received_at"]
    ride = ride_summary([d for d in docs if d["topic"] == HELMET], end)
    events = group_events(docs)

    counts = {}
    for e in events:
        counts[e["title"]] = counts.get(e["title"], 0) + 1
    duration = round(end - start)
    penalty = sum(PENALTY.get(title, DEFAULT_PENALTY) * n for title, n in counts.items())
    score = max(0, round(100 - penalty / max(duration / SCORE_PER_S, 1)))

    labels = [base_label(data_of(d).get("label")) for d in docs
              if d["topic"].startswith(CMD_PREFIX) and data_of(d).get("label")]
    smooth = round(100 * sum(l in NO_ALERT for l in labels) / len(labels)) if labels else None

    spots = []
    for e in events:
        if ride["track_t"]:
            i = min(range(len(ride["track_t"])), key=lambda k: abs(ride["track_t"][k] - e["at"]))
            spots.append({"kind": e["kind"], "point": ride["track"][i]})
    segments, safe_route_pct = route_segments(ride["track"], ride["track_t"], events)

    speeds = [v for _, v in ride["speeds"]]
    return {
        "start": start,
        "when": datetime.fromtimestamp(start).strftime("%d %b %Y, %H:%M"),
        "score": score,
        "grade": "Great ride" if score >= 85 else "Good ride" if score >= 65 else "Needs work",
        "duration_s": duration,
        "distance_km": ride["distance_km"],
        "top_speed": ride["top_speed"],
        "avg_speed": round(sum(speeds) / len(speeds), 1) if speeds else None,
        "smooth_pct": smooth,
        "safe_route_pct": safe_route_pct,
        "counts": [{"title": t, "count": n, "points": PENALTY.get(t, DEFAULT_PENALTY)} for t, n in
                   sorted(counts.items(), key=lambda kv: -kv[1])],
        "track": ride["track"],
        "segments": segments,
        "spots": spots,
        "events": list(reversed(events)),  # oldest first, for the timeline
        "penalties": PENALTY,
    }


def trip_meta(trip_id):
    """Short summary for the trip list, cached in trips/<id>.json once the trip has ended."""
    path = trip_path(trip_id, ".json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    s = build_summary(load_trip(trip_id))
    if s is None:
        return None
    meta = {"id": trip_id, "when": s["when"], "duration_s": s["duration_s"], "distance_km": s["distance_km"],
            "score": s["score"], "grade": s["grade"], "alerts": sum(c["count"] for c in s["counts"])}
    if trip_id != trip["id"]:
        with open(path, "w") as f:
            json.dump(meta, f)
    return meta


# ---------------- Routes ----------------

@app.route("/", methods=["GET"])
def index():
    return render_template("rider.html"), 200


@app.route("/api/state", methods=["GET"])
def state():
    return jsonify(build_state(time.time())), 200


@app.route("/api/trip/start", methods=["POST"])
def trip_start():
    with lock:
        if trip["id"] is None:
            now = time.time()
            trip_id = datetime.fromtimestamp(now).strftime("%Y-%m-%d_%H-%M-%S")
            os.makedirs(TRIP_DIR, exist_ok=True)
            trip.update(id=trip_id, start=now, docs=[], file=open(trip_path(trip_id), "a", buffering=1))
            if predictor is not None:
                predictor.reset()
            print(f"Trip {trip_id} started")
    if broker["client"] is None:
        start_mqtt()
    return jsonify({"id": trip["id"]}), 200


@app.route("/api/trip/end", methods=["POST"])
def trip_end():
    discard = request.args.get("discard") == "1"
    with lock:
        trip_id, f = trip["id"], trip["file"]
        trip.update(id=None, start=None, file=None, docs=[])
    stop_mqtt()
    if trip_id is None:
        return jsonify({"id": None}), 200
    f.close()
    if discard or os.path.getsize(trip_path(trip_id)) == 0:
        os.remove(trip_path(trip_id))
        print(f"Trip {trip_id} discarded")
        return jsonify({"id": None}), 200
    trip_meta(trip_id)
    print(f"Trip {trip_id} saved")
    return jsonify({"id": trip_id}), 200


@app.route("/api/trips", methods=["GET"])
def trips():
    out = []
    for path in sorted(glob.glob(os.path.join(TRIP_DIR, "*.jsonl")), reverse=True):
        trip_id = os.path.basename(path)[:-len(".jsonl")]
        if trip_id == trip["id"]:
            continue
        meta = trip_meta(trip_id)
        if meta is not None:
            out.append(meta)
    return jsonify(out), 200


@app.route("/api/summary", methods=["GET"])
def summary():
    trip_id = request.args.get("trip")
    if trip_id is None:
        with lock:
            if trip["id"] is not None:
                return jsonify(build_summary(list(trip["docs"]))), 200
        files = sorted(glob.glob(os.path.join(TRIP_DIR, "*.jsonl")))
        if not files:
            return jsonify(None), 200
        trip_id = os.path.basename(files[-1])[:-len(".jsonl")]
    if not os.path.exists(trip_path(trip_id)):
        return jsonify({"error": "no such trip"}), 404
    return jsonify(build_summary(load_trip(trip_id))), 200


def main():
    global FRONT_ANGLE_DEG, SPEED_LIMIT_KMPH, predictor, model_labels
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=os.path.join(HERE, "model.json"),
                        help="model.json from export_model.py (phone) or model.joblib (laptop with scikit-learn)")
    parser.add_argument("--host", default="localhost", help="MQTT broker (mosquitto on this phone)")
    parser.add_argument("--front-angle", type=float, default=FRONT_ANGLE_DEG)
    parser.add_argument("--speed-limit", type=float, default=SPEED_LIMIT_KMPH)
    args = parser.parse_args()
    FRONT_ANGLE_DEG, SPEED_LIMIT_KMPH = args.front_angle, args.speed_limit
    broker["host"] = args.host

    if os.path.exists(args.model):
        bundle = load_bundle(args.model)
        predictor, model_labels = Predictor(bundle), bundle["labels"]
        print(f"Loaded {args.model}: labels {bundle['labels']}, window {bundle['window']} s, step {bundle['step']} s")
    else:
        print(f"No model at {args.model}: running without live predictions")
    print(f"LiDAR front angle {FRONT_ANGLE_DEG} deg, speed limit {SPEED_LIMIT_KMPH} km/h")
    app.run(host="0.0.0.0", port=3238)


if __name__ == "__main__":
    main()
