"""Compare belief models on synthetic fact histories.
Models:
  A  Hindsight-style additive (+a confirm, -2a contradict, no time decay)
  B  Beta-Bernoulli, source-weighted pseudo-counts, exponential forgetting of counts toward Beta(1,1)
  C  2-state hazard filter (factored: extraction prior x survival; Bayes update w/ source LRs)
  Cmis  C with half-lives 4x too long
  Clearn  Cmis + online gamma-Poisson learning of per-class hazard from observed user-stated changes
"""
import math, random
import numpy as np

random.seed(7); np.random.seed(7)
DAY = 1.0
SRC_R = {  # P(report agrees with truth)
    "user_direct": 0.95, "user_confirmed": 0.98, "tool": 0.90,
    "web": 0.75, "inference": 0.65, "other_agent": 0.70}
SRC_MIX = [("user_direct", .35), ("tool", .15), ("web", .1), ("inference", .3), ("other_agent", .1)]
B_W = {"user_direct": 5, "user_confirmed": 8, "tool": 4, "web": 2, "inference": 1, "other_agent": 1.5}

CLASSES = {  # true half-life (days), evidence rate/day, ask period (days)
    "immutable": (math.inf, 1/180, None),
    "preference": (365, 1/60, 120),
    "state": (14, 1/4, None),
}
DEFAULT_HL = {"immutable": 36500, "preference": 365, "state": 14}
HORIZON = 3 * 365

def draw_src():
    x = random.random(); acc = 0
    for s, p in SRC_MIX:
        acc += p
        if x < acc: return s
    return SRC_MIX[-1][0]

def gen_fact(cls):
    hl, rate, ask = CLASSES[cls]
    src0 = draw_src()
    ever_true = random.random() < SRC_R[src0]
    lam = 0 if hl == math.inf else math.log(2) / hl
    change_t = math.inf if (not ever_true or lam == 0) else np.random.exponential(1 / lam)
    truth = lambda t: ever_true and t < change_t
    ev = []
    t = 0
    while True:
        t += np.random.exponential(1 / rate)
        if t > HORIZON: break
        s = draw_src()
        ok = random.random() < SRC_R[s]
        ev.append((t, s, truth(t) if ok else not truth(t)))
    if ask:
        for ta in np.arange(ask, HORIZON, ask):
            ok = random.random() < SRC_R["user_confirmed"]
            ev.append((ta, "user_confirmed", truth(ta) if ok else not truth(ta)))
    ev.sort()
    queries = sorted(np.random.uniform(0, HORIZON, 15))
    return dict(cls=cls, src0=src0, ever_true=ever_true, change_t=change_t, ev=ev,
                queries=[(q, truth(q)) for q in queries])

# ---------------- models -----------------
class ModelA:
    def __init__(s, cls, src0, **k): s.c = 0.7
    def decay(s, dt): pass
    def update(s, src, agrees): s.c = min(1, s.c + .1) if agrees else max(0, s.c - .2)
    def p(s): return s.c

class ModelB:
    def __init__(s, cls, src0, hl=None, **k):
        s.hl = hl or DEFAULT_HL[cls]; s.a = 1 + B_W[src0]; s.b = 1.0
    def decay(s, dt):
        f = 2 ** (-dt / s.hl); s.a = 1 + (s.a - 1) * f; s.b = 1 + (s.b - 1) * f
    def update(s, src, agrees):
        if agrees: s.a += B_W[src]
        else: s.b += B_W[src]
    def p(s): return s.a / (s.a + s.b)

class ModelC:
    """p = P(fact currently true). Absorbing 'false' state (a changed pref doesn't revert to same value)."""
    def __init__(s, cls, src0, hl=None, **k):
        s.hl = hl or DEFAULT_HL[cls]; s.lam = math.log(2) / s.hl
        s.pp = SRC_R[src0]  # extraction/source prior = P(ever true)
    def decay(s, dt): s.pp *= math.exp(-s.lam * dt)
    def update(s, src, agrees):
        r = SRC_R[src]
        lt, lf = (r, 1 - r) if agrees else (1 - r, r)
        s.pp = s.pp * lt / (s.pp * lt + (1 - s.pp) * lf)
    def p(s): return min(max(s.pp, 1e-6), 1 - 1e-6)

def run(model_factory, facts, learner=None):
    preds, labels, cls_l = [], [], []
    for f in facts:
        hl = learner.hl(f["cls"]) if learner else None
        m = model_factory(f["cls"], f["src0"], hl=hl)
        t = 0; ei = 0; ev = f["ev"]
        for q, lab in f["queries"]:
            while ei < len(ev) and ev[ei][0] <= q:
                te, s, a = ev[ei]; m.decay(te - t); t = te; m.update(s, a); ei += 1
            m.decay(q - t); t = q
            preds.append(m.p()); labels.append(lab); cls_l.append(f["cls"])
        if learner: learner.observe(f)
    return np.array(preds), np.array(labels, float), np.array(cls_l)

