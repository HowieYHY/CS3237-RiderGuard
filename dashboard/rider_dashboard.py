# RiderGuard rider dashboard: rider-facing view of readings stored by `cloud/server.py --mongo`
# Separate app from cloud/dashboard.py (team view), so both run side by side and never touch each other's files.
#   pip install flask pymongo
#   python3 rider_dashboard.py  -> http://localhost:3238
# Topic/payload spec: cloud/TOPICS.md
import time
from datetime import datetime

from flask import Flask, render_template
from pymongo import DESCENDING, MongoClient

app = Flask(__name__)
col = None  # set in main()

HAZARD_WINDOW_S = 3   # a warning stays on screen this long
OFFLINE_AFTER_S = 5   # node shown as offline after this long without data
MAX_SPEED_KMPH = 120  # full scale of the speed gauge
LIDAR_MAX_M = 8       # full scale of the distance bar
OBJECT_WARN_M = 2     # matches latency_tools/decision.py OBJECT_THRESHOLD_CM
LATENCY_TARGET_MS = 200

NODES = {"Helmet": "riderguard/helmet", "Left glove": "riderguard/leftGlove", "Right glove": "riderguard/rightGlove"}
# command node -> sensor topic whose reading triggered it (for matching seq)
CMD_SOURCE = {"helmet": "riderguard/helmetLIDAR", "leftGlove": "riderguard/leftGloveMPU6050",
              "rightGlove": "riderguard/rightGloveMPU6050"}


def latest(query):
    return col.find_one(query, sort=[("received_at", DESCENDING)])


def data_of(doc):
    return doc["data"] if doc and isinstance(doc.get("data"), dict) else {}


def clock(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def response_ms(cmd):
    """Server-side time from the triggering reading arriving to the warning going out."""
    seq = data_of(cmd).get("seq")
    source = CMD_SOURCE.get(cmd["topic"].rsplit("/", 1)[-1])
    if seq is None or source is None:
        return None
    reading = latest({"topic": source, "data.seq": seq, "received_at": {"$lte": cmd["received_at"]}})
    return round((cmd["received_at"] - reading["received_at"]) * 1000) if reading else None


def build_state():
    now = time.time()

    nodes = []
    for name, prefix in NODES.items():
        doc = latest({"topic": {"$regex": "^" + prefix}})
        age = now - doc["received_at"] if doc else None
        nodes.append({"name": name, "online": age is not None and age <= OFFLINE_AFTER_S,
                      "age": None if age is None else round(age, 1)})

    gps = data_of(latest({"topic": "riderguard/helmetGPS"}))
    speed = gps.get("speed_kmph")

    lidar = data_of(latest({"topic": "riderguard/helmetLIDAR"}))
    distance_m = None
    if lidar.get("scan_valid", True) and isinstance(lidar.get("distance_cm"), (int, float)) and lidar["distance_cm"] > 0:
        distance_m = lidar["distance_cm"] / 100

    lane = data_of(latest({"topic": "riderguard/laneCheck"}))
    feedback_doc = latest({"topic": "riderguard/feedback"})
    lane_result = data_of(feedback_doc).get("result")

    cmds = list(col.find({"topic": {"$regex": "^riderguard/cmd/"}}).sort("received_at", DESCENDING).limit(20))
    times = [ms for ms in (response_ms(c) for c in cmds) if ms is not None]
    warnings = [{"time": clock(c["received_at"]), "node": c["topic"].rsplit("/", 1)[-1],
                 "action": data_of(c).get("action", "?"), "ms": response_ms(c)} for c in cmds[:5]]

    if not any(n["online"] for n in nodes):
        status = ("neutral", "No live data", "Waiting for helmet and gloves")
    elif cmds and now - cmds[0]["received_at"] <= HAZARD_WINDOW_S:
        ahead = f"{distance_m:.1f} m in front · slow down" if distance_m is not None else "Slow down"
        status = ("danger", "Object ahead", ahead)
    elif lane_result == "NOT_OK" and now - feedback_doc["received_at"] <= HAZARD_WINDOW_S:
        status = ("warning", "Gap too tight", "Not enough space to lane-split")
    else:
        status = ("success", "All clear", "No hazards detected")

    return {
        "status": status,
        "speed": None if speed is None else round(speed),
        "speed_pct": 0 if speed is None else min(speed, MAX_SPEED_KMPH) / MAX_SPEED_KMPH,
        "distance_m": distance_m,
        "distance_pct": 0 if distance_m is None else min(distance_m, LIDAR_MAX_M) / LIDAR_MAX_M,
        "object_close": distance_m is not None and distance_m < OBJECT_WARN_M,
        "lidar_max_m": LIDAR_MAX_M, "object_warn_m": OBJECT_WARN_M,
        "lane_left": lane.get("left_cm"), "lane_right": lane.get("right_cm"), "lane_result": lane_result,
        "lat": gps.get("latitude"), "lon": gps.get("longitude"),
        "bpm": data_of(latest({"topic": "riderguard/leftGloveMPU6050"})).get("bpm"),
        "nodes": nodes,
        "latency_last": times[0] if times else None,
        "latency_avg": round(sum(times) / len(times)) if times else None,
        "latency_max": max(times) if times else None,
        "latency_target": LATENCY_TARGET_MS,
        "warnings": warnings,
    }


@app.route("/", methods=["GET"])
def index():
    return render_template("rider.html", **build_state()), 200


def main():
    global col
    col = MongoClient("mongodb://localhost:27017/")["riderguard"]["readings"]
    app.run(host="0.0.0.0", port=3238)


if __name__ == "__main__":
    main()
