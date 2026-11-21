# RiderGuard SVM training (pattern from Lab 5 train_model.py; SVM pipeline from Lecture 4 SVM.ipynb)
# Uses all three devices together: glove IMUs + glove distance + helmet GPS speed + helmet LiDAR.
#   python3 train.py --session LEFT.csv RIGHT.csv LOCATION.csv [--session ... next session] --window 4 --step 1
# Each --session is one recording session's three CSVs from cloud/export.py --labelled.
#   ... --glove-session LEFT.csv RIGHT.csv hardbrake   -> gloves-only session, every numbered set is that class
#   ... --glove-session LEFT.csv RIGHT.csv auto        -> gloves-only session labelled per set (safe_1, hardbrake_2)
#   ... --helmet-session LOCATION.csv                  -> helmet-only session labelled per run (safe_1, speeding_2)
# --window/--step are in seconds (the devices send at different rates, so windows are matched by received_at).
# Run boundaries and labels come from LOCATION.csv; the gloves' own label columns are ignored.
# Train/test split is by labelled run so near-identical neighbouring windows never end up on both sides
# and inflate the scores.
import argparse

import joblib
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import GroupKFold, GroupShuffleSplit, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from features import base_label, make_time_windows


def run_windows(devices, t0, t1, size_s, step_s):
    """Windows over one run; a run shorter than the window (e.g. a 2 s hard brake) still gives one window."""
    return make_time_windows(devices, t0, t1, size_s, step_s) or make_time_windows(devices, t0, t1 + 1e-6, t1 - t0 + 1e-6, 1)


def load_session(left_path, right_path, location_path, size_s, step_s, label=None):
    """One session's three exports -> [(windows, label, group)], one entry per labelled run in LOCATION.csv.
    label: class for every run, when the label column only numbers the sets (56, 57, ...)."""
    devices = {k: pd.read_csv(p) for k, p in (("left", left_path), ("right", right_path), ("helmet", location_path))}
    ref = devices["helmet"]
    ref = ref.assign(run=(ref["label"] != ref["label"].shift()).cumsum()).dropna(subset=["label"])
    return [(run_windows(devices, seg["received_at"].min(), seg["received_at"].max(), size_s, step_s),
             base_label(label or seg["label"].iloc[0]), f"{location_path}#{run}") for run, seg in ref.groupby("run")]


def load_glove_session(left_path, right_path, label, size_s, step_s):
    """Gloves-only session, one run per value of the label column (safe_1, hardbrake_2, ... or just 7, 8, ...).
    LABEL "auto" takes each run's class from its label (safe_1 -> safe); any other LABEL is used for every run.
    The helmet is left out, so its features are NaN and filled by the imputer like a missing GPS fix."""
    devices = {"left": pd.read_csv(left_path), "right": pd.read_csv(right_path), "helmet": pd.DataFrame(columns=["received_at"])}
    both = pd.concat([devices["left"], devices["right"]]).dropna(subset=["label"])
    return [(run_windows(devices, seg["received_at"].min(), seg["received_at"].max(), size_s, step_s),
             base_label(run if label == "auto" else label), f"{left_path}#{run}") for run, seg in both.groupby("label")]


def load_helmet_session(location_path, size_s, step_s):
    """Helmet-only session labelled per run (safe_1, speeding_2, ...). The gloves are left out, so their
    features are NaN and filled by the imputer."""
    empty = pd.DataFrame(columns=["received_at"])
    devices = {"left": empty, "right": empty, "helmet": pd.read_csv(location_path)}
    ref = devices["helmet"].dropna(subset=["label"])
    return [(run_windows(devices, seg["received_at"].min(), seg["received_at"].max(), size_s, step_s),
             base_label(run), f"{location_path}#{run}") for run, seg in ref.groupby("label")]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", nargs="+", action="append", default=[],
                        help="LEFT RIGHT LOCATION [CLASS]; CLASS labels every run when the sets are only numbered")
    parser.add_argument("--glove-session", nargs=3, action="append", default=[], metavar=("LEFT", "RIGHT", "LABEL"),
                        help="gloves-only session where every set is LABEL (e.g. hardbrake), or auto to read each set's label")
    parser.add_argument("--helmet-session", action="append", default=[], metavar="LOCATION",
                        help="helmet-only session labelled per run (safe_1, speeding_2, ...)")
    parser.add_argument("--classes", nargs="+")
    parser.add_argument("--window", type=float, default=4)
    parser.add_argument("--step", type=float, default=1)
    parser.add_argument("--out", default="model.joblib")
    args = parser.parse_args()

    rows, y, groups = [], [], []
    if any(len(s) not in (3, 4) for s in args.session):
        parser.error("--session takes LEFT RIGHT LOCATION [CLASS]")
    runs = [r for s in args.session for r in load_session(*s[:3], args.window, args.step, *s[3:])]
    runs += [r for s in args.glove_session for r in load_glove_session(*s, args.window, args.step)]
    runs += [r for s in args.helmet_session for r in load_helmet_session(s, args.window, args.step)]
    for windows, label, group in runs:
        if args.classes and label not in args.classes:
            continue
        rows += windows
        y += [label] * len(windows)
        groups += [group] * len(windows)
    X, y, groups = pd.DataFrame(rows), pd.Series(y), pd.Series(groups)
    labels = sorted(y.unique())
    print(f"{len(X)} windows from {groups.nunique()} runs:\n{y.value_counts().to_string()}\n")

    # Imputer fills gaps (no GPS fix, no LiDAR scan, a device silent in a window) with the training median
    model = Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler()),
                      ("svm", SVC(kernel="rbf", class_weight="balanced"))])

    train_idx, test_idx = next(GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42).split(X, y, groups))
    folds = min(5, groups.nunique())
    if folds >= 2:
        cv = cross_val_score(model, X, y, groups=groups, cv=GroupKFold(folds))
        print(f"{folds}-fold grouped CV accuracy: {cv.mean():.3f} (+/- {cv.std():.3f})")
    model.fit(X.iloc[train_idx], y.iloc[train_idx])
    pred = model.predict(X.iloc[test_idx])

    print(f"Test accuracy: {accuracy_score(y.iloc[test_idx], pred):.3f}")
    print(f"Confusion matrix (rows = true, cols = predicted, order {labels}):")
    print(confusion_matrix(y.iloc[test_idx], pred, labels=labels))
    print(classification_report(y.iloc[test_idx], pred, labels=labels, digits=3, zero_division=0))

    # Refit on all data for deployment
    model.fit(X, y)
    joblib.dump({"model": model, "window": args.window, "step": args.step, "labels": labels}, args.out)
    print(f"Saved {args.out}")
