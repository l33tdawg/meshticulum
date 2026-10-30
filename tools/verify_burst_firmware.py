"""Check immutable historical burst build evidence and an optional binary.

Current source/recipe matching is reported separately; --require-current makes
it mandatory. This does not rerun builds, authenticate unpublished manifests,
open devices or establish RF evidence. New profiling preparations have their
own inventory and identity; this six-input record does not qualify them.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_RECORD_SHA256 = 'f370c34f4f2c189c008a43ff3b67068938ac239bddf7e929dcf92f75e65eba6d'
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


def verify(root=ROOT, *, binary=None, require_current=False):
    root = Path(root)
    require(type(require_current) is bool, 'invalid_current_source_requirement')
    record_bytes = (root / 'provenance/MCU_BURST_FIRMWARE.json').read_bytes()
    record = json.loads(record_bytes)
    require(type(record) is dict and set(record) == RECORD_KEYS,
            'invalid_burst_firmware_record_shape')
    require(record['schema'] == 'meshticulum.mcu-burst-firmware.v1'
            and record['target'] == 'mtbench_heltec_v4', 'wrong_burst_firmware_record')
    firmware = root / 'firmware/meshticulum-bench'
    require(type(record['application']) is dict and record['application'].keys() == APPLICATION
            and all(sha(value) for value in record['application'].values()),
            'invalid_burst_firmware_inputs')
    identity = 'mtb1-' + hashlib.sha256(json.dumps(
        record['application'], sort_keys=True).encode()).hexdigest()[:10]
    require(record['identity'] == identity, 'burst_firmware_identity_drift')
    require(sha(record['lock_sha256']) and sha(record['recipe_sha256']),
            'invalid_burst_firmware_input_digest')
    toolchain = record['toolchain']
    require(type(toolchain) is dict and set(toolchain) == {
        'compiler', 'compiler_version', 'compiler_release', 'platformio_version',
        'host', 'source_date_epoch'}
        and toolchain['compiler'] == 'xtensa-esp32s3-elf-g++'
        and toolchain['compiler_version'] == '8.4.0'
        and toolchain['compiler_release'] == 'crosstool-NG esp-2021r2-patch5'
        and type(toolchain['platformio_version']) is str
        and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', toolchain['platformio_version'])
        and type(toolchain['host']) is str
        and re.fullmatch(r'[a-z0-9_-]{1,64}', toolchain['host'])
        and type(toolchain['source_date_epoch']) is int
        and toolchain['source_date_epoch'] > 0, 'invalid_burst_firmware_toolchain')
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
    # Keep the published historical evidence immutable, including source,
    # recipe, lock and build hashes. Consistent edits to both an inventory and
    # its short identity must not silently create replacement build evidence.
    record_digest = hashlib.sha256(record_bytes).hexdigest()
    require(record_digest == HISTORICAL_RECORD_SHA256, 'historical_firmware_record_changed')
    actual = {name: digest(firmware / name) if (firmware / name).is_file() else None
              for name in APPLICATION}
    current = dict(recorded_application_files_match=record['application'] == actual,
        lock_matches=(firmware / 'inputs.lock.json').is_file()
            and record['lock_sha256'] == digest(firmware / 'inputs.lock.json'),
        recipe_matches=(firmware / 'prepare_firmware.py').is_file()
            and record['recipe_sha256'] == digest(firmware / 'prepare_firmware.py'))
    if require_current:
        require(current['recorded_application_files_match'], 'burst_firmware_source_drift')
        require(current['lock_matches'], 'burst_firmware_lock_drift')
        require(current['recipe_matches'], 'burst_firmware_recipe_drift')
    if binary is not None:
        binary = Path(binary)
        require(binary.stat().st_size == builds[0]['firmware']['bytes']
                and digest(binary) == builds[0]['firmware']['sha256'],
                'burst_firmware_binary_changed')
    return {'identity': identity, 'target': record['target'],
            'application_inputs': len(record['application']), 'binary_checked': binary is not None,
            'firmware': builds[0]['firmware'],
            'validation_scope': 'immutable_historical_build_record',
            'historical_record_sha256': record_digest,
            'current_source_matches': all(current.values()), 'current_inputs': current}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--binary', type=Path)
    parser.add_argument('--require-current', action='store_true',
                        help='also require every recorded source, recipe and lock digest to match this tree')
    args = parser.parse_args()
    print(json.dumps(verify(args.root, binary=args.binary,
                            require_current=args.require_current), sort_keys=True))


if __name__ == '__main__':
    main()
