# RiderGuard latency report: summarises round-trip times the ESP32s published to riderguard/latency
# (stored by server.py --mongo). Each sample is ESP32 send -> cloud reply -> ESP32, timed on the ESP32's millis().
#   python latency_report.py                   -> all nodes, all samples
#   python latency_report.py --node leftGlove  -> one node
#   python latency_report.py --minutes 10      -> only the last 10 minutes
#   python latency_report.py --plot            -> also save latency_<node>.png (histogram)
# Node-to-node (e.g. helmet detects -> glove vibrates) can't be timed directly: ESP32 clocks aren't synced.
# It is estimated as RTT_A/2 + RTT_B/2 (medians), assuming uplink ~= downlink; server time is inside both RTTs.
import argparse
import itertools
import statistics
import time

from pymongo import MongoClient

col = MongoClient("mongodb://localhost:27017/")["riderguard"]["readings"]


def report(node, since):
    docs = list(col.find({"topic": "riderguard/latency", "data.node": node, "received_at": {"$gte": since}})
                .sort("received_at", 1))
    if not docs:
        print(f"{node}: no samples")
        return None
    rtt = sorted(d["data"]["rtt_ms"] for d in docs)
    t0, t1 = docs[0]["received_at"], docs[-1]["received_at"]
    pct = lambda p: rtt[min(len(rtt) - 1, int(p * len(rtt)))]

    print(f"\n{node}: {len(rtt)} samples over {(t1 - t0) / 60:.1f} min "
          f"({time.strftime('%d/%m %H:%M', time.localtime(t0))} - {time.strftime('%H:%M', time.localtime(t1))})")
    print(f"  median {statistics.median(rtt):.0f} ms | mean {statistics.mean(rtt):.1f} ms "
          f"(SD {statistics.pstdev(rtt):.1f}) | min {rtt[0]} | max {rtt[-1]}")
    print(f"  p90 {pct(.9)} | p95 {pct(.95)} | p99 {pct(.99)} ms")
    print("  " + " | ".join(f"<{b} ms: {100 * sum(r < b for r in rtt) / len(rtt):.0f}%" for b in (100, 200, 500)))
    return rtt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--node")
    parser.add_argument("--minutes", type=float)
    parser.add_argument("--plot", action="store_true")
    args = parser.parse_args()

    since = time.time() - args.minutes * 60 if args.minutes else 0
    nodes = [args.node] if args.node else sorted(col.distinct("data.node", {"topic": "riderguard/latency"}))
    medians = {}
    for node in nodes:
        rtt = report(node, since)
        if rtt:
            medians[node] = statistics.median(rtt)
        if args.plot and rtt:
            import matplotlib.pyplot as plt
            plt.figure()
            plt.hist(rtt, bins=40)
            plt.axvline(statistics.median(rtt), color="k", linestyle="--", label=f"median {statistics.median(rtt):.0f} ms")
            plt.xlabel("Round-trip latency (ms)")
            plt.ylabel("Samples")
            plt.title(f"RiderGuard {node} round-trip latency (n={len(rtt)})")
            plt.legend()
            plt.savefig(f"latency_{node}.png", dpi=150, bbox_inches="tight")
            print(f"  saved latency_{node}.png")

    if len(medians) >= 2:
        print("\nEstimated node -> cloud -> node (one way, median RTT_A/2 + RTT_B/2, assumes uplink ~= downlink):")
        for a, b in itertools.combinations(sorted(medians), 2):
            print(f"  {a} <-> {b}: ~{medians[a] / 2 + medians[b] / 2:.0f} ms")
