"""Compile board images from a prepared local snapshot; never upload or flash."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from prepare_build import CORE, DEST, HERE, RETAINED, hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh', action='store_true', help='Retain prior manifest and copy current bench source into the isolated snapshot')
    parser.add_argument('--target', choices=['mtbench_heltec_v4', 'mtbench_xiao_s3_wio'], action='append')
    args = parser.parse_args()
    manifest_path = DEST.parent / 'FIRMWARE_SOURCE_MANIFEST.json'
    manifest = json.loads(manifest_path.read_text())
    source = DEST / 'examples/meshticulum_bench'
    if args.refresh:
        revision = 1
        while (DEST.parent / f'firmware-source-revision-{revision:02}.json').exists():
            revision += 1
        shutil.copy2(manifest_path, DEST.parent / f'firmware-source-revision-{revision:02}.json')
        for name in ('main.cpp', 'protocol.hpp'):
            shutil.copy2(HERE / name, source / name)
        manifest['bench_source'] = hashes(source)
        manifest['bench_source_digest'] = hashlib.sha256(json.dumps(manifest['bench_source'], sort_keys=True).encode()).hexdigest()
        version = 'mtb1-' + manifest['bench_source_digest'][:10]
        config = DEST / 'platformio.local.ini'
        config.write_text(config.read_text().replace(manifest['firmware_identity'], version))
        manifest['firmware_identity'] = version
        manifest['snapshot_source'] = hashes(DEST, ('.pio', '.git', '__pycache__'))
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    assert hashes(source) == manifest['bench_source'], 'snapshot source drift'
    assert {n: hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in ('main.cpp', 'protocol.hpp')} == manifest['bench_source'], 'unfrozen current source'
    assert hashes(DEST, ('.pio', '.git', '__pycache__')) == manifest['snapshot_source'], 'build configuration drift'
    environment = dict(os.environ)
    environment['PATH'] = str(RETAINED/'compiler/xtensa-esp32s3-elf/bin') + os.pathsep + environment.get('PATH', '')
    environment['PLATFORMIO_CORE_DIR'] = str(CORE)
    environment['PLATFORMIO_SETTING_ENABLE_TELEMETRY'] = 'no'
    results = []
    for target in args.target or ('mtbench_heltec_v4', 'mtbench_xiao_s3_wio'):
        number = 1
        while (DEST.parent / f'{target}-build-{number:02}.log').exists():
            number += 1
        log = DEST.parent / f'{target}-build-{number:02}.log'
        command = [str(Path.home()/'.local/bin/pio'), 'run', '-d', str(DEST), '-e', target]
        with log.open('xb') as output:
            done = subprocess.run(command, env=environment, stdout=output, stderr=subprocess.STDOUT)
        result = {'target': target, 'returncode': done.returncode, 'log': str(log), 'command': command,
                  'firmware_identity': manifest['firmware_identity'], 'flashed': False}
        if done.returncode == 0:
            output_dir = DEST/'.pio/build'/target
            result['artifacts'] = {name: {'path': str(output_dir/name), 'bytes': (output_dir/name).stat().st_size,
                'sha256': hashlib.sha256((output_dir/name).read_bytes()).hexdigest()}
                for name in ('firmware.bin', 'firmware.elf', 'partitions.bin', 'bootloader.bin')}
        results.append(result)
        print(json.dumps(result), flush=True)
    evidence = {'schema': 'meshticulum.bench-firmware-builds.v1', 'source_manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                'scope': 'Cross-compiled source only; no radio flashed or RF tested.', 'results': results}
    (DEST.parent/'FIRMWARE_BUILD_EVIDENCE.json').write_text(json.dumps(evidence, indent=2)+'\n')
    return 0 if all(r['returncode'] == 0 for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())
