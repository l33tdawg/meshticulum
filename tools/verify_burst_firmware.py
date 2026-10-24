"""Check recorded burst firmware inputs and, optionally, a supplied binary.

This checks source/provenance consistency. It does not rerun the two builds,
authenticate unpublished build records, open devices or establish RF evidence.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
APPLICATION = frozenset(('main.cpp', 'protocol.hpp', 'gfsk_profile.hpp',
                        'gfsk250_profile.hpp', 'burst_adapter.hpp',
                        'experimental/tx_burst.hpp'))
RECORD_KEYS = frozenset(('schema', 'target', 'identity', 'lock_sha256',
    'recipe_sha256', 'application', 'toolchain', 'builds', 'same_host_reproduction',
    'independent_preparations', 'firmware_bytes_identical', 'elf_bytes_identical',
    'scope', 'flashed', 'rf_qualified'))


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def artifact(value):
    require(type(value) is dict and set(value) == {'bytes', 'sha256'}
            and type(value['bytes']) is int and value['bytes'] > 0
            and sha(value['sha256']), 'invalid_firmware_artifact')


def verify(root=ROOT, *, binary=None):
    root = Path(root)
    record = json.loads((root / 'provenance/MCU_BURST_FIRMWARE.json').read_bytes())
    require(type(record) is dict and set(record) == RECORD_KEYS,
            'invalid_burst_firmware_record_shape')
    require(record['schema'] == 'meshticulum.mcu-burst-firmware.v1'
            and record['target'] == 'mtbench_heltec_v4', 'wrong_burst_firmware_record')
    firmware = root / 'firmware/meshticulum-bench'
    require(type(record['application']) is dict and record['application'].keys() == APPLICATION
            and all(sha(value) for value in record['application'].values()),
            'invalid_burst_firmware_inputs')
    actual = {name: digest(firmware / name) for name in APPLICATION}
    require(record['application'] == actual, 'burst_firmware_source_drift')
    identity = 'mtb1-' + hashlib.sha256(json.dumps(actual, sort_keys=True).encode()).hexdigest()[:10]
    require(record['identity'] == identity, 'burst_firmware_identity_drift')
    require(record['lock_sha256'] == digest(firmware / 'inputs.lock.json'),
            'burst_firmware_lock_drift')
    require(record['recipe_sha256'] == digest(firmware / 'prepare_firmware.py'),
            'burst_firmware_recipe_drift')
    lock = json.loads((firmware / 'inputs.lock.json').read_bytes())
    require(record['toolchain'] == {
        'compiler': 'xtensa-esp32s3-elf-g++',
        'compiler_version': '8.4.0',
        'compiler_release': 'crosstool-NG esp-2021r2-patch5',
        'platformio_version': lock['platformio_version'],
        'host': lock['host'],
        'source_date_epoch': lock['source_date_epoch'],
    }, 'burst_firmware_toolchain_drift')
    builds = record['builds']
    require(type(builds) is list and len(builds) == 2, 'missing_independent_firmware_builds')
    for build in builds:
        require(type(build) is dict and set(build) == {
            'source_manifest_sha256', 'build_manifest_sha256', 'firmware', 'elf'},
            'invalid_firmware_build_record')
        require(sha(build['source_manifest_sha256']) and sha(build['build_manifest_sha256']),
                'invalid_firmware_manifest_digest')
        artifact(build['firmware'])
        artifact(build['elf'])
    require(builds[0]['source_manifest_sha256'] != builds[1]['source_manifest_sha256']
            and builds[0]['build_manifest_sha256'] != builds[1]['build_manifest_sha256'],
            'reused_firmware_build_record')
    require(builds[0]['firmware'] == builds[1]['firmware']
            and builds[0]['elf'] == builds[1]['elf'], 'firmware_reproduction_changed')
    require(record['same_host_reproduction'] is True
            and type(record['independent_preparations']) is int
            and record['independent_preparations'] == 2
            and record['firmware_bytes_identical'] is True
            and record['elf_bytes_identical'] is True
            and record['flashed'] is False and record['rf_qualified'] is False,
            'invalid_firmware_evidence_scope')
    require(record['scope'] == 'Two clean same-host source builds; no cross-host, device or RF qualification.',
            'invalid_firmware_evidence_scope')
    if binary is not None:
        binary = Path(binary)
        require(binary.stat().st_size == builds[0]['firmware']['bytes']
                and digest(binary) == builds[0]['firmware']['sha256'],
                'burst_firmware_binary_changed')
    return {'identity': identity, 'target': record['target'],
            'application_inputs': len(actual), 'binary_checked': binary is not None,
            'firmware': builds[0]['firmware']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--binary', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.root, binary=args.binary), sort_keys=True))


if __name__ == '__main__':
    main()
