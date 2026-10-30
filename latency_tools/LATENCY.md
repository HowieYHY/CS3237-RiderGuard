# RiderGuard latency test

Measures how long the system takes to answer a single sensor reading: from an ESP32 sending it, through the cloud, until the reply is back on the same ESP32.
System setup (network, broker, server, firmware) is in [DATA_COLLECTION.md](../DATA_COLLECTION.md).

## What is measured

**Round-trip time (RTT)** per message, per device:
```
ESP32 sends reading ─► Wi-Fi ─► Mosquitto ─► responder decides ─► Mosquitto ─► Wi-Fi ─► ESP32 receives reply
   t_ms = millis()                                                                    rtt_ms = millis() - t_ms
```

| Device | Responder | Decision in the loop | Replies |
|---|---|---|---|
| Left glove | `ml/predict.py --model left.joblib` | window features + SVM (`ml/features.py`) | every reading |
| Right glove | `ml/predict.py --model right.joblib --topic riderguard/rightGloveMPU6050` | window features + SVM | every reading |
| Helmet | `latency_tools/latency_server.py` | nearest LiDAR point < 200 cm (`decision.py`) | only when hazard |

Included: ESP32 MQTT send, Wi-Fi both ways, broker, responder processing.
Not included: reading the sensors (before `t_ms`), the ML window fill time, acting on the reply (motor), MongoDB storage (`server.py` runs in parallel).

## How it is calculated (no clock sync needed)

Both timestamps come from the **same ESP32 clock**, so device and laptop clocks never need to agree.

1. Each reading carries a counter and the send time: `{"seq": 42, "t_ms": 81157, ...}`.
2. The responder copies them into its reply on `riderguard/cmd/<node>`: `{"action": "vibrate"|"none", "label": ..., "seq": 42, "t_ms": 81157}`.
3. The ESP32's MQTT callback, on arrival, computes `rtt_ms = millis() - t_ms`. It does this in the callback, not in `loop()`, so loop delays don't inflate the result.
4. The ESP32 prints `ROUND TRIP seq 42: 48 ms` to Serial and publishes `{"node": "helmet", "seq": 42, "rtt_ms": 48}` to `riderguard/latency`.
5. `cloud/server.py --mongo` stores these like any other message. `latency_report.py` summarises them.

`seq` starts at 0 when a device boots and goes up by 1 per reading. Each device has its own counter. A skipped `seq` = no reply for that reading (for the helmet: nothing within 200 cm).

### Node-to-node estimate

Devices never talk directly; everything goes through the broker. "Helmet detects → glove vibrates" can't be timed exactly because ESP32 clocks aren't synchronised, so `latency_report.py` estimates it as
```
one-way A → cloud → B  ≈  median RTT_A / 2  +  median RTT_B / 2
```
assuming uplink ≈ downlink. Server processing is already inside both RTTs.

## Running the test

Prerequisites: broker and `python cloud/server.py --mongo` running, all three devices on the latency firmware in `firmware/` (their readings in the server terminal contain `seq` and `t_ms`), and trained models (see DATA_COLLECTION.md section 6).

One terminal each, from the repo folder:
```
python ml/predict.py --model left.joblib
python ml/predict.py --model right.joblib --topic riderguard/rightGloveMPU6050
python latency_tools/latency_server.py
```
Watch results live:
```
wsl -d Ubuntu -- mosquitto_sub -t riderguard/latency -v
```
```
riderguard/latency {"node":"leftGlove","seq":749,"rtt_ms":48}
riderguard/latency {"node":"helmet","seq":81,"rtt_ms":20}
riderguard/latency {"node":"rightGlove","seq":93,"rtt_ms":51}
```
Run 2–3 minutes (wear the gloves, keep something within 2 m of the LiDAR), then Ctrl+C the three responders.

## Results

```
python latency_tools/latency_report.py --minutes 5 --plot
```
`--minutes N` = only the last N minutes (counted back from now); `--node leftGlove|rightGlove|helmet` = one device; `--plot` saves `latency_<node>.png` (histogram with median line).

Example output (real run, see below):
```
helmet: 633 samples over 10.5 min (30/10 16:21 - 16:32)
  median 27 ms | mean 85.7 ms (SD 118.9) | min 13 | max 962
  p90 280 | p95 345 | p99 485 ms
  <100 ms: 77% | <200 ms: 83% | <500 ms: 99%
...
Estimated node -> cloud -> node (one way, median RTT_A/2 + RTT_B/2, assumes uplink ~= downlink):
  helmet <-> leftGlove: ~48 ms
```

| Field | Meaning |
|---|---|
| samples | replies received (compare with readings sent to see loss) |
| median | half the round trips were faster: the typical latency |
| mean (SD) | average and spread; mean ≫ median means a long tail of slow replies |
| min / max | fastest / slowest single round trip |
| p90 / p95 / p99 | 90 / 95 / 99 % of round trips were faster than this |
| <100 / <200 / <500 ms | share of round trips under each threshold |

### Measured (30 Oct 2026, 16:20–16:32, all three devices together, phone hotspot)

| Device | Responder | Readings sent | RTT samples | Median | Mean (SD) | p95 | Max | < 500 ms |
|---|---|---|---|---|---|---|---|---|
| Left glove | ML (SVM) | 1316 | 1308 | **68 ms** | 143 (143) ms | 404 ms | 1473 ms | 98 % |
| Right glove | ML (SVM) | 594 | 589 | **55 ms** | 119 (110) ms | 318 ms | 599 ms | 99 % |
| Helmet | distance rule | 633 | 633 | **27 ms** | 86 (119) ms | 345 ms | 962 ms | 99 % |

Estimated one-way node → cloud → node: helmet ↔ left glove ~48 ms, helmet ↔ right glove ~41 ms, left ↔ right glove ~62 ms.

(The LiDAR saw part of the helmet/mount at ~17 cm, so every helmet message counted as a hazard and got a reply.)

Interpretation:
- A typical reply reaches a device in 30–70 ms; 98–99 % arrive within 500 ms; >99 % of readings were answered.
- The ML step itself is small: feature extraction + SVM prediction takes ~7 ms per reading on the laptop. Most of the RTT is network (Wi-Fi + hotspot), and the glove/helmet difference is more than the ML cost alone explains.
- End-to-end warning time for a turn ≈ ML window (4 readings ≈ 2 s at 2 Hz) + RTT. The window dominates, not the network.
- The 400–1000 ms tail is most likely ESP32 Wi-Fi power saving (radio dozes between beacons) plus phone-hotspot jitter. `WiFi.setSleep(false);` after `WiFi.begin(...)` is the first thing to try.

## Files

| File | Role |
|---|---|
| `firmware/*/*.ino` | add `seq`/`t_ms`, subscribe to `riderguard/cmd/<node>`, publish `riderguard/latency` |
| `ml/predict.py` | glove responder: classifies a window, replies with `seq`/`t_ms` echoed |
| `latency_tools/latency_server.py`, `decision.py` | helmet responder: distance rule, replies with `seq`/`t_ms` echoed |
| `latency_tools/latency_report.py` | summary statistics, node-to-node estimate, histograms |
| `cloud/server.py` | stores `riderguard/latency` in MongoDB |
