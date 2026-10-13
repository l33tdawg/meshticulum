"""Isolated finish_file memory check; no pytest runner or native transport.

The parent supplies real signed manifest/parts inputs and expected file bytes.
Only finish_file and the optional deliberate whole-file buffer are traced.
The process-wide memory limit must not include another test or runner thread.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tracemalloc

from meshticulum.resources import byte_bundle as bb


def run(directory, *, retain_whole_file=False):
    manifest = (directory / 'manifest.json').read_bytes()
    signature = (directory / 'manifest.sig.json').read_bytes()
    trust = json.loads((directory / 'trust.json').read_bytes())
    expected = (directory / 'expected.bin').read_bytes()
    expected_sha256 = hashlib.sha256(expected).hexdigest()
    n = len(expected)
    assert n in (100000, 1000000)
    document = json.loads(manifest)
    descriptor = bb.descriptor(document)
    assert (descriptor['bytes'], descriptor['sha256']) == (n, expected_sha256)
    path = directory / 'receiver'
    path.mkdir(mode=0o700)
    # Match the original source fixture's clock/lifetime without renewing it
    # during the measured operation.
    now = (100000000, 800000000)
    observed = []
    original_pread = os.pread

    def bounded(fd, count, offset):
        if os.fstat(fd).st_size == n:
            observed.append(count)
            assert count <= 23900
        return original_pread(fd, count, offset)

    with bb.Receiver(path, manifest, signature, trust, hashlib.sha256(manifest).hexdigest(),
                     expires_epoch_us=2800000000, clock=lambda: now,
                     boot_reader=lambda: 'test-host-boot') as receiver:
        # Use the exact publisher-signed chunks prepared by the parent. A new
        # builder here would replace the signature/manifest being exercised.
        for index, entry in enumerate(document['chunks']):
            receiver.accept(index, (directory / 'chunks' / (entry['sha256'] + '.json')).read_bytes())
        os.pread = bounded
        retained = None
        try:
            assert not tracemalloc.is_tracing()
            tracemalloc.start()
            try:
                if retain_whole_file:
                    retained = bytearray(expected)
                result = receiver.finish_file()
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
        finally:
            os.pread = original_pread
    assert observed and max(observed) <= 23900
    published = (path / 'bundle.bin').read_bytes()
    assert published == expected and hashlib.sha256(published).hexdigest() == expected_sha256
    assert result['complete'] and result['publisher_verified']
    assert result['read_block_bytes'] == 23900
    assert result['useful_decoded_bytes'] == n
    assert result['whole_file_sha256'] == expected_sha256
    within_memory_limit = n != 1000000 or peak < 1000000
    report = dict(source_only=True, decoded_bytes=n, content_verified=True,
                  finish_file_peak_traced_bytes=peak, largest_finalization_read=max(observed),
                  retained_whole_file_bytes=0 if retained is None else len(retained),
                  within_memory_limit=within_memory_limit)
    print(json.dumps(report, sort_keys=True))
    return 0 if within_memory_limit else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--retain-whole-file', action='store_true')
    args = parser.parse_args()
    raise SystemExit(run(args.directory, retain_whole_file=args.retain_whole_file))
