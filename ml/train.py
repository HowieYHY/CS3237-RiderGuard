# RiderGuard SVM training (pattern from Lab 5 train_model.py; SVM pipeline from Lecture 4 SVM.ipynb)
# Uses all three devices together: glove IMUs + glove distance + helmet GPS speed + helmet LiDAR.
#   python3 train.py --session LEFT.csv RIGHT.csv LOCATION.csv [--session ... next session] --window 4 --step 1
# Each --session is one recording session's three CSVs from cloud/export.py --labelled.
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


def load_session(left_path, right_path, location_path, size_s, step_s):
    """One session's three exports -> [(windows, label, group)], one entry per labelled run in LOCATION.csv."""
    devices = {k: pd.read_csv(p) for k, p in (("left", left_path), ("right", right_path), ("helmet", location_path))}
    ref = devices["helmet"]
    ref = ref.assign(run=(ref["label"] != ref["label"].shift()).cumsum()).dropna(subset=["label"])
    return [(make_time_windows(devices, seg["received_at"].min(), seg["received_at"].max(), size_s, step_s),
             base_label(seg["label"].iloc[0]), f"{location_path}#{run}") for run, seg in ref.groupby("run")]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", nargs=3, action="append", required=True, metavar=("LEFT", "RIGHT", "LOCATION"))
    parser.add_argument("--classes", nargs="+")
    parser.add_argument("--window", type=float, default=4)
    parser.add_argument("--step", type=float, default=1)
    parser.add_argument("--out", default="model.joblib")
    args = parser.parse_args()

    rows, y, groups = [], [], []
    for session in args.session:
        for windows, label, group in load_session(*session, args.window, args.step):
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
