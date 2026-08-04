#!/usr/bin/env python3
"""
VFault load and concurrency test suite
========================================
Tests performance under load: latency, throughput,
concurrent requests, and memory usage.
"""

import sys
import os
import time
import threading
import statistics
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vfault import load_all_shards, verify_text, verify_claim, HOT_CACHE, create_app


def setup():
    load_all_shards()
    print(f"Loaded {len(HOT_CACHE)} subjects, {sum(len(v) for v in HOT_CACHE.values())} triples")
    print()


def test_lookup_latency():
    """Test individual lookup latency across many calls."""
    print("TEST 1: Lookup latency (1,000 calls)")
    print("-" * 50)

    test_names = [
        'wp_enqueue_script', 'json.dumps', 'fs.readFile', 'collect',
        'os.path.join', 'Math.sqrt', 'dd', 'Array.prototype.map',
        'wp_register_scripts', 'json.stringify',
    ]

    times = []
    for _ in range(100):
        for name in test_names:
            start = time.perf_counter()
            verify_claim(name)
            elapsed = (time.perf_counter() - start) * 1000  # ms
            times.append(elapsed)

    print(f"  Calls: {len(times)}")
    print(f"  Min:    {min(times):.4f} ms")
    print(f"  Median: {statistics.median(times):.4f} ms")
    print(f"  Mean:   {statistics.mean(times):.4f} ms")
    print(f"  P95:    {sorted(times)[int(len(times)*0.95)]:.4f} ms")
    print(f"  P99:    {sorted(times)[int(len(times)*0.99)]:.4f} ms")
    print(f"  Max:    {max(times):.4f} ms")

    ok = statistics.median(times) < 1.0  # Under 1ms median
    print(f"  Result: {'PASS' if ok else 'FAIL'} — {'under 1ms median' if ok else 'OVER 1ms'}")
    print()
    return ok


def test_verify_text_latency():
    """Test full text verification latency."""
    print("TEST 2: Full text verification latency (100 calls)")
    print("-" * 50)

    sample = """
    Use wp_enqueue_script() and wp_enqueue_style() to load assets.
    In Python use json.dumps() and os.path.join(). In Node.js use
    fs.readFile() and http.createServer(). In Laravel use collect()
    and dd() for debugging. Also try math.sqrt() and re.findall().
    """

    times = []
    for _ in range(100):
        start = time.perf_counter()
        verify_text(sample)
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)

    print(f"  Calls: {len(times)}")
    print(f"  Min:    {min(times):.2f} ms")
    print(f"  Median: {statistics.median(times):.2f} ms")
    print(f"  Mean:   {statistics.mean(times):.2f} ms")
    print(f"  P95:    {sorted(times)[int(len(times)*0.95)]:.2f} ms")
    print(f"  Max:    {max(times):.2f} ms")

    ok = statistics.median(times) < 50  # Under 50ms for full text
    print(f"  Result: {'PASS' if ok else 'FAIL'}")
    print()
    return ok


def test_concurrent_lookups():
    """Test concurrent lookup performance with multiple threads."""
    print("TEST 3: Concurrent lookups (10 threads, 100 each)")
    print("-" * 50)

    results = {'times': [], 'errors': 0}
    lock = threading.Lock()

    names = ['wp_enqueue_script', 'json.dumps', 'fs.readFile', 'collect',
             'os.path.join', 'Math.sqrt', 'wp_register_scripts', 'json.stringify']

    def worker():
        for _ in range(100):
            name = names[_ % len(names)]
            start = time.perf_counter()
            try:
                r = verify_claim(name)
                elapsed = (time.perf_counter() - start) * 1000
                with lock:
                    results['times'].append(elapsed)
            except Exception:
                with lock:
                    results['errors'] += 1

    threads = [threading.Thread(target=worker) for _ in range(10)]

    start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total_time = time.perf_counter() - start

    times = results['times']
    print(f"  Total calls: {len(times)}")
    print(f"  Errors: {results['errors']}")
    print(f"  Total time: {total_time:.2f}s")
    print(f"  Throughput: {len(times)/total_time:.0f} calls/sec")
    print(f"  Median latency: {statistics.median(times):.4f} ms")
    print(f"  P99 latency: {sorted(times)[int(len(times)*0.99)]:.4f} ms")

    ok = results['errors'] == 0 and statistics.median(times) < 2.0
    print(f"  Result: {'PASS' if ok else 'FAIL'}")
    print()
    return ok


