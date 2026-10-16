"""Explainable-AI analysis of benchmark outcomes with SHAP and LIME.

A classifier learns to predict whether a sample is delivered correct from facts about it (toolchain, how much of
the project the model saw, model strength, task suite, what went wrong on the first attempt, and whether the
feedback listed what exists). SHAP explains it globally (which facts drive success across all samples) and
locally (why one sample was predicted to succeed or fail); LIME gives a second, independent local explanation.

Read the results as correlations in the benchmark data, not causes: they show which facts go together with
success. Ablations (switching one toolchain feature off and re-running) are the causal test.

Used by `python -m bench.explain ... --xai`; needs scikit-learn, shap and lime.
"""

from __future__ import annotations

import numpy as np

# First-attempt errors, grouped by kind of mistake across both toolchains.
MISTAKES = {
    "1st try: invented import or export": {"R003", "ImportError", "ModuleNotFoundError"},
    "1st try: unknown name": {"R001", "NameError"},
    "1st try: no such field or method": {"R002", "AttributeError"},
    "1st try: wrong arguments": {"T006", "TypeError"},
    "1st try: own test or example failed": {"C001", "C005", "AssertionError"},
    "1st try: type mistake": {"T002", "T003", "T005", "T008", "T009", "C006"},
    "1st try: syntax error": {"S001", "SyntaxError"},
}
GROUND_TRUTH = ("it exports:", "its fields:", "its variants:", "project modules and libraries:", "it declares:")


def features(s: dict) -> dict[str, int]:
    errors = set(s["first_errors"])
    f = {
        "Language: Jig": int(s["cond"].startswith("Jig")),
        "Python writes its own tests": int(s["cond"] == "Python + own tests"),
        "Model saw only part of the code": int("partial view" in s["setting"]),
        "Strong model": int("flash-latest" in s["setting"]),
        "Task: store app": int("store app" in s["setting"]),
        "Task: clinic app": int("clinic app" in s["setting"]),
    }
    f |= {name: int(bool(errors & codes)) for name, codes in MISTAKES.items()}
    f["Feedback listed what exists"] = int(any(g in fb for fb in s["feedback"] for g in GROUND_TRUTH))
    return f


def analyze(samples: list[dict]) -> dict:
    from lime.lime_tabular import LimeTabularExplainer
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold, cross_val_score
    import shap

    rows = [features(s) for s in samples]
    names = list(rows[0])
    X = np.array([[r[n] for n in names] for r in rows], dtype=float)
    y = np.array([int(s["outcome"] == "delivered") for s in samples])

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=0)
    gb = GradientBoostingClassifier(n_estimators=150, max_depth=3, learning_rate=0.1, random_state=0)
    lr = LogisticRegression(max_iter=1000)
    metrics = {
        "samples": len(y),
        "delivered_rate": round(float(y.mean()), 3),
        "gradient_boosting": {m: round(float(cross_val_score(gb, X, y, cv=cv, scoring=m).mean()), 3)
                              for m in ("accuracy", "roc_auc")},
        "logistic_regression": {m: round(float(cross_val_score(lr, X, y, cv=cv, scoring=m).mean()), 3)
                                for m in ("accuracy", "roc_auc")},
    }
    gb.fit(X, y)

    # SHAP: exact Shapley values for the tree model, in log-odds of being delivered.
    values = shap.TreeExplainer(gb).shap_values(X)
    global_ = []
    for j, name in enumerate(names):
        present = X[:, j] == 1
        global_.append({
            "feature": name,
            "mean_abs": round(float(np.abs(values[:, j]).mean()), 4),
            "push_when_present": round(float(values[present, j].mean()), 4) if present.any() else 0.0,
            "present": int(present.sum()),
            "delivered_when_present": round(float(y[present].mean()), 3) if present.any() else None,
            "delivered_when_absent": round(float(y[~present].mean()), 3) if (~present).any() else None,
        })
    global_.sort(key=lambda g: -g["mean_abs"])

    # SHAP interaction values: how much two facts together add beyond each alone (e.g. Jig only helps when
    # the model cannot see everything). Reported for samples where both facts are present.
    inter = shap.TreeExplainer(gb).shap_interaction_values(X)
    pairs = []
    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            both = (X[:, a] == 1) & (X[:, b] == 1)
            if both.sum() >= 10:
                pairs.append({
                    "features": [names[a], names[b]],
                    "together": round(float(2 * inter[both, a, b].mean()), 4),
                    "samples": int(both.sum()),
                })
    pairs.sort(key=lambda p: -abs(p["together"]))

    # LIME: a local linear surrogate around each sample, independent of the tree's internals.
    lime = LimeTabularExplainer(
        X, feature_names=names, class_names=["not delivered", "delivered"],
        categorical_features=list(range(len(names))), discretize_continuous=False, random_state=0,
    )
    local = {}
    probs = gb.predict_proba(X)[:, 1]
    for i, s in enumerate(samples):
        top = sorted(((names[j], float(values[i, j])) for j in range(len(names))), key=lambda t: -abs(t[1]))
        exp = lime.explain_instance(X[i], gb.predict_proba, num_features=5, num_samples=800)
        local[s["id"]] = {
            "p": round(float(probs[i]), 3),
            "shap": [(n, round(v, 3)) for n, v in top[:5] if abs(v) > 0.01],
            "lime": [(text, round(w, 3)) for text, w in exp.as_list()],
        }
    return {"metrics": metrics, "global": global_, "interactions": pairs[:6], "local": local}
