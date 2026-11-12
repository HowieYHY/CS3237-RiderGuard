import argparse
import math
import re
import time
from datetime import datetime

from flask import Flask, jsonify, render_template
from pymongo import MongoClient
from pymongo.errors import PyMongoError

app = Flask(__name__)
col = None

HELMET = "riderguard/location"
GLOVES = {"left": "riderguard/leftGloveMPU6050", "right": "riderguard/rightGloveMPU6050"}
NODE_TOPICS = {"Helmet": HELMET, "Left glove": GLOVES["left"], "Right glove": GLOVES["right"]}
CMD_PREFIX = "riderguard/cmd/"
CMD_SOURCE = {"helmet": HELMET, "leftGlove": GLOVES["left"], "rightGlove": GLOVES["right"]}
NODE_NAMES = {"helmet": "helmet", "leftGlove": "left glove", "rightGlove": "right glove"}
TOPIC_NODE = {GLOVES["left"]: "left glove", GLOVES["right"]: "right glove"}
LANE_TOPIC = "riderguard/laneCheck"
FEEDBACK_TOPIC = "riderguard/feedback"

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
SIDE_DANGER_CM = 30
SIDE_CAUTION_CM = 60
SPEED_LIMIT_KMPH = 18
FOLLOW_GAP_S = 2
MOVING_KMPH = 3
SESSION_GAP_S = 60
MAX_RIDE_S = 3 * 3600
MIN_MOVE_M = 3
EVENT_GAP_S = 3
PENALTY = {"Hardbrake": 8, "Unsafe following distance": 6, "Near object": 6, "Speeding": 6, "Abrupt turn": 5,
           "Unsafe lane splitting": 4}
DEFAULT_PENALTY = 5
SCORE_PER_S = 600
ALERT_TOPICS = "^riderguard/(cmd/|feedback$|location$|leftGloveMPU6050$|rightGloveMPU6050$)"

session = {"start": None, "last": None}

SEVERITY = {"safe": 0, "uneven_road": 0, "abrupt_turn": 1, "speeding": 1, "hardbrake": 2, "unsafe_following_distance": 2}
LABEL_NAMES = {"safe": "Safe riding", "abrupt_turn": "Abrupt turn", "hardbrake": "Hardbrake", "speeding": "Speeding",
               "unsafe_following_distance": "Unsafe following distance", "uneven_road": "Uneven road"}
LABEL_ALIASES = {"uneven": "uneven_road", "hard_break": "hardbrake", "hard_brake": "hardbrake",
                 "abrupt_left_turn": "abrupt_turn", "abrupt_right_turn": "abrupt_turn"}
NO_ALERT = {"safe", "uneven_road"}
LABEL_KIND = {"speeding": "speed", "unsafe_following_distance": "follow"}


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


def find_ride_start(now):
    last = None
    for doc in col.find({"received_at": {"$gte": now - MAX_RIDE_S, "$lte": now}}, {"received_at": 1}).sort("received_at", -1):
        t = doc["received_at"]
        if last is not None and last - t > SESSION_GAP_S:
            return last
        last = t
    return last


def alert_kinds(doc):
    d = data_of(doc)
    if doc["topic"] in TOPIC_NODE:
        gap = num(d.get("distance_cm"))
        return [("lane", "Unsafe lane splitting")] if gap is not None and 0 < gap < SIDE_DANGER_CM else []
    if doc["topic"] == HELMET:
        speed, kinds = num(d.get("speed_kmph")), []
        if speed is not None and speed > SPEED_LIMIT_KMPH:
            kinds.append(("speed", "Speeding"))
        if follow_gap(speed, front_distance_m(d)) is not None:
            kinds.append(("follow", "Unsafe following distance"))
        return kinds
    if doc["topic"] == FEEDBACK_TOPIC:
        return [("lane", "Unsafe lane splitting")] if d.get("result") == "NOT_OK" else []
    label = base_label(d.get("label"))
    if label is not None:
        return [] if label in NO_ALERT else [(LABEL_KIND.get(label, "riding"), label_name(label))]
    return [("object", "Near object")] if d.get("action") == "vibrate" else []