def test_concurrent_verify():
    """Test concurrent full text verification."""
    print("TEST 4: Concurrent text verification (10 threads, 10 each)")
    print("-" * 50)

    sample = """
    Use wp_enqueue_script() for scripts. In Python use json.dumps().
    In Node use fs.readFile(). In Laravel use collect() and dd().
    Watch out for wp_register_scripts() — that's wrong.
    """

    results = {'times': [], 'errors': 0, 'correct': 0}
    lock = threading.Lock()

    def worker():
        for _ in range(10):
            start = time.perf_counter()
            try:
                r = verify_text(sample)
                elapsed = (time.perf_counter() - start) * 1000
                with lock:
                    results['times'].append(elapsed)
                    if r['summary']['not_found'] >= 1:
                        results['correct'] += 1
            except Exception:
                with lock:
                    results['errors'] += 1

    threads = [threading.Thread(target=worker) for _ in range(10)]

    start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total_time = time.perf_counter() - start

    times = results['times']
    print(f"  Total calls: {len(times)}")
    print(f"  Errors: {results['errors']}")
    print(f"  Correct results: {results['correct']}/{len(times)}")
    print(f"  Total time: {total_time:.2f}s")
    print(f"  Throughput: {len(times)/total_time:.0f} verifications/sec")
    print(f"  Median latency: {statistics.median(times):.2f} ms")
    print(f"  P99 latency: {sorted(times)[int(len(times)*0.99)]:.2f} ms")

    ok = results['errors'] == 0 and results['correct'] == len(times)
    print(f"  Result: {'PASS' if ok else 'FAIL'}")
    print()
    return ok


def test_api_throughput():
    """Test API endpoint throughput."""
    print("TEST 5: API throughput (100 requests)")
    print("-" * 50)

    app = create_app()
    from fastapi.testclient import TestClient
    client = TestClient(app)

    times = []
    for _ in range(100):
        start = time.perf_counter()
        r = client.post('/verify', json={'text': 'Use wp_enqueue_script() and json.dumps()'})
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)

    print(f"  Calls: {len(times)}")
    print(f"  Median: {statistics.median(times):.2f} ms")
    print(f"  P95:    {sorted(times)[95]:.2f} ms")
    print(f"  P99:    {sorted(times)[99]:.2f} ms")
    print(f"  Throughput: {1000/statistics.median(times):.0f} req/sec (serial)")

    ok = statistics.median(times) < 100  # Under 100ms per API call
    print(f"  Result: {'PASS' if ok else 'FAIL'}")
    print()
    return ok


def test_memory():
    """Check memory usage with all shards loaded."""
    print("TEST 6: Memory usage")
    print("-" * 50)

    import resource
    mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    print(f"  Peak RSS: {mem_mb:.1f} MB")
    print(f"  Subjects cached: {len(HOT_CACHE)}")
    print(f"  Triples cached: {sum(len(v) for v in HOT_CACHE.values())}")
    print(f"  Bytes per triple: {mem_mb * 1024 * 1024 / max(sum(len(v) for v in HOT_CACHE.values()), 1):.0f}")

    ok = mem_mb < 500  # Under 500MB
    print(f"  Result: {'PASS' if ok else 'FAIL'} — {'acceptable' if ok else 'too high'}")
    print()
    return ok


def main():
    print("=" * 60)
    print("VFAULT LOAD & CONCURRENCY TEST SUITE")
    print("=" * 60)
    print()

    setup()

    tests = [
        test_lookup_latency,
        test_verify_text_latency,
        test_concurrent_lookups,
        test_concurrent_verify,
        test_api_throughput,
        test_memory,
    ]

    results = []
    for test in tests:
        try:
            results.append(test())
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append(False)

    passed = sum(results)
    total = len(results)

    print("=" * 60)
    print(f"RESULTS: {passed}/{total} tests passed")
    print("=" * 60)

    if passed == total:
        print("ALL TESTS PASSED")
    else:
        print(f"{total - passed} TESTS FAILED")
        sys.exit(1)


if __name__ == '__main__':
    main()
