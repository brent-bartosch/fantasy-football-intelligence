"""GMM tier clustering (Boris Chen / fftiers method) over a 1-D value vector.
K chosen by BIC over 2..max_k. Deterministic: fixed random_state."""
import numpy as np
from sklearn.mixture import GaussianMixture


def gmm_tiers(values: list[float], max_k: int = 9) -> list[int]:
    if len(values) < 4:
        raise ValueError(f"need >=4 values to tier, got {len(values)}")
    x = np.asarray(values, dtype=float).reshape(-1, 1)
    best, best_bic = None, np.inf
    for k in range(2, min(max_k, len(values) // 2) + 1):
        gm = GaussianMixture(n_components=k, random_state=17, n_init=3).fit(x)
        bic = gm.bic(x)
        if bic < best_bic:
            best, best_bic = gm, bic
    labels = best.predict(x)
    # relabel clusters so tier 1 = highest mean value
    order = np.argsort(-best.means_.ravel())
    remap = {int(old): rank + 1 for rank, old in enumerate(order)}
    return [remap[int(l)] for l in labels]
