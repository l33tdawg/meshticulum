#!/usr/bin/env python3
"""Profile local durable journal calls using explicitly synthetic history.

This opens only fresh temporary SQLite files. It never opens a device, grants
authority, transmits, or measures radio throughput. Wall timings and SQLite VM
instruction counts are collected separately: the progress callback perturbs
execution time and therefore is disabled for all latency samples.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from meshticulum.bench.attempts import AttemptJournal

MAX_HISTORY = 50_000
MAX_SAMPLES = 128
MAX_CASES = 8
ESTIMATE_US = 1000
PACKET = b'meshticulum-offline-journal-profile'
METADATA = b'offline-synthetic-queue-metadata'
BINDING = hashlib.sha256(b'meshticulum.offline-journal-profile.v1').digest()


def validate(histories, samples):
    if (type(histories) not in (tuple, list) or not 1 <= len(histories) <= MAX_CASES
            or any(type(value) is not int or not 0 <= value <= MAX_HISTORY
                   for value in histories)
            or len(set(histories)) != len(histories)):
        raise ValueError('histories must contain 1..8 distinct integers in 0..50000')
    if type(samples) is not int or not 1 <= samples <= MAX_SAMPLES:
        raise ValueError('samples must be an integer in 1..128')


def distribution(values):
    ordered = sorted(values)
    return {'count': len(ordered), 'minimum': ordered[0], 'maximum': ordered[-1],
            'median': statistics.median(ordered),
            'p95': ordered[math.ceil(len(ordered) * .95) - 1],
            'p95_method': 'nearest_rank'}


def elapsed(call):
    start = time.perf_counter_ns()
    result = call()
    return result, time.perf_counter_ns() - start


def vm_instructions(db, call):
    instructions = 0
    def progress():
        nonlocal instructions
        instructions += 1
        return int(instructions > 2_000_000)
    db.set_progress_handler(progress, 1)
    try:
        result = call()
    finally:
        db.set_progress_handler(None, 0)
    return result, instructions


def settings(journal):
    return {'journal_mode': journal.db.execute('PRAGMA journal_mode').fetchone()[0],
            'synchronous': journal.db.execute('PRAGMA synchronous').fetchone()[0]}


def profile_case(history, samples):
    # One additional actual reservation belongs to the VM diagnostic pass.
    limit = history + samples + 1
    options = dict(binding=BINDING, max_attempts=limit,
                   max_airtime_us=limit * ESTIMATE_US, request_clock=lambda: 1)
    with tempfile.TemporaryDirectory(prefix='meshticulum-journal-profile-') as directory:
        path = Path(directory) / 'attempts.sqlite'
        journal = AttemptJournal(path, create=True, **options)
        try:
            # Fixture setup intentionally skips per-row API fsyncs. These are
            # synthetic NOT_SENT rows, not hardware observations. Reopening
            # gives the normal startup/reconciliation path its full role.
            with journal.transaction():
                journal.db.executemany('INSERT INTO attempts VALUES (?,?,?,?,?,?)',
                    ((request, hashlib.sha256(PACKET).digest(), len(PACKET), ESTIMATE_US,
                      'NOT_SENT', 'offline_synthetic_history')
                     for request in range(1, history + 1)))
                journal.db.execute('UPDATE owner SET request=? WHERE id=1', (history,))
        finally:
            journal.close()
        journal = AttemptJournal(path, **options)
        try:
            initial = journal.snapshot()
            durability = settings(journal)
            if durability != {'journal_mode': 'wal', 'synchronous': 2}:
                raise RuntimeError('journal durability settings changed')
            if initial != dict(attempts=history,
                    reserved_estimated_airtime_us=history * ESTIMATE_US,
                    fenced=False, recovered_unknown=0):
                raise RuntimeError('synthetic history accounting mismatch')
            snapshot_times, reserve_times, settle_times = [], [], []
            # Snapshot samples see exactly the stated history size.
            for _ in range(samples):
                _, duration = elapsed(journal.snapshot)
                snapshot_times.append(duration)
            def reserve():
                return journal.reserve(PACKET, ESTIMATE_US, queue_metadata=METADATA)
            def settle(request):
                return journal.settle_queued(request, PACKET, 'NOT_SENT',
                                             'offline_fixture_no_transport', None)
            for _ in range(samples):
                request, duration = elapsed(reserve)
                reserve_times.append(duration)
                _, duration = elapsed(lambda: settle(request))
                settle_times.append(duration)
            # This separate diagnostic pass has callback overhead. Its time
            # must never be used as a latency sample or throughput figure.
            _, snapshot_steps = vm_instructions(journal.db, journal.snapshot)
            request, reserve_steps = vm_instructions(journal.db, reserve)
            _, settle_steps = vm_instructions(journal.db, lambda: settle(request))
            final = journal.snapshot()
            raw = journal.db.execute('SELECT count(*),coalesce(sum(estimated_us),0) '
                                     'FROM attempts').fetchone()
            pending = journal.db.execute("SELECT count(*) FROM attempts WHERE state='RESERVED'").fetchone()[0]
            unreleased = journal.db.execute('SELECT count(*) FROM queued_attempts WHERE released=0').fetchone()[0]
            expected = (limit, limit * ESTIMATE_US)
            if (raw != expected or (final['attempts'], final['reserved_estimated_airtime_us']) != expected
                    or final['fenced'] or pending or unreleased or settings(journal) != durability):
                raise RuntimeError('profile left inconsistent debt or unsettled work')
            return {'synthetic_history_attempts': history,
                    'durability': durability,
                    'timings_ns': {
                        'snapshot': distribution(snapshot_times),
                        'reserve_queued': distribution(reserve_times),
                        'settle_not_sent': distribution(settle_times)},
                    'timing_history_range': {
                        'snapshot': [history, history],
                        'reserve_queued': [history, history + samples - 1],
                        'settle_not_sent': [history + 1, history + samples]},
                    'vm_diagnostic': {'history_before_snapshot_and_reserve': history + samples,
                        'snapshot_instructions': snapshot_steps,
                        'reserve_queued_instructions': reserve_steps,
                        'settle_not_sent_instructions': settle_steps,
                        'timings_excluded': True},
                    'initial_state': initial, 'final_state': final,
                    'actual_reservations': samples + 1,
                    'sql_totals_match': True, 'pending_attempts': pending,
                    'unreleased_queue_records': unreleased}
        finally:
            journal.close()


def source_metadata():
    def git(*args):
        try:
            return subprocess.run(['git', '-C', str(ROOT), *args],
                text=True, capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
    revision = git('rev-parse', 'HEAD')
    dirty = git('diff', '--quiet', 'HEAD', '--', 'src/meshticulum/bench/attempts.py')
    return {'revision': revision.stdout.strip() if revision and revision.returncode == 0 else None,
            'journal_source_dirty': dirty.returncode != 0 if dirty is not None else None,
            'journal_source_sha256': hashlib.sha256(
                (ROOT / 'src/meshticulum/bench/attempts.py').read_bytes()).hexdigest(),
            'profiler_source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def profile(histories=(0, 1000, 10000), samples=25):
    validate(histories, samples)
    return {'schema': 'meshticulum.attempt-journal-profile.v1',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'source': source_metadata(),
            'environment': {'platform': platform.platform(),
                            'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version,
                            'clock': 'time.perf_counter_ns'},
            'fixture': {'history_seed': 'synthetic_NOT_SENT_rows_in_one_FULL_transaction',
                        'sampling': 'actual_AttemptJournal_API', 'samples': samples,
                        'reserved_estimate_per_attempt_us': ESTIMATE_US,
                        'storage': 'fresh_local_temporary_files',
                        'latency_progress_handler_enabled': False},
            'scope': 'Local CPU and filesystem timings only. Synthetic history is not observed radio activity. '
                     'VM diagnostics are separate from latency samples. No RF or application throughput is measured.',
            'cases': [profile_case(history, samples) for history in histories]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--histories', nargs='+', type=int, default=[0, 1000, 10000])
    parser.add_argument('--samples', type=int, default=25)
    args = parser.parse_args(argv)
    try:
        validate(args.histories, args.samples)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(profile(args.histories, args.samples), indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
