import zlib, bz2, os, time
import numpy as np

results = []
for tau in [0.3, 0.4, 0.5, 0.6, 0.7]:
    fallbacks, sizes, times = [], [], []
    for _ in range(100):
        raw = os.urandom(512)  # normal traffic packet
        t0 = time.perf_counter()
        z_out = zlib.compress(raw)
        eta = (len(raw) - len(z_out)) / len(raw)
        if eta < tau:
            out = bz2.compress(raw)
            fallbacks.append(1)
        else:
            out = z_out
            fallbacks.append(0)
        times.append((time.perf_counter() - t0) * 1000)
        sizes.append(len(out) / 1024)
    print(f"tau={tau}: fallback={np.mean(fallbacks)*100:.1f}%, "
          f"size={np.mean(sizes):.3f}KB, time={np.mean(times):.3f}ms")
