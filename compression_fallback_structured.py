import zlib, bz2, json, time
import numpy as np

results = []
np.random.seed(42)

for tau in [0.3, 0.4, 0.5, 0.6, 0.7]:
    fallbacks, sizes, times = [], [], []
    for _ in range(100):
        # realistic structured sensor packet matching paper description
        packet = json.dumps({
            "temp":     round(np.random.uniform(20.0, 30.0), 3),
            "humidity": round(np.random.uniform(40.0, 60.0), 3),
            "pressure": round(np.random.uniform(1010.0, 1020.0), 3),
            "epoch":    int(np.random.randint(1000000, 9999999)),
            "node_id":  int(np.random.randint(1, 54)),
            "seq":      int(np.random.randint(0, 10000)),
            "status":   "normal",
            "region":   "r1"
        }).encode()

        t0 = time.perf_counter()
        z_out = zlib.compress(packet, level=6)
        eta = (len(packet) - len(z_out)) / len(packet)

        if eta < tau:
            out = bz2.compress(packet)
            fallbacks.append(1)
        else:
            out = z_out
            fallbacks.append(0)

        times.append((time.perf_counter() - t0) * 1000)
        sizes.append(len(out) / 1024)

    print(f"tau={tau}: fallback={np.mean(fallbacks)*100:.1f}%, "
          f"size={np.mean(sizes):.4f}KB, "
          f"time={np.mean(times):.3f}ms")