def group_events(docs):
    events, open_events, last_follow = [], {}, None
    for doc in docs:
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


def live_ride(now):
    start = session["start"] or now - WINDOW_S
    ride = ride_summary(list(col.find({"topic": HELMET, "received_at": {"$gte": start}}).sort("received_at", 1)), now)
    ride["speeds"] = [p for p in ride["speeds"] if p[0] >= -WINDOW_S]
    ride.pop("track_t")
    return ride


def build_state(now):
    docs = list(col.find({"received_at": {"$gte": now - WINDOW_S}}).sort("received_at", 1))
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
    if any(n["online"] for n in nodes):
        if session["last"] is None:
            session["start"] = find_ride_start(now) or now
        elif now - session["last"] > SESSION_GAP_S:
            session["start"] = now
        session["last"] = now
    ride_s = round(session["last"] - session["start"]) if session["start"] is not None else 0

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
    riding_recent = [label for c in cmds if now - c["received_at"] <= HAZARD_HOLD_S
                     and (label := base_label(data_of(c).get("label"))) is not None and label not in NO_ALERT]
    helmet_warned = any(c["topic"] == CMD_PREFIX + "helmet" and data_of(c).get("action") == "vibrate"
                        and now - c["received_at"] <= HAZARD_HOLD_S for c in cmds)

    lane = data_of(fresh(LANE_TOPIC))
    feedback = fresh(FEEDBACK_TOPIC, HAZARD_HOLD_S)
    lane_result = data_of(feedback).get("result")

    times = [ms for ms in (response_ms(c, by_topic) for c in cmds[-20:]) if ms is not None]

    readings = [d for d in docs if not d["topic"].startswith(CMD_PREFIX) and now - d["received_at"] <= OFFLINE_AFTER_S]
    recording = readings[-1].get("label") if readings else None

    since = session["start"] or now - WINDOW_S
    events = group_events(col.find({"received_at": {"$gte": since}, "topic": {"$regex": ALERT_TOPICS}}).sort("received_at", 1))

    lane_left = num(lane.get("left_cm")) if lane else gloves["left"]["gap_cm"]
    lane_right = num(lane.get("right_cm")) if lane else gloves["right"]["gap_cm"]
    if lane_result:
        lane_verdict = "ok" if lane_result == "OK_TO_SPLIT" else "tight"
    elif any(v is not None and v < SIDE_DANGER_CM for v in (lane_left, lane_right)):
        lane_verdict = "tight"
    elif lane_left is not None and lane_right is not None:
        lane_verdict = "ok"
    else:
        lane_verdict = None
    speed = num(helmet.get("speed_kmph"))
    speeding = speed is not None and speed > SPEED_LIMIT_KMPH
    gap_s = follow_gap(speed, front_m)

    if not any(n["online"] for n in nodes):
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
    elif lane_verdict == "tight":
        status = ("warning", "Unsafe lane splitting", "Not enough space to lane-split")
    else:
        status = ("safe", "All clear", "No hazards detected")

    lat, lon = num(helmet.get("latitude")), num(helmet.get("longitude"))
    return {
        "status": {"level": status[0], "title": status[1], "detail": status[2]},
        "recording": recording,
        "speed": None if speed is None else round(speed),
        "speed_pct": 0 if speed is None else min(speed, MAX_SPEED_KMPH) / MAX_SPEED_KMPH,
        "nearest": nearest,
        "lidar_status": helmet.get("lidar_status"),
        "riding": None if riding is None else {"key": riding, "name": label_name(riding),
                                               "level": "safe" if riding in NO_ALERT else "warning"},
        "predictions": {NODE_NAMES.get(node, node): label_name(label) for node, label in predictions.items()},
        "gloves": gloves,
        "lane": {"left": lane_left, "right": lane_right, "result": lane_result, "verdict": lane_verdict},
        "speeding": speeding,
        "follow_gap_s": None if gap_s is None else round(gap_s, 1),
        "location": None if lat is None or lon is None else {
            "lat": lat, "lon": lon, "fix": helmet.get("gps_status") == "FIX", "satellites": helmet.get("satellites")},
        "nodes": nodes,
        "latency": {"last": times[-1] if times else None,
                    "avg": round(sum(times) / len(times)) if times else None,
                    "max": max(times) if times else None, "target": LATENCY_TARGET_MS},
        "events": events[:15],
        "alert_count": len(events),
        "ride_s": ride_s,
        "ride": live_ride(now),
        "thresholds": {"side_danger": SIDE_DANGER_CM, "side_caution": SIDE_CAUTION_CM, "warn_m": OBJECT_WARN_M,
                       "follow_s": FOLLOW_GAP_S,
                       "speed_limit": SPEED_LIMIT_KMPH, "front_deg": FRONT_ANGLE_DEG},
    }


