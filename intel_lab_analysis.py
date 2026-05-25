
import pandas as pd
import numpy as np
import zlib, bz2, sqlite3, hashlib, time, os, json
from sklearn.ensemble import IsolationForest
from sklearn.metrics import f1_score, precision_score, recall_score

# ── 1. Load Intel Lab data ───────────────────────────────────────────────────
df = pd.read_csv(
    "data.txt.gz", sep=r"\s+", header=None,
    names=["date","time","epoch","moteid","temp","humidity","light","voltage"]
)
df = df.dropna(subset=["temp","humidity"])
df = df[(df["temp"] > 0) & (df["temp"] < 50)]
df = df[(df["humidity"] > 0) & (df["humidity"] < 100)]
df = df.sort_values(["moteid","epoch"]).reset_index(drop=True)

# ── 2. Aggregate into 5-second windows (group by moteid + epoch bucket) ─────
# Each window: mean, std, min, max of temp and humidity = 8 features
# This matches the paper's "summary vectors at fixed intervals"
df["window"] = (df["epoch"] // 5).astype(int)
agg = df.groupby(["moteid","window"]).agg(
    temp_mean=("temp","mean"), temp_std=("temp","std"),
    temp_min=("temp","min"),  temp_max=("temp","max"),
    hum_mean=("humidity","mean"), hum_std=("humidity","std"),
    hum_min=("humidity","min"),   hum_max=("humidity","max"),
    count=("temp","count")
).reset_index().dropna()

features = ["temp_mean","temp_std","temp_min","temp_max",
            "hum_mean","hum_std","hum_min","hum_max"]
X = agg[features].values


# ── 3. Label anomalies — 99th percentile of temp deviation ──────────────────
# Compute deviation on full dataset first
agg = agg.sort_values(["moteid","window"]).reset_index(drop=True)
agg["temp_baseline"] = (
    agg.groupby("moteid")["temp_mean"]
       .transform(lambda s: s.rolling(window=288, min_periods=1).mean())
)
agg["deviation"] = (agg["temp_mean"] - agg["temp_baseline"]).abs()
thresh_99 = agg["deviation"].quantile(0.99)
agg["is_anomaly"] = (agg["deviation"] > thresh_99).astype(int)

print(f"Total anomalies in full dataset: {agg['is_anomaly'].sum()}")
print(f"Total normal in full dataset: {(agg['is_anomaly']==0).sum()}")

# Build balanced test set: 900 normal + 100 anomalous
normal_samples  = agg[agg["is_anomaly"] == 0].sample(
                      n=1000, random_state=42).reset_index(drop=True)
anomaly_samples = agg[agg["is_anomaly"] == 1].sample(
                      n=100,  random_state=42).reset_index(drop=True)

# Training set: first 900 normal samples
X_train = normal_samples.head(900)[features].values

# Test set: remaining 100 normal + all 100 anomalous
test_normal   = normal_samples.tail(100)
test_set      = pd.concat([test_normal, anomaly_samples],
                           ignore_index=True).sample(
                           frac=1, random_state=42).reset_index(drop=True)
X_test  = test_set[features].values
y_test  = test_set["is_anomaly"].values

print(f"Test set size: {len(test_set)}")
print(f"Test set anomaly rate: {y_test.mean()*100:.1f}%")

# ── 4. Anomaly Detection F1 ──────────────────────────────────────────────────
clf = IsolationForest(contamination=0.1, random_state=42)
clf.fit(X_train)
preds = (clf.predict(X_test) == -1).astype(int)
f1   = f1_score(y_test, preds, zero_division=0)
prec = precision_score(y_test, preds, zero_division=0)
rec  = recall_score(y_test, preds, zero_division=0)
print(f"Anomaly Detection — Precision: {prec:.3f}, Recall: {rec:.3f}, F1: {f1:.3f}")

# ── 5. Compression Ratios — encode as structured JSON packet ────────────────
# This matches the paper's structured sensor log payload
zlib_ratios, bz2_ratios = [], []
for _, row in test_set[features].head(200).iterrows():
    packet = json.dumps({
        "temp_mean": round(row["temp_mean"], 3),
        "temp_std":  round(row["temp_std"],  3),
        "temp_min":  round(row["temp_min"],  3),
        "temp_max":  round(row["temp_max"],  3),
        "hum_mean":  round(row["hum_mean"],  3),
        "hum_std":   round(row["hum_std"],   3),
        "hum_min":   round(row["hum_min"],   3),
        "hum_max":   round(row["hum_max"],   3),
        "count":     int(row["count"]) if "count" in row.index else 0,
        "timestamp": int(time.time())
    }).encode()
    z_out = zlib.compress(packet, level=6)
    b_out = bz2.compress(packet)
    zlib_ratios.append((len(packet) - len(z_out)) / len(packet))
    bz2_ratios.append((len(packet) - len(b_out)) / len(packet))

print(f"Mean raw packet size: {len(packet)} bytes")
print(f"Compression Ratio ZLIB: {np.mean(zlib_ratios)*100:.1f}%")
print(f"Compression Ratio BZ2:  {np.mean(bz2_ratios)*100:.1f}%")

# ── 6. Threshold Violations ──────────────────────────────────────────────────
violations = 0
load = 0
Lthresh = 1000
for _, row in test_set[features].head(100).iterrows():
    packet = json.dumps(row.to_dict()).encode()
    compressed = zlib.compress(packet, level=6)
    # AES-GCM adds 12 (nonce) + 16 (tag) = 28 bytes overhead
    encrypted_size = len(compressed) + 28
    load += encrypted_size
    if load > Lthresh:
        violations += 1
        load = 0
print(f"Threshold Violations / 100 steps: {violations}")

# ── 7. Rollback Latency — file-based WAL SQLite matching paper setup ─────────
db_path = "/tmp/intel_lab_test.db"
if os.path.exists(db_path):
    os.remove(db_path)
db = sqlite3.connect(db_path)
db.execute("PRAGMA journal_mode=WAL")
db.execute("""CREATE TABLE records
              (id INTEGER PRIMARY KEY, data TEXT,
               checksum TEXT, timestamp REAL)""")
db.commit()

latencies = []
for i in range(50):
    data = json.dumps(test_set[features].iloc[i].to_dict())
    chk  = hashlib.sha256(data.encode()).hexdigest()
    db.execute("BEGIN")
    db.execute("INSERT INTO records(data,checksum,timestamp) VALUES(?,?,?)",
               (data, chk, time.time()))
    # simulate corruption event — rollback instead of commit
    t0 = time.perf_counter()
    db.rollback()
    latencies.append((time.perf_counter() - t0) * 1000)

db.close()
os.remove(db_path)
print(f"Mean Rollback Latency: {np.mean(latencies):.3f} ms")
print(f"Std:                   {np.std(latencies):.3f} ms")
