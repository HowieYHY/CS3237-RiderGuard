# RiderGuard feature extraction, shared by train.py and the live decision service so features always match.
# Live use:
#   bundle = joblib.load("model.joblib")
#   x = pd.DataFrame([window_features(last_n_readings)])   # last_n_readings: DataFrame, bundle["window"] rows
#   label = bundle["model"].predict(x)[0]
import numpy as np

AXES = ["ax", "ay", "az", "gx", "gy", "gz"]


def window_features(w):
    """One window of IMU rows (DataFrame with AXES columns) -> dict of features."""
    f = {}
    for a in AXES:
        f[f"{a}_mean"], f[f"{a}_std"] = w[a].mean(), w[a].std()
        f[f"{a}_min"], f[f"{a}_max"] = w[a].min(), w[a].max()
    for name, cols in (("acc", AXES[:3]), ("gyr", AXES[3:])):
        mag = np.sqrt((w[cols] ** 2).sum(axis=1))
        f[f"{name}_mag_mean"], f[f"{name}_mag_std"], f[f"{name}_mag_max"] = mag.mean(), mag.std(), mag.max()
    return f


def make_windows(segment, size, step):
    """Slide a window over one continuous, single-label segment -> list of feature dicts."""
    return [window_features(segment.iloc[i:i + size]) for i in range(0, len(segment) - size + 1, step)]