def last_ride():
    if session["start"] is not None:
        return session["start"], session["last"]
    latest = col.find_one({}, {"received_at": 1}, sort=[("received_at", -1)])
    if latest is None:
        return None, None
    return find_ride_start(latest["received_at"]), latest["received_at"]


def build_summary(now):
    start, end = last_ride()
    start, end = (now - WINDOW_S, now) if start is None else (start, end)
    docs = list(col.find({"received_at": {"$gte": start, "$lte": end + 1},
                          "topic": {"$regex": ALERT_TOPICS}}).sort("received_at", 1))
    helmet = [d for d in docs if d["topic"] == HELMET]
    ride = ride_summary(helmet, now)
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

    speeds = [v for _, v in ride["speeds"]]
    return {
        "score": score,
        "grade": "Great ride" if score >= 85 else "Good ride" if score >= 65 else "Needs work",
        "duration_s": duration,
        "distance_km": ride["distance_km"],
        "top_speed": ride["top_speed"],
        "avg_speed": round(sum(speeds) / len(speeds), 1) if speeds else None,
        "smooth_pct": smooth,
        "counts": [{"title": t, "count": n, "points": PENALTY.get(t, DEFAULT_PENALTY)} for t, n in
                   sorted(counts.items(), key=lambda kv: -kv[1])],
        "track": ride["track"],
        "spots": spots,
        "penalties": PENALTY,
    }


@app.route("/", methods=["GET"])
def index():
    return render_template("rider.html", warn_m=OBJECT_WARN_M, front_deg=FRONT_ANGLE_DEG,
                           front_width=FRONT_HALF_WIDTH_DEG), 200


@app.route("/api/state", methods=["GET"])
def state():
    try:
        return jsonify(build_state(time.time())), 200
    except PyMongoError:
        return jsonify({"error": "database"}), 503


@app.route("/api/summary", methods=["GET"])
def summary():
    try:
        return jsonify(build_summary(time.time())), 200
    except PyMongoError:
        return jsonify({"error": "database"}), 503


def main():
    global col, FRONT_ANGLE_DEG, SPEED_LIMIT_KMPH
    parser = argparse.ArgumentParser()
    parser.add_argument("--front-angle", type=float, default=FRONT_ANGLE_DEG)
    parser.add_argument("--speed-limit", type=float, default=SPEED_LIMIT_KMPH)
    args = parser.parse_args()
    FRONT_ANGLE_DEG, SPEED_LIMIT_KMPH = args.front_angle, args.speed_limit
    print(f"LiDAR front angle {FRONT_ANGLE_DEG} deg, speed limit {SPEED_LIMIT_KMPH} km/h")
    col = MongoClient("mongodb://localhost:27017/", serverSelectionTimeoutMS=1500)["riderguard"]["readings"]
    app.run(host="0.0.0.0", port=3238)


if __name__ == "__main__":
    main()
