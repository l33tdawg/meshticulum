# SPDX-License-Identifier: Apache-2.0
"""Prepare and compile pinned raw-radio firmware without device or fleet access.

Fetch is explicit. Preparation verifies every archive before creating output,
retains upstream source/licenses and applies one hash-checked framework patch.
Build accepts only the two reviewed targets and never invokes upload or erase.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import urllib.request

HERE = Path(__file__).resolve().parent
TARGETS = {'mtbench_heltec_v4': ('Heltec_lora32_v4', 1, 10),
           'mtbench_xiao_s3_wio': ('Xiao_S3_WIO', 2, 22)}
DISABLED = ('ENABLE_ADVERT_ON_BOOT', 'ENABLE_PRIVATE_KEY_IMPORT', 'ENABLE_PRIVATE_KEY_EXPORT',
    'ENV_INCLUDE_GPS', 'ENV_INCLUDE_AHTX0', 'ENV_INCLUDE_BME280', 'ENV_INCLUDE_BMP280',
    'ENV_INCLUDE_SHTC3', 'ENV_INCLUDE_SHT4X', 'ENV_INCLUDE_LPS22HB', 'ENV_INCLUDE_INA3221',
    'ENV_INCLUDE_INA219', 'ENV_INCLUDE_INA226', 'ENV_INCLUDE_INA260', 'ENV_INCLUDE_MLX90614',
    'ENV_INCLUDE_VL53L0X', 'ENV_INCLUDE_BME680', 'ENV_INCLUDE_BMP085')


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as source:
        while block := source.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def lock():
    record = json.loads((HERE / 'inputs.lock.json').read_bytes())
    require(record['schema'] == 'meshticulum.raw-firmware-inputs.v1', 'wrong_firmware_lock')
    require(len({row['id'] for row in record['inputs']}) == len(record['inputs']), 'duplicate_input')
    for row in record['inputs']:
        require(Path(row['filename']).name == row['filename'] and row['url'].startswith('https://')
                and type(row['bytes']) is int and 0 < row['bytes'] <= 300000000
                and len(row['sha256']) == 64 and bytes.fromhex(row['sha256']).hex() == row['sha256'],
                'invalid_firmware_input')
    return record


def check_archive(directory, row):
    path = Path(directory) / row['filename']
    require(path.is_file() and path.stat().st_size == row['bytes']
            and sha(path) == row['sha256'], 'firmware_archive_mismatch:' + row['id'])
    return path


def fetch(directory):
    record = lock()
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    for row in record['inputs']:
        path = directory / row['filename']
        if path.exists():
            check_archive(directory, row)
            continue
        part = path.with_name(path.name + '.part')
        with urllib.request.urlopen(row['url'], timeout=30) as response, part.open('xb') as output:
            size = 0
            while block := response.read(1024 * 1024):
                size += len(block)
                require(size <= row['bytes'], 'firmware_download_too_large')
                output.write(block)
        require(part.stat().st_size == row['bytes'] and sha(part) == row['sha256'],
                'firmware_download_mismatch:' + row['id'])
        part.rename(path)
    return dict(archives=len(record['inputs']), fetched=True, device_actions=0)


def extract(archive, destination, prefix):
    staging = destination.with_name(destination.name + '.unpack')
    staging.mkdir(parents=True)
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        require(len(members) <= 60000 and sum(m.size for m in members) <= 1200000000,
                'firmware_archive_expansion_limit')
        source.extractall(staging, members=members, filter='data')
    source = staging / prefix if prefix else staging
    require(source.is_dir(), 'firmware_archive_prefix_missing')
    source.rename(destination)
    if prefix:
        shutil.rmtree(staging)


def tree(directory):
    return {str(path.relative_to(directory)): sha(path) for path in sorted(directory.rglob('*'))
            if path.is_file() and not any(p in ('.git', '.pio', '__pycache__', '.cache')
                                         for p in path.relative_to(directory).parts)
            and path.name not in ('.piopm', 'appstate.json') and path.suffix not in ('.pyc', '.pyo')}


def native_profile_config(native_profile):
    require(type(native_profile) is bool, 'invalid_native_profile_option')
    return ('#pragma once\n#ifdef MTBENCH_NATIVE_PROFILE\n'
            '#error "native profiling is pinned by the prepared application"\n#endif\n'
            f'#define MTBENCH_NATIVE_PROFILE {int(native_profile)}\n')


def prepare_application(app, *, native_profile=False):
    """Copy exact generic app inputs and bind the build option into its identity."""
    require(type(native_profile) is bool, 'invalid_native_profile_option')
    app = Path(app)
    app.mkdir()
    for name in ('main.cpp', 'protocol.hpp', 'gfsk_profile.hpp', 'gfsk250_profile.hpp',
                 'burst_adapter.hpp', 'experimental/tx_burst.hpp', 'native_profile.hpp'):
        (app / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HERE / name, app / name)
    # Forced inclusion makes an external -D override an error, not a different
    # binary silently using the same application identity.
    (app / 'native_profile_config.hpp').write_text(native_profile_config(native_profile))
    application = tree(app)
    identity = 'mtb1-' + hashlib.sha256(json.dumps(application, sort_keys=True).encode()).hexdigest()[:10]
    return application, identity


def prepare(archive_directory, output, *, native_profile=False):
    require(type(native_profile) is bool, 'invalid_native_profile_option')
    record = lock()
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'recipe_requires_darwin_arm64')
    archives = {row['id']: check_archive(archive_directory, row) for row in record['inputs']}
    patch = record['framework_patch']
    require(sha(HERE / patch['path']) == patch['patch_sha256'], 'framework_patch_source_mismatch')
    output = Path(output).resolve()
    require(not output.exists(), 'firmware_output_already_exists')
    output.mkdir(parents=True)
    locations = {}
    for row in record['inputs']:
        if row['id'] == 'meshcore':
            location = output / 'project'
        elif row['role'] == 'library':
            location = output / 'vendor' / row['id']
        elif row['id'] == 'platform':
            location = output / 'platform'
        else:
            location = output / 'packages' / row['id']
        extract(archives[row['id']], location, row['strip_prefix'])
        locations[row['id']] = location
    framework = locations['framework']
    license = record['framework_license']
    require(sha(HERE / license['path']) == license['sha256'], 'framework_license_drift')
    shutil.copy2(HERE / license['path'], framework / 'LICENSE.md')
    patched_file = framework / patch['target']
    require(sha(patched_file) == patch['original_sha256'], 'wrong_framework_patch_base')
    subprocess.run(['/usr/bin/patch', '-p1', '-F0', '-i', str(HERE / patch['path'])], cwd=framework, check=True)
    require(sha(patched_file) == patch['patched_sha256'], 'wrong_framework_patch_result')
    # Espressif's official compiler archive lacks PlatformIO wrapper metadata.
    (locations['compiler'] / 'package.json').write_text(json.dumps(dict(
        name='toolchain-xtensa-esp32s3', version='8.4.0+2021r2-patch5', system='darwin_arm64',
        license='GPL-2.0-or-later', repository=dict(type='git', url='https://github.com/espressif/crosstool-NG')),
        sort_keys=True, indent=2) + '\n')
    core = output / 'pio-core'
    (core / 'packages').mkdir(parents=True)
    for identifier, name in (('scons', 'tool-scons'), ('riscv', 'toolchain-riscv32-esp')):
        destination = core / 'packages' / name
        shutil.copytree(locations[identifier], destination)
        row = next(row for row in record['inputs'] if row['id'] == identifier)
        spec = {**row['package_spec'], 'requirements': None, 'uri': None}
        # Register the already checksum-verified package in this private core.
        # This is build-manager metadata, not a change to upstream source.
        (destination / '.piopm').write_text(json.dumps(dict(type='tool', name=name,
            version=row['version'], spec=spec), sort_keys=True) + '\n')
    project = locations['meshcore']
    app = project / 'examples/meshticulum_bench'
    application, identity = prepare_application(app, native_profile=native_profile)
    config = ['[platformio]', 'core_dir = ' + str(core), '']
    for target, (parent, board, power) in TARGETS.items():
        config += [f'[env:{target}]', f'extends = {parent}', 'platform = symlink://' + str(locations['platform']),
            'platform_packages =', '  toolchain-xtensa-esp32s3 @ symlink://' + str(locations['compiler']),
            '  framework-arduinoespressif32 @ symlink://' + str(framework),
            '  tool-esptoolpy @ symlink://' + str(locations['esptool']),
            '  toolchain-riscv32-esp @ symlink://' + str(locations['riscv']),
            'lib_deps =', 'lib_extra_dirs = ' + str(output / 'vendor'),
            f'build_src_filter = ${{{parent}.build_src_filter}}', '  +<../examples/meshticulum_bench/main.cpp>',
            f'build_flags = ${{{parent}.build_flags}}']
        config += ['  -U' + name for name in DISABLED]
        config += ['  -ffile-prefix-map=' + str(output) + '=/meshticulum-build']
        config += ['  -include ' + str(app / 'native_profile_config.hpp')]
        config += ['  -D ARDUINO_USB_MODE=1', '  -D ARDUINO_USB_CDC_ON_BOOT=1',
            f'  -D MESHTICULUM_BOARD_ID={board}', f'  -D MESHTICULUM_MAX_CHIP_DBM={power}',
            f'  \'-D MESHTICULUM_FIRMWARE="{identity}"\'', '']
    (project / 'platformio.local.ini').write_text('\n'.join(config))
    manifest = dict(schema='meshticulum.raw-firmware-prepared.v1', identity=identity,
        lock_sha256=sha(HERE / 'inputs.lock.json'), recipe_sha256=sha(Path(__file__)),
        application=application, build_options=dict(native_profile=native_profile),
        input_archives=record['inputs'], source=tree(output),
        build_scope='Source preparation; no compile, installation, RF or device authority.',
        source_notices_complete=False)
    (output / 'SOURCE.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
    return dict(output=str(output), identity=identity, prepared=True, device_actions=0)


def build(output, python, targets):
    output = Path(output).resolve()
    manifest = json.loads((output / 'SOURCE.json').read_bytes())
    require(manifest['schema'] == 'meshticulum.raw-firmware-prepared.v1'
            and manifest['lock_sha256'] == sha(HERE / 'inputs.lock.json')
            and manifest['recipe_sha256'] == sha(Path(__file__)), 'firmware_recipe_drift')
    actual = tree(output)
    actual.pop('SOURCE.json', None)
    # Prior evidence/logs are outputs, never source inputs.
    actual = {name: digest for name, digest in actual.items()
              if not name.startswith('evidence/')}
    require(actual == manifest['source'], 'prepared_firmware_source_drift')
    options = manifest.get('build_options')
    require(type(options) is dict and set(options) == {'native_profile'}
            and type(options['native_profile']) is bool, 'firmware_build_options_mismatch')
    app = output / 'project/examples/meshticulum_bench'
    application = tree(app)
    require(application == manifest.get('application')
            and (app / 'native_profile_config.hpp').read_text()
                == native_profile_config(options['native_profile'])
            and manifest.get('identity') == 'mtb1-' + hashlib.sha256(
                json.dumps(application, sort_keys=True).encode()).hexdigest()[:10],
            'firmware_application_identity_mismatch')
    version = subprocess.check_output([str(python), '-I', '-c',
        'import importlib.metadata; print(importlib.metadata.version("platformio"))'], text=True).strip()
    require(version == lock()['platformio_version'], 'wrong_platformio_version')
    evidence = output / 'evidence'
    evidence.mkdir(exist_ok=True)
    environment = dict(os.environ, PLATFORMIO_CORE_DIR=str(output / 'pio-core'),
        PLATFORMIO_SETTING_ENABLE_TELEMETRY='no', SOURCE_DATE_EPOCH=str(lock()['source_date_epoch']))
    results = []
    for target in targets:
        require(target in TARGETS, 'unreviewed_firmware_target')
        log = evidence / (target + '.log')
        with log.open('xb') as handle:
            process = subprocess.run([str(python), '-I', '-m', 'platformio', 'run', '-d',
                str(output / 'project'), '-e', target], env=environment, stdout=handle, stderr=subprocess.STDOUT)
        artifacts = {}
        if process.returncode == 0:
            for name in ('firmware.bin', 'firmware.elf', 'firmware.map', 'bootloader.bin', 'partitions.bin'):
                path = output / 'project/.pio/build' / target / name
                artifacts[name] = dict(bytes=path.stat().st_size, sha256=sha(path))
        results.append(dict(target=target, returncode=process.returncode, log_sha256=sha(log), artifacts=artifacts))
    record = dict(schema='meshticulum.raw-firmware-build.v1', source_manifest_sha256=sha(output / 'SOURCE.json'),
        platformio_version=version, host=dict(system=platform.system(), machine=platform.machine()),
        results=results, flashed=False, rf_qualified=False, notices_complete=False)
    (evidence / 'BUILD.json').write_text(json.dumps(record, sort_keys=True, indent=2) + '\n')
    require(all(row['returncode'] == 0 for row in results), 'firmware_build_failed')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    get = commands.add_parser('fetch')
    get.add_argument('--archive-dir', required=True, type=Path)
    prep = commands.add_parser('prepare')
    prep.add_argument('--archive-dir', required=True, type=Path)
    prep.add_argument('--output-dir', required=True, type=Path)
    prep.add_argument('--native-profile', action='store_true',
                      help='enable bounded boot-cumulative native timing diagnostics')
    run = commands.add_parser('build')
    run.add_argument('--output-dir', required=True, type=Path)
    run.add_argument('--pio-python', required=True, type=Path)
    run.add_argument('--target', choices=TARGETS, action='append')
    args = parser.parse_args()
    if args.action == 'fetch':
        result = fetch(args.archive_dir)
    elif args.action == 'prepare':
        result = prepare(args.archive_dir, args.output_dir, native_profile=args.native_profile)
    else:
        result = build(args.output_dir, args.pio_python, args.target or list(TARGETS))
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
