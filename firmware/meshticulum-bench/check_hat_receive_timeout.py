# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Finite receive-only diagnostic for C; forbid TX opcodes and record no payload."""
from collections import deque
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path('/opt/meshticulum-bench/runtime-35a224db1beedb7f')
MANIFEST = 'dbd9a8ed23d3cafbccc854400d1658e7571a7a9601a07f2b0986f014b33ebf54'
raw = (ROOT / 'manifest.json').read_bytes()
assert hashlib.sha256(raw).hexdigest() == MANIFEST
for name, digest in json.loads(raw)['files'].items():
    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
sys.path.insert(0, str(ROOT / 'src'))
from meshticulum.bench.linux_hat import LinuxHAT
from meshticulum.bench.node import roles
from meshticulum.bench.serial_protocol import RadioProfile
from meshticulum.bench.sx1262 import SX1262

before = roles('unit-c')
result = {'schema': 'meshticulum.hat-rx-timeout-diagnostic.v1', 'services_before': before,
          'runtime_manifest_sha256': MANIFEST, 'rf_transmissions': 0, 'cycles_completed': 0}
trace = deque(maxlen=32)
with LinuxHAT() as io:
    result['host_boot_id'] = io.boot_id
    end = io.monotonic() + 12
    original = io.transfer
    def transfer(packet):
        assert io.monotonic() < end, 'diagnostic_deadline'
        assert packet[0] not in (0x83, 0xD1, 0xD2, 0x94), 'TX_or_duty_cycle_forbidden'
        value = original(packet)
        row = {'opcode': hex(packet[0]), 'bytes': len(packet), 'monotonic': io.monotonic()}
        index = {0xC0: 1, 0x1D: 3, 0x11: 1, 0x17: 1, 0x12: 1,
                 0x13: 1, 0x1E: 2, 0x14: 1}.get(packet[0])
        if index is not None: row['status'] = hex(value[index])
        if packet[0] in (0x12, 0x17): row['flags'] = value[2:].hex()
        trace.append(row)
        return value
    io.transfer = transfer
    radio = SX1262(io)
    try:
        radio.configure(RadioProfile(921375000, 125000, 8, 5, 0, 8, 0x42, 10000, 1, 1000000))
        for _ in range(3):
            radio.start_receive(authority_deadline=end)
            while radio.receiving:
                radio.poll_receive()
                io.sleep(.003)
            result['cycles_completed'] += 1
    except Exception as error:
        result['error'] = type(error).__name__ + ':' + str(error)
    finally:
        radio.close()
        result['fenced'] = radio.fenced
        result['trace'] = list(trace)
        result['attempts'] = radio.attempts
result['descriptors_released'] = all(getattr(io, field) is None for field in ('spi_fd','chip_fd','lines_fd','lock_fd'))
result['services_after'] = roles('unit-c')
assert result['services_after'] == before
print(json.dumps(result))
