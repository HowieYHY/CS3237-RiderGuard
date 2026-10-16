# RiderGuard SVM training (pattern from Lab 5 train_model.py; SVM pipeline from Lecture 4 SVM.ipynb)
#   python3 train.py exports/leftGloveMPU6050.csv            -> our data, exported by cloud/export.py --labelled
#   python3 train.py --tuwien TrainingData --classes cruise fun --window 250 --step 250
#                                                             -> TU Wien motorcyclist IMU dataset (50 Hz), for prototyping
# --window/--step are in samples, so pick them from the sensor's sample rate (e.g. 2 s window at 50 Hz = 100).
# Train/test split is by group (TU Wien: trip date, ours: continuous labelled run) so near-identical
# neighbouring windows never end up on both sides and inflate the scores.
import argparse
import glob
import os
import re

import joblib
import pandas as pd
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import GroupKFold, GroupShuffleSplit, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from features import AXES, make_windows

TUWIEN_COLS = dict(zip(["Acc_X", "Acc_Y", "Acc_Z", "Gyr_X", "Gyr_Y", "Gyr_Z"], AXES))


def load_export(path):
    """CSV from cloud/export.py -> [(segment, label, group)], one segment per continuous labelled run."""
    df = pd.read_csv(path)
    df["run"] = (df["label"] != df["label"].shift()).cumsum()
    df = df.dropna(subset=["label"])
    return [(seg, seg["label"].iloc[0], run) for run, seg in df.groupby("run")]


def load_tuwien(root, classes):
    """TU Wien TrainingData/<class>/<class>N_Daten_YYYY_MM_DD_C.csv -> [(segment, label, trip date)]."""
    segments = []
    for path in glob.glob(os.path.join(root, "*", "*.csv")):
        label = os.path.basename(os.path.dirname(path))
        if classes and label not in classes:
            continue
        seg = pd.read_csv(path, usecols=list(TUWIEN_COLS)).rename(columns=TUWIEN_COLS)
        segments.append((seg, label, re.search(r"Daten_(\d{4}_\d{2}_\d{2})", path).group(1)))
    return segments


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("export_csv", nargs="?")
    parser.add_argument("--tuwien")
    parser.add_argument("--classes", nargs="+")
    parser.add_argument("--window", type=int, default=100)
    parser.add_argument("--step", type=int, default=50)
    parser.add_argument("--out", default="model.joblib")
    args = parser.parse_args()

    segments = load_tuwien(args.tuwien, args.classes) if args.tuwien else load_export(args.export_csv)
    rows, y, groups = [], [], []
    for seg, label, group in segments:
        windows = make_windows(seg, args.window, args.step)
        rows += windows
        y += [label] * len(windows)
        groups += [group] * len(windows)
    X, y, groups = pd.DataFrame(rows), pd.Series(y), pd.Series(groups)
    labels = sorted(y.unique())
    print(f"{len(X)} windows from {groups.nunique()} groups:\n{y.value_counts().to_string()}\n")

    model = Pipeline([("scale", StandardScaler()), ("svm", SVC(kernel="rbf", class_weight="balanced"))])

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
