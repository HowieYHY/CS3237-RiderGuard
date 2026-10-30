# RiderGuard dashboard: Flask view of readings stored by `server.py --mongo` (Lecture 7 testsite.py)
#   pip install flask pymongo
#   python3 dashboard.py  -> http://localhost:3237
import json
from datetime import datetime

from flask import Flask, render_template
from pymongo import DESCENDING, MongoClient

app = Flask(__name__)


def fmt(doc):
    return {
        "topic": doc["topic"],
        "time": datetime.fromtimestamp(doc["received_at"]).strftime("%H:%M:%S"),
        "label": doc.get("label") or "",
        "data": json.dumps(doc["data"]),
    }


@app.route("/", methods=["GET"])
def index():
    topics = []
    for topic in sorted(col.distinct("topic")):
        latest = col.find_one({"topic": topic}, sort=[("received_at", DESCENDING)])
        topics.append({"count": col.count_documents({"topic": topic}), **fmt(latest)})

    recent = [fmt(d) for d in col.find().sort("received_at", DESCENDING).limit(50)]
    return render_template("index.html", topics=topics, recent=recent), 200


def main():
    global client, db, col, app
    client = MongoClient("mongodb://localhost:27017/")
    db = client["riderguard"]
    col = db["readings"]
    app.run(host="0.0.0.0", port=3237)


if __name__ == "__main__":
    main()
