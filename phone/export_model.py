# Run on the LAPTOP after train.py: turns model.joblib (scikit-learn) into model.json for the phone,
# where scikit-learn cannot be installed. predict.py's NumpySVM gives the same predictions with numpy only.
#   python export_model.py model.joblib model.json
import json
import sys

import joblib
import numpy as np
import pandas as pd

src, dst = (sys.argv[1:3] + ["model.joblib", "model.json"][len(sys.argv[1:3]):])
bundle = joblib.load(src)
model = bundle["model"]
imp, scale, svm = model.named_steps["impute"], model.named_steps["scale"], model.named_steps["svm"]
if svm.kernel != "rbf" or len(svm.classes_) < 3:
    sys.exit("Only multi-class RBF SVMs (train.py's pipeline) can be exported")

out = {"window": bundle["window"], "step": bundle["step"], "labels": list(map(str, bundle["labels"])),
       "features": list(map(str, model.feature_names_in_)), "median": imp.statistics_.tolist(),
       "mean": scale.mean_.tolist(), "scale": scale.scale_.tolist(), "gamma": float(svm._gamma),
       "classes": list(map(str, svm.classes_)), "n_support": svm.n_support_.tolist(),
       "support_vectors": svm.support_vectors_.tolist(), "dual_coef": svm.dual_coef_.tolist(),
       "intercept": svm.intercept_.tolist()}
with open(dst, "w") as f:
    json.dump(out, f)

# Check the numpy version agrees with scikit-learn on random inputs, with some values missing
from predict import NumpySVM
rng = np.random.default_rng(0)
X = pd.DataFrame(rng.normal(scale.mean_, scale.scale_ * 2, (500, len(out["features"]))), columns=out["features"])
X = X.mask(rng.random(X.shape) < 0.1)
same = sum(NumpySVM(out).predict(X.iloc[[i]])[0] == p for i, p in enumerate(model.predict(X)))
print(f"Saved {dst}: {len(out['support_vectors'])} support vectors, numpy matches scikit-learn on {same}/500")
