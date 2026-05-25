# S-Edge

A parameter-driven, multi-region edge-computing framework that aggregates IoT sensor data at the edge, compresses and encrypts it before upload, balances load across three simulated cloud regions, and replicates aggregated data for fault tolerance — all while providing real-time health monitoring and a live terminal dashboard.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Module Reference](#module-reference)
3. [Data Pipeline](#data-pipeline)
4. [Benchmark Suite](#benchmark-suite)
5. [Quick Start](#quick-start)
6. [Project Structure](#project-structure)
7. [Configuration](#configuration)
8. [Requirements](#requirements)
9. [License](#license)

---

## Architecture Overview
<p align="center">
       <img src="images/system_architecture.png" alt="System Architecture" width="700"/>
</p>
<p align="center"><em>Figure: High-level system architecture of S-Edge framework.</em></p>

The S-Edge System Architecture illustrating the five interconnected layers from sensor data ingestion to multiregion
replication, with explicit feedback coupling arrows showing the closed-loop interaction between the Edge Processing
Node and the Control Plane.

---

## Load Balancing & Compression Algorithms

<p align="center">
       <img src="images/load_balancing_compression_flowchart.png" alt="Load Balancing Compression Flowchart" width="600"/>
</p>
<p align="center"><em>Figure: Flowchart showing the decision process for load balancing and compression selection in S-Edge.</em></p>

---

## Module Reference

All modules live under `multiregion/`.

### `edge.py` — Main Simulator

The entry point. Initialises all managers, spawns per-region edge-device threads and replication threads, and starts the dashboard.

| Function | Purpose |
|---|---|
| `generate_sensor_data()` | Returns a dict with `timestamp`, `temperature`, `humidity`, and a large `system_log` string for compression testing |
| `edge_device(region)` | Collects sensor data, aggregates every 5 readings, runs anomaly detection, assigns priority, and calls `save_to_cloud()` |
| `save_to_cloud(region, data)` | Serialises → compresses (adaptive algorithm) → encrypts (AES-256-GCM) → selects target region via load balancer (with hysteresis) → writes blob → records version in SQLite |
| `replicate_data(src, dst)` | Continuously copies new files from one region's cloud storage to another's replicated storage, simulating WAN latency |
| `read_compressed_data(path)` | Cache-first read: checks `SmartCache`, otherwise decrypts → decompresses → caches |
| `simulate_network_latency(size, type)` | Gaussian latency model (mean 50 ms, σ 15 ms) plus bandwidth-based transmission delay (4G: 10 Mbps, WAN: 100 Mbps) |
| `determine_priority(summary, prediction)` | Maps anomaly detection output + sensor thresholds to `high` / `medium` / `low` priority |

### `load_balancer.py` — Hysteresis-Based Load Balancer

| Detail | Value |
|---|---|
| Algorithm | Least-loaded region selection with 0.7× hysteresis dead-band to prevent oscillation |
| Redistribution | When a region exceeds the threshold (1000 units), 50 % of its excess load is transferred to the optimal region |
| Concurrency | Thread-safe via `threading.RLock` |
| Network cost | Each redistribution incurs a simulated Gaussian latency (mean 50 ms, floor 10 ms) |

### `compression_manager.py` — Adaptive Multi-Algorithm Compression

| Detail | Value |
|---|---|
| Algorithms | ZLIB (level 9), LZMA, BZ2 |
| Selection logic | Default: ZLIB. If compression ratio < 50 %, falls back to BZ2. High-priority data uses LZMA |
| Stats tracked | `total_original`, `total_compressed`, per-item ratios, running average |

### `encryption_manager.py` — AES-256-GCM Encryption

| Detail | Value |
|---|---|
| Library | `pycryptodome` (`Crypto.Cipher.AES`) |
| Mode | GCM (Galois/Counter Mode) — authenticated encryption (AEAD) |
| Key | 256-bit (32 bytes), auto-generated and persisted to `encryption.key` |
| Wire format | `nonce (16 B) ‖ tag (16 B) ‖ ciphertext` |

### `anomaly_detector.py` — Isolation Forest Anomaly Detection

| Detail | Value |
|---|---|
| Model | `sklearn.ensemble.IsolationForest` (contamination = 0.1) |
| Features | `[temperature, humidity]` |
| Online learning | Retrains every 20 data points accumulated via `update()` |
| Integration | Anomaly prediction feeds `determine_priority()` — anomalies are flagged as `high` priority |

### `smart_cache.py` — LRU Cache with TTL

| Detail | Value |
|---|---|
| Backing store | `collections.OrderedDict` |
| Eviction | Least-recently-used when `max_size` (default 20) is exceeded |
| Expiry | Per-item TTL (default 300 s); expired items are silently dropped on `get()` |

### `version_control.py` — SQLite-Backed Data Versioning

| Detail | Value |
|---|---|
| Storage | SQLite database at `.versions/version_history.db` + per-version JSON blobs on disk |
| Checksum | SHA-256 over deterministic JSON serialisation |
| Metadata | Region, priority, compression ratio, upload latency — stored as JSON in SQLite |
| Operations | `save_version()`, `get_version()`, `rollback()` |

### `health_monitor.py` — System Health Monitor

| Detail | Value |
|---|---|
| Metrics | CPU %, system memory %, disk free %, network connections, thread count |
| Collection | Background thread, configurable interval (default 5 s) |
| History | Rolling window of 720 samples per metric |
| Alerts | Threshold-based with 60 s cooldown to prevent spam |
| Default thresholds | CPU > 75 %, memory > 85 %, disk > 85 % |

### `monitoring_dashboard.py` / `dashboard_window.py` — Live Terminal Dashboard

| Detail | Value |
|---|---|
| Library | `rich` (Layout, Panel, Table, Live) |
| Panels | System Metrics, Recent Alerts, Region Statistics, Compression Statistics |
| Compression fields | Size reduction (%), Compression factor (×), Total Original (KB), Total Compressed (KB), Algorithm |
| Inter-process | Main simulator writes `shared_dashboard_data.json`; the dashboard process reads it at 4 Hz |
| Launch | Spawned in a separate terminal window via `run_dashboard.bat` |

---

## Data Pipeline

Each aggregation cycle for a single region:

```
1. Generate 5 sensor readings        (generate_sensor_data)
2. Compute mean temperature/humidity  (edge_device)
3. Run Isolation Forest prediction    (anomaly_detector.predict)
4. Assign priority (high/med/low)     (determine_priority)
5. Serialise to JSON                  (json.dumps + DateTimeEncoder)
6. Select compression algorithm       (ZLIB → BZ2 fallback, or LZMA for high)
7. Compress                           (compression_manager.compress)
8. Encrypt with AES-256-GCM          (encryption_manager.encrypt)
9. Route to optimal region            (load_balancer — hysteresis check)
10. Simulate 4G upload latency        (simulate_network_latency)
11. Write encrypted blob to disk      (region_N_cloud_storage/)
12. Record version + metadata (SQLite)(version_control.save_version)
13. Update load balancer state        (load_balancer.update_load)
14. Cache plain data for fast reads   (smart_cache.set)
```

Circular replication threads (background) copy new blobs between regions, simulating WAN latency per file.

---

## Benchmark Suite

All scripts are in `benchmark/` and produce publication-ready PNG figures.

| Script | Output | What it measures |
|---|---|---|
| `gen_lb_table.py` | `load_balancing_table.png` | Table 3 — Round-Robin vs Least-Connections vs S-Edge over 100 steps: threshold violations, oscillation events, simulated CPU utilisation |
| `gen_benchmark.py` | `load_balancing_comparison.png` | Side-by-side load curves for Round-Robin vs S-Edge |
| `gen_compression_benchmark.py` | `compression_comparison.png` | Compression ratio comparison across ZLIB, LZMA, BZ2 |
| `gen_latency_benchmark.py` | `latency_breakdown.png` | Per-stage latency overhead: anomaly detection, compression, encryption |
| `gen_recovery_benchmark.py` | `recovery_latency.png` | Recovery latency under simulated region failure |
| `investigate_flaps.py` | Console output | Detailed trace of every oscillation event in S-Edge vs Least-Connections |

### Benchmark Visualizations

<p align="center">
  <img src="benchmark/compression_comparison.png" alt="Compression Comparison" width="500"/>
</p>
<p align="center"><em>Figure: Compression ratio comparison across ZLIB, LZMA, BZ2.</em></p>

<p align="center">
  <img src="benchmark/latency_breakdown.png" alt="Pipeline Latency Breakdown" width="500"/>
</p>
<p align="center"><em>Figure: Per-stage latency overhead in the data pipeline.</em></p>
<p align="center">
       <img src="benchmark/load_balancing_comparison.png" alt="Load Balancing Algorithm Comparison" width="600"/>
</p>
<p align="center"><em>Figure: Comparative analysis of Queue Load Distribution across regions.</em></p>
<p align="center">
  <img src="benchmark/recovery_latency.png" alt="Recovery Latency" width="500"/>
</p>
<p align="center"><em>Figure: Recovery latency under simulated region failure.</em></p>

Run any benchmark:

```bash
cd benchmark
python gen_lb_table.py
```

---

## Quick Start

### Prerequisites

- Python 3.8+
- Windows, macOS, or Linux

### Installation

```bash
git clone https://github.com/Emir2099/S-Edge.git
cd EdgeSimulator
pip install -r requirements.txt
```

### Run the Simulator

```bash
cd multiregion
python edge.py
```

The simulator will:
- Start health monitoring and the live dashboard (separate terminal window)
- Spawn 3 edge-device threads (one per region)
- Spawn 3 circular replication threads
- Print per-save stats: compression ratio, upload latency, region loads

Press `Ctrl+C` to shut down gracefully.

### Run the Dashboard Separately

```bash
cd multiregion
python dashboard_window.py
```

Or use the batch launcher (Windows):

```bash
multiregion\run_dashboard.bat
```

### Run Benchmarks

```bash
cd benchmark
python gen_lb_table.py              # Load balancing comparison table
python gen_compression_benchmark.py  # Compression algorithm comparison
python gen_latency_benchmark.py      # Pipeline latency breakdown
python gen_recovery_benchmark.py     # Recovery time under failure
python investigate_flaps.py          # Oscillation event investigation
```

---

## Project Structure

```
EdgeSimulator/
├── edge_simulation.py              # Original single-region demo (baseline)
├── requirements.txt
├── README.md
├── .gitignore
│
├── multiregion/                    # Full S-Edge framework
│   ├── edge.py                     # Main entry point
│   ├── load_balancer.py            # Hysteresis-based load balancer
│   ├── compression_manager.py      # ZLIB / LZMA / BZ2 adaptive compression
│   ├── encryption_manager.py       # AES-256-GCM encryption
│   ├── anomaly_detector.py         # Isolation Forest anomaly detection
│   ├── smart_cache.py              # LRU cache with TTL
│   ├── version_control.py          # SQLite-backed data versioning
│   ├── health_monitor.py           # System health monitoring (psutil)
│   ├── monitoring_dashboard.py     # Dashboard data writer
│   ├── dashboard_window.py         # Rich-based live TUI dashboard
│   ├── run_dashboard.bat           # Windows dashboard launcher
│   └── shared_dashboard_data.json  # Inter-process dashboard data (generated)
│
├── benchmark/                      # Reproducible benchmark scripts
│   ├── gen_lb_table.py             # Table 3 — load balancing comparison
│   ├── gen_benchmark.py            # Load curve visualisation
│   ├── gen_compression_benchmark.py
│   ├── gen_latency_benchmark.py
│   ├── gen_recovery_benchmark.py
│   └── investigate_flaps.py        # Oscillation event forensics
│
├── cloud_storage/                  # Single-region baseline storage
├── replicated_storage/             # Single-region baseline replication
├── region_N_cloud_storage/         # Per-region cloud storage (generated)
└── region_N_replicated_storage/    # Per-region replicated storage (generated)
```

---

## Configuration

Key parameters are set at the top of `multiregion/edge.py` and in each module's constructor:

| Parameter | Location | Default | Description |
|---|---|---|---|
| `regions` | `edge.py` | `['region_1', 'region_2', 'region_3']` | Number and names of simulated regions |
| `aggregation_interval` | `edge_device()` | 5 readings | Readings collected before each aggregation |
| `load_threshold` | `LoadBalancer` | 1000 | Queue-load threshold triggering redistribution |
| Hysteresis factor | `save_to_cloud()` | 0.7 | Dead-band multiplier preventing oscillation |
| Compression default | `CompressionManager` | ZLIB (level 9) | Default algorithm; BZ2 fallback if ratio < 50 %, LZMA for high-priority |
| `max_size` / `ttl` | `SmartCache` | 20 items / 300 s | Cache capacity and per-item expiry |
| `check_interval` | `HealthMonitor` | 5 s | Health metric sampling interval |
| Alert cooldown | `HealthMonitor` | 60 s | Minimum seconds between duplicate alerts |
| Network model | `simulate_network_latency()` | 4G: 10 Mbps, WAN: 100 Mbps | Gaussian base latency (μ=50 ms, σ=15 ms) + bandwidth delay |

---

## Requirements

```
pandas>=1.3.0
numpy>=1.19.0
psutil>=5.8.0
rich>=10.0.0
pycryptodome>=3.9.0
scikit-learn>=0.24.2
matplotlib>=3.4.0
```

Install with:

```bash
pip install -r requirements.txt
```

---

## License

This project is developed for academic research purposes.

