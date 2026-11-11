# RiderGuard feature extraction, shared by train.py and predict.py so features always match.
# Every window uses all three devices: fused_features(left, right, helmet), each a DataFrame of that device's
# readings from the same bundle["window"] seconds.
import re

import numpy as np
import pandas as pd

AXES = ["ax", "ay", "az", "gx", "gy", "gz"]

# Raw tagger labels -> classes. Run suffixes (_1, _2) and typos like "1abrupt left turn" are stripped by base_label.
CLASS_ALIASES = {"uneven": "uneven_road", "hard_break": "hardbrake", "hard_brake": "hardbrake",
                 "abrupt_left_turn": "abrupt_turn", "abrupt_right_turn": "abrupt_turn"}


def base_label(raw):
    """'abrupt right turn_3' / 'abrupt_left_turn_1' / 'hard_break' -> 'abrupt_turn' / 'abrupt_turn' / 'hardbrake'."""
    s = re.sub(r"_\d+$", "", str(raw).strip().lower())
    s = re.sub(r"^\d+", "", s).strip().replace(" ", "_")
    return CLASS_ALIASES.get(s, s)


def imu_features(w):
    """One glove's IMU rows in a window (DataFrame with AXES columns) -> dict of features."""
    f = {}
    for a in AXES:
        f[f"{a}_mean"], f[f"{a}_std"] = w[a].mean(), w[a].std()
        f[f"{a}_min"], f[f"{a}_max"] = w[a].min(), w[a].max()
    for name, cols in (("acc", AXES[:3]), ("gyr", AXES[3:])):
        mag = np.sqrt((w[cols] ** 2).sum(axis=1))
        f[f"{name}_mag_mean"], f[f"{name}_mag_std"], f[f"{name}_mag_max"] = mag.mean(), mag.std(), mag.max()
    return f


def valid(w, col):
    """Numeric column with invalid readings dropped (sensors send -1 / 0 when they have no reading)."""
    v = pd.to_numeric(w[col], errors="coerce") if col in w else pd.Series(dtype=float)
    return v[v > 0]


def fused_features(left, right, helmet):
    """Readings from all three devices in one time window -> dict of features.
    Latitude/longitude/altitude are left out on purpose: they describe where we recorded, not how we rode.
    Missing values (device silent, no GPS fix, no LiDAR scan) are NaN and filled by the model's imputer."""
    f = {}
    for side, w in (("L", left), ("R", right)):
        f.update({f"{side}_{k}": v for k, v in imu_features(w.reindex(columns=AXES)).items()})
        d = valid(w, "distance_cm")  # glove ultrasonic
        f[f"{side}_dist_min"], f[f"{side}_dist_mean"] = d.min(), d.mean()
    gps = helmet[helmet["location_valid"].astype(str).str.lower() == "true"] if "location_valid" in helmet else helmet
    speed = pd.to_numeric(gps["speed_kmph"], errors="coerce").dropna() if "speed_kmph" in gps else pd.Series(dtype=float)
    f["speed_mean"], f["speed_min"], f["speed_max"] = speed.mean(), speed.min(), speed.max()
    f["speed_change"] = speed.iloc[-1] - speed.iloc[0] if len(speed) else np.nan  # negative when braking
    lidar = valid(helmet, "nearest_distance_cm")
    f["lidar_min"], f["lidar_mean"] = lidar.min(), lidar.mean()
    return f


def make_time_windows(devices, t0, t1, size_s, step_s):
    """Slide a [t, t + size_s) window over one labelled run [t0, t1] -> list of fused feature dicts.
    devices: {"left": df, "right": df, "helmet": df}, each with a received_at column (laptop/phone time)."""
    out, t = [], t0
    while t + size_s <= t1:
        part = {k: d[(d["received_at"] >= t) & (d["received_at"] < t + size_s)] for k, d in devices.items()}
        out.append(fused_features(part["left"], part["right"], part["helmet"]))
        t += step_s
    return out
