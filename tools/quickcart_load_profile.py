#!/usr/bin/env python3
"""Bounded demo load profile to run ON the QuickCart workload EC2 host.

The CPU workers create synthetic host CPU pressure so CloudWatch can observe a
clear threshold event. Optional HTTP workers also exercise the app endpoint.
This is only a short POC demonstration tool, not a production/telecom benchmark.
"""
from __future__ import annotations

import argparse
import hashlib
import multiprocessing as mp
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen


def _cpu_worker(stop_event) -> None:
    payload = b"cloudpilot-quickcart-poc-cpu-profile"
    while not stop_event.is_set():
        # CPU work is deliberately synthetic; stop_event bounds its lifetime.
        for _ in range(25000):
            payload = hashlib.sha256(payload).digest()
        if stop_event.is_set():
            break


def _http_worker(url: str, deadline: float, high_phase_end: float, ramp_phase_end: float, results: dict, lock: threading.Lock) -> None:
    while time.monotonic() < deadline:
        request = Request(url, headers={"User-Agent": "Innominds-IIPP-LoadProfile/1.0"})
        try:
            with urlopen(request, timeout=3) as response:
                code = response.status
            with lock:
                results["attempts"] += 1
                if 200 <= code < 400:
                    results["success"] += 1
                else:
                    results["errors"] += 1
        except (URLError, HTTPError, TimeoutError, OSError):
            with lock:
                results["attempts"] += 1
                results["errors"] += 1
        now = time.monotonic()
        # Reduce HTTP request frequency with each phase to let target CPU recover.
        time.sleep(0.05 if now < high_phase_end else (0.20 if now < ramp_phase_end else 0.80))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=int, default=240, help="Profile duration, 30-300 seconds (default: 240)")
    parser.add_argument("--cpu-workers", type=int, default=2, help="Synthetic CPU workers, 0-2 (default: 2)")
    parser.add_argument("--url", default="http://127.0.0.1:8000/", help="QuickCart endpoint on this host; use --no-http to skip")
    parser.add_argument("--http-concurrency", type=int, default=4, help="Concurrent endpoint requests, 1-8 (default: 4)")
    parser.add_argument("--no-http", action="store_true", help="Only create synthetic CPU pressure")
    args = parser.parse_args()

    if not 30 <= args.duration <= 300:
        parser.error("--duration must be between 30 and 300 seconds")
    if not 0 <= args.cpu_workers <= 2:
        parser.error("--cpu-workers must be between 0 and 2 to bound resource use")
    if not 1 <= args.http_concurrency <= 8:
        parser.error("--http-concurrency must be between 1 and 8")
    if args.cpu_workers == 0 and args.no_http:
        parser.error("Nothing to run: choose CPU workers or enable HTTP traffic")

    cpu_count = os.cpu_count() or 1
    if args.cpu_workers > cpu_count:
        parser.error(f"This host reports {cpu_count} logical CPU(s); reduce --cpu-workers")

    print("Innominds IIPP QuickCart POC load profile")
    print(f"Duration: {args.duration}s | CPU workers: {args.cpu_workers} | HTTP target: {'disabled' if args.no_http else args.url}")
    print("This is synthetic POC pressure, not a telecom production workload. Profile phases: high CPU, reduced CPU, then CPU off; all workers stop automatically.", flush=True)

    ctx = mp.get_context("fork" if "fork" in mp.get_all_start_methods() else "spawn")
    stop_events = [ctx.Event() for _ in range(args.cpu_workers)]
    cpu_processes = [ctx.Process(target=_cpu_worker, args=(stop_event,), daemon=True) for stop_event in stop_events]
    for process in cpu_processes:
        process.start()

    start = time.monotonic()
    deadline = start + args.duration
    high_phase_end = start + args.duration * 0.50
    ramp_phase_end = start + args.duration * 0.75
    results = {"attempts": 0, "success": 0, "errors": 0}
    lock = threading.Lock()
    pool = None
    try:
        if not args.no_http:
            pool = ThreadPoolExecutor(max_workers=args.http_concurrency)
            futures = [pool.submit(_http_worker, args.url, deadline, high_phase_end, ramp_phase_end, results, lock) for _ in range(args.http_concurrency)]
        # Phase 1: full configured CPU worker count; phase 2: remove one worker;
        # phase 3: stop CPU workers while optional low-rate HTTP traffic continues.
        while time.monotonic() < high_phase_end:
            time.sleep(1)
        if len(stop_events) >= 2:
            stop_events[0].set()
            cpu_processes[0].join(timeout=3)
            if cpu_processes[0].is_alive():
                cpu_processes[0].terminate()
                cpu_processes[0].join(timeout=2)
            print("Load profile phase: reduced CPU pressure (one worker stopped).", flush=True)
        while time.monotonic() < ramp_phase_end:
            time.sleep(1)
        for stop_event in stop_events:
            stop_event.set()
        for process in cpu_processes:
            process.join(timeout=3)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
        print("Load profile phase: CPU pressure stopped; optional HTTP traffic continues at low concurrency until the profile ends.", flush=True)
        while time.monotonic() < deadline:
            time.sleep(1)
        if pool:
            pool.shutdown(wait=True, cancel_futures=True)
            pool = None
    except KeyboardInterrupt:
        print("Interrupted; stopping workers…", flush=True)
    finally:
        for stop_event in stop_events:
            stop_event.set()
        if pool:
            pool.shutdown(wait=False, cancel_futures=True)
        for process in cpu_processes:
            process.join(timeout=3)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)

    elapsed = max(time.monotonic() - start, 0.01)
    print(f"Finished in {elapsed:.1f}s. HTTP attempts={results['attempts']}, successes={results['success']}, errors={results['errors']}.")
    print("CPU workers are stopped. Allow CloudWatch time to publish three distinct low-CPU datapoints for a scale-down recommendation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
