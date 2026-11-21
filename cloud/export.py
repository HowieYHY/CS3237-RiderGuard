# RiderGuard export: dumps readings stored by `server.py --mongo` to one CSV per topic, for feature extraction/training
#   python3 export.py                      -> exports/<topic>.csv, all readings
#   python3 export.py --labelled           -> only readings recorded while a label was set
#   python3 export.py --label abrupt_turn  -> only that label
import argparse
import os

import pandas as pd
from pymongo import MongoClient

col = MongoClient("mongodb://localhost:27017/")["riderguard"]["readings"]


def export(out_dir, query):
    os.makedirs(out_dir, exist_ok=True)
    for topic in sorted(col.distinct("topic", query)):
        docs = list(col.find({**query, "topic": topic}).sort("received_at", 1))

        rows = [{"received_at": d["received_at"], "label": d.get("label"), **d["data"]} for d in docs]

        path = os.path.join(out_dir, topic.split("/")[-1] + ".csv")
        pd.DataFrame(rows).to_csv(path, index=False)
        print(f"{path}: {len(docs)} rows")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="exports")
    parser.add_argument("--labelled", action="store_true")
    parser.add_argument("--label")
    args = parser.parse_args()

    query = {"label": args.label} if args.label else {"label": {"$ne": None}} if args.labelled else {}
    export(args.out, query)