class HazardLearner:
    """Gamma-Poisson on per-class change rate. Observed 'change' = a user-sourced contradiction
    following a user-sourced confirmation (what the store could actually see). Exposure = time between
    first and last user-sourced report."""
    def __init__(s, init_hl, a0=2.0):
        s.a = {c: a0 for c in init_hl}; s.b = {c: a0 * h / math.log(2) for c, h in init_hl.items()}
    def hl(s, c): return math.log(2) * s.b[c] / s.a[c]
    def observe(s, f):
        u = [(t, a) for t, src, a in f["ev"] if src.startswith("user")]
        if len(u) < 2: return
        changes = 0; start = u[0][0]; end = u[-1][0]; seen_true = False
        for t, a in u:
            if a: seen_true = True
            elif seen_true: changes = 1; end = t; break
        s.a[f["cls"]] += changes; s.b[f["cls"]] += end - start

def brier(p, y): return float(np.mean((p - y) ** 2))
def ece(p, y, n=10):
    bins = np.minimum((p * n).astype(int), n - 1); e = 0
    for b in range(n):
        m = bins == b
        if m.any(): e += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(e)
def reliability(p, y, n=5):
    bins = np.minimum((p * n).astype(int), n - 1); out = []
    for b in range(n):
        m = bins == b
        if m.any(): out.append(f"[{b/n:.1f}-{(b+1)/n:.1f}) n={m.sum():5d} pred={p[m].mean():.2f} obs={y[m].mean():.2f}")
    return out

facts = [gen_fact(c) for c in ["immutable"] * 600 + ["preference"] * 800 + ["state"] * 600]
random.shuffle(facts)
MIS = {c: h * 4 for c, h in DEFAULT_HL.items()}
results = {
    "A_hindsight": run(ModelA, facts),
    "B_beta_forget": run(ModelB, facts),
    "C_hazard": run(ModelC, facts),
    "C_mis(4x hl)": run(lambda c, s, hl=None: ModelC(c, s, hl=MIS[c]), facts),
}
L = HazardLearner(MIS)
results["C_learn(from 4x)"] = run(ModelC, facts, learner=L)
print("learned half-lives:", {c: round(L.hl(c)) for c in MIS})
print(f"{'model':18s} {'Brier':>6s} {'ECE':>6s} | Brier by class imm/pref/state")
for k, (p, y, c) in results.items():
    by = " / ".join(f"{brier(p[c==cl], y[c==cl]):.3f}" for cl in ["immutable", "preference", "state"])
    print(f"{k:18s} {brier(p,y):6.3f} {ece(p,y):6.3f} | {by}")
print("base-rate Brier:", round(brier(np.full_like(results['C_hazard'][1], results['C_hazard'][1].mean()), results['C_hazard'][1]), 3))
for k in ["A_hindsight", "B_beta_forget", "C_hazard"]:
    print("\nreliability", k); [print("  ", l) for l in reliability(*results[k][:2])]

# ---------------- scenarios -----------------
def scenario(name, cls, src0, events, t_query):
    row = [name]
    for mk, M in [("A", ModelA), ("B", ModelB), ("C", ModelC)]:
        m = M(cls, src0); t = 0
        for te, s, a in events:
            m.decay(te - t); t = te; m.update(s, a)
        m.decay(t_query - t)
        row.append(f"{mk}={m.p():.2f}")
    print("  ".join(row))

print("\nScenarios (P fact true now):")
confs = [(90 * i, "user_confirmed", True) for i in range(1, 9)]
scenario("S1a long-confirmed pref, before contradiction", "preference", "user_direct", confs, 730)
scenario("S1b ...+1 contradiction from inference   ", "preference", "user_direct", confs + [(730, "inference", False)], 730)
scenario("S1c ...+1 contradiction from user_direct ", "preference", "user_direct", confs + [(730, "user_direct", False)], 730)
scenario("S1d immutable name + inference contra    ", "immutable", "user_direct", confs + [(730, "inference", False)], 730)
scenario("S2 pref stated once, 0d                  ", "preference", "user_direct", [], 0)
scenario("S2 pref stated once, 180d                ", "preference", "user_direct", [], 180)
scenario("S2 pref stated once, 365d                ", "preference", "user_direct", [], 365)
scenario("S3 name stated once, 5y                  ", "immutable", "user_direct", [], 5 * 365)
scenario("S4 state stated once, 14d                ", "state", "user_direct", [], 14)
scenario("S5 pref inferred once, 30d               ", "preference", "inference", [], 30)
