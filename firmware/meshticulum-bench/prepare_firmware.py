# SPDX-License-Identifier: Apache-2.0
"""Prepare and compile pinned raw-radio firmware without device or fleet access.

Fetch is explicit. Preparation verifies every archive before creating output,
retains upstream source/licenses and applies one hash-checked framework patch.
Build accepts only the two reviewed targets and never invokes upload or erase.
"""
import argparse
import configparser
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

ESP32_BULK_PATCH = dict(path='radiolib-esp32-bulk.patch',
    target='src/hal/Arduino/ArduinoHal.cpp',
    original_sha256='018c2555fd5f039e1b9159eded9c18bcdce19f72cadd6a929842d55d111160d1',
    patched_sha256='2d93a05fbf38732b9ce4c2598dfb57727388f1b7ed7a4b6ea573d6d1f79a5fb9',
    patch_sha256='c64d835bc3c2c0d7a7f0069fa82607b991457798295964e8b3f3166342e1ef3f')

ERRATUM_WRITES_PATCH = dict(path='radiolib-erratum-writes.patch',
    target='src/modules/SX126x/SX126x.cpp',
    original_sha256='ce262173efb1f4a3de6cec4894f985c4ff99885645f7385592c367605fc26f85',
    patched_sha256='d1fb795fe0263f11cda11639a38b50b1be5cbf0ec5c5c885c5bdcd19ae6ae3bb',
    patch_sha256='034249f0830fba506158c50358a27ac83b579f9d891937452004a93b6b243d1d')

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


def build_options(native_profile, heltec_spi_hz=None, *, rx_header_preserve=False, esp32_bulk_spi=False, skip_unchanged_erratum_writes=False, burst_wake_hint=False, heltec_cpu_mhz=None):
    require(type(native_profile) is bool, 'invalid_native_profile_option')
    require(heltec_spi_hz is None or type(heltec_spi_hz) is int
            and heltec_spi_hz in (2000000, 8000000), 'invalid_heltec_spi_option')
    require(type(rx_header_preserve) is bool, 'invalid_rx_header_preserve_option')
    require(type(esp32_bulk_spi) is bool, 'invalid_esp32_bulk_spi_option')
    require(type(skip_unchanged_erratum_writes) is bool, 'invalid_skip_unchanged_erratum_writes_option')
    require(type(burst_wake_hint) is bool, 'invalid_burst_wake_hint_option')
    require(heltec_cpu_mhz is None or type(heltec_cpu_mhz) is int and heltec_cpu_mhz in (80, 160), 'invalid_heltec_cpu_option')
    require(heltec_cpu_mhz != 160 or native_profile, 'cpu160_requires_native_profile')
    options = dict(native_profile=native_profile)
    if heltec_cpu_mhz == 160:
        options['heltec_cpu_mhz'] = 160
    if heltec_spi_hz is not None:
        options['heltec_spi_hz'] = heltec_spi_hz
    if rx_header_preserve:
        options['rx_header_preserve'] = True
    if esp32_bulk_spi:
        options['esp32_bulk_spi'] = True
    if skip_unchanged_erratum_writes:
        options['skip_unchanged_erratum_writes'] = True
    if burst_wake_hint:
        options['burst_wake_hint'] = True
    return options


def native_profile_config(native_profile, *, heltec_spi_hz=None, rx_header_preserve=False, esp32_bulk_spi=False, skip_unchanged_erratum_writes=False, burst_wake_hint=False, heltec_cpu_mhz=None):
    build_options(native_profile, heltec_spi_hz, rx_header_preserve=rx_header_preserve, esp32_bulk_spi=esp32_bulk_spi, skip_unchanged_erratum_writes=skip_unchanged_erratum_writes, burst_wake_hint=burst_wake_hint, heltec_cpu_mhz=heltec_cpu_mhz)
    original = ('#pragma once\n#ifdef MTBENCH_NATIVE_PROFILE\n'
            '#error "native profiling is pinned by the prepared application"\n#endif\n'
            f'#define MTBENCH_NATIVE_PROFILE {int(native_profile)}\n')
    original += ('#ifdef MTBENCH_BURST_WAKE_HINT\n'
            '#error "burst wake hint is pinned by the prepared application"\n#endif\n'
            '#ifdef MTBENCH_BURST_WAKE_HINT_CONFIGURED\n'
            '#error "burst wake configuration must come from the pinned recipe"\n#endif\n'
            '#define MTBENCH_BURST_WAKE_HINT_CONFIGURED 1\n'
            f'#define MTBENCH_BURST_WAKE_HINT {int(burst_wake_hint)}\n')
    if heltec_spi_hz is not None:
        original += ('#ifdef MTBENCH_HELTEC_SPI_HZ\n'
        '#error "Heltec SPI clock is pinned by the prepared application"\n#endif\n'
        '#ifdef RADIOLIB_DEFAULT_SPI_SETTINGS\n'
        '#error "RadioLib SPI settings must come from the pinned recipe"\n#endif\n'
        '#if defined(HELTEC_LORA_V4) && defined(SEEED_XIAO_S3)\n'
        '#error "contradictory SPI experiment targets"\n'
        '#elif defined(HELTEC_LORA_V4)\n'
        '#if !defined(MESHTICULUM_BOARD_ID) || MESHTICULUM_BOARD_ID != 1\n'
        '#error "Heltec SPI experiment requires board ID 1"\n#endif\n'
        '#elif defined(SEEED_XIAO_S3)\n'
        '#if !defined(MESHTICULUM_BOARD_ID) || MESHTICULUM_BOARD_ID != 2\n'
        '#error "XIAO baseline requires board ID 2"\n#endif\n'
        '#else\n#error "unreviewed SPI experiment target"\n#endif\n'
        f'#define MTBENCH_HELTEC_SPI_HZ {heltec_spi_hz}\n'
        '#if defined(HELTEC_LORA_V4)\n'
        '#define RADIOLIB_DEFAULT_SPI_SETTINGS \\\n'
        '    SPISettings(MTBENCH_HELTEC_SPI_HZ, MSBFIRST, SPI_MODE0)\n#endif\n')
    if rx_header_preserve:
        original += ('#ifdef MTBENCH_RX_HEADER_PRESERVE\n'
            '#error "RX header preservation is pinned by the prepared application"\n#endif\n'
            '#ifdef MTBENCH_RX_HEADER_PRESERVE_CONFIGURED\n'
            '#error "RX header configuration must come from the pinned recipe"\n#endif\n'
            '#define MTBENCH_RX_HEADER_PRESERVE_CONFIGURED 1\n'
            '#define MTBENCH_RX_HEADER_PRESERVE 1\n')
    if esp32_bulk_spi:
        require(sha(HERE / ESP32_BULK_PATCH['path']) == ESP32_BULK_PATCH['patch_sha256'],
                'esp32_bulk_patch_source_mismatch')
        original += ('#ifdef MTBENCH_ESP32_BULK_SPI\n'
            '#error "ESP32 bulk SPI is pinned by the prepared application"\n#endif\n'
            '#ifdef MTBENCH_ESP32_BULK_PATCH_SHA256\n'
            '#error "ESP32 bulk patch identity is pinned"\n#endif\n'
            '#if !defined(ARDUINO_ARCH_ESP32)\n#error "bulk SPI requires ESP32"\n#endif\n'
            '#if defined(HELTEC_LORA_V4) && defined(SEEED_XIAO_S3)\n'
            '#error "contradictory bulk SPI targets"\n'
            '#elif defined(HELTEC_LORA_V4)\n'
            '#if !defined(MESHTICULUM_BOARD_ID) || MESHTICULUM_BOARD_ID != 1\n'
            '#error "Heltec bulk SPI requires board ID 1"\n#endif\n'
            '#define MTBENCH_ESP32_BULK_SPI 1\n'
            '#elif defined(SEEED_XIAO_S3)\n'
            '#if !defined(MESHTICULUM_BOARD_ID) || MESHTICULUM_BOARD_ID != 2\n'
            '#error "XIAO baseline requires board ID 2"\n#endif\n'
            '#define MTBENCH_ESP32_BULK_SPI 0\n'
            '#else\n#error "unreviewed bulk SPI target"\n#endif\n'
            '#define MTBENCH_ESP32_BULK_PATCH_SHA256 "' + ESP32_BULK_PATCH['patch_sha256'] + '"\n')
    if skip_unchanged_erratum_writes:
        require(sha(HERE / ERRATUM_WRITES_PATCH['path']) == ERRATUM_WRITES_PATCH['patch_sha256'],
                'erratum_writes_patch_source_mismatch')
        original += ('#ifdef MTBENCH_SKIP_UNCHANGED_ERRATUM_WRITES\n'
            '#error "erratum writes is pinned by the prepared application"\n#endif\n'
            '#ifdef MTBENCH_ERRATUM_WRITES_PATCH_SHA256\n'
            '#error "erratum writes patch identity is pinned"\n#endif\n'
            '#if !defined(ARDUINO_ARCH_ESP32)\n#error "erratum writes requires ESP32"\n#endif\n'
            '#if defined(HELTEC_LORA_V4) && defined(SEEED_XIAO_S3)\n'
            '#error "contradictory erratum writes targets"\n'
            '#elif defined(HELTEC_LORA_V4)\n'
            '#if !defined(MESHTICULUM_BOARD_ID) || MESHTICULUM_BOARD_ID != 1\n'
            '#error "Heltec erratum writes requires board ID 1"\n#endif\n'
            '#define MTBENCH_SKIP_UNCHANGED_ERRATUM_WRITES 1\n'
            '#elif defined(SEEED_XIAO_S3)\n'
            '#if !defined(MESHTICULUM_BOARD_ID) || MESHTICULUM_BOARD_ID != 2\n'
            '#error "XIAO baseline requires board ID 2"\n#endif\n'
            '#define MTBENCH_SKIP_UNCHANGED_ERRATUM_WRITES 0\n'
            '#else\n#error "unreviewed erratum writes target"\n#endif\n'
            '#define MTBENCH_ERRATUM_WRITES_PATCH_SHA256 "' + ERRATUM_WRITES_PATCH['patch_sha256'] + '"\n')
    if heltec_cpu_mhz == 160:
        original += """#ifdef MTBENCH_CPU160
#error "CPU clock mode is pinned by the prepared application"
#endif
#if defined(HELTEC_LORA_V4) && defined(SEEED_XIAO_S3)
#error "contradictory CPU targets"
#elif defined(HELTEC_LORA_V4)
#if !defined(MESHTICULUM_BOARD_ID) || MESHTICULUM_BOARD_ID != 1 || !defined(ARDUINO_ARCH_ESP32)
#error "CPU160 requires ESP32 Heltec board1"
#endif
#define MTBENCH_CPU160 1
#elif defined(SEEED_XIAO_S3)
#if !defined(MESHTICULUM_BOARD_ID) || MESHTICULUM_BOARD_ID != 2
#error "XIAO baseline requires board2"
#endif
#define MTBENCH_CPU160 0
#else
#error "unreviewed CPU target"
#endif
"""
    return original



def cpu160_main(source):
    """Exact single-anchor generated variant; baseline source stays untouched."""
    edits = [
        ('static void command(const Frame& frame) {', """#if MTBENCH_CPU160
static bool cpuClockVerified=false;
static bool configureCpuClock() {
    if(!setCpuFrequencyMhz(160)) return false;
    return getCpuFrequencyMhz()==160 && getApbFrequency()==80000000;
}
#endif

static void command(const Frame& frame) {"""),
        ('    if(frame.type==NATIVE_PROFILE) {', """    if(frame.type==NATIVE_PROFILE) {
        if(frame.size==1 && frame.body[0]==0xfc) {
#if MTBENCH_CPU160
            if(armed || transmitting || resultPending || serialContainmentPending
                || burstBoot.containment_required
                || (burstQueue && (burstQueue->pending() || burstQueue->retained()))) {
                refuse(frame,mtbench::BUSY);return;
            }
            auto reply=response(NATIVE_PROFILE_REPLY,frame.request);
            reply.size=24;memcpy(reply.body,"MNC1",4);
            reply.body[4]=1; // schema1; bytes5..7 remain zero
            put32(reply.body+8,160);
            put32(reply.body+12,getCpuFrequencyMhz());
            put32(reply.body+16,getApbFrequency());
            put32(reply.body+20,cpuClockVerified ? 1 : 0);
            output.offer(reply);return;
#else
            refuse(frame,BAD_COMMAND);return;
#endif
        }
"""),
        ('    board.begin();', """    board.begin();
#if MTBENCH_CPU160
    cpuClockVerified=configureCpuClock();
    if(!cpuClockVerified) { fenced=true;serialReady=false;return; }
#endif"""),
    ]
    for before, after in edits:
        require(source.count(before) == 1, 'cpu160_main_anchor_drift')
        source = source.replace(before, after, 1)
    return source

def prepare_application(app, *, native_profile=False, heltec_spi_hz=None, rx_header_preserve=False, esp32_bulk_spi=False, skip_unchanged_erratum_writes=False, burst_wake_hint=False, heltec_cpu_mhz=None):
    """Copy exact generic app inputs and bind the build option into its identity."""
    header = native_profile_config(native_profile, heltec_spi_hz=heltec_spi_hz,
                                   rx_header_preserve=rx_header_preserve, esp32_bulk_spi=esp32_bulk_spi, skip_unchanged_erratum_writes=skip_unchanged_erratum_writes, burst_wake_hint=burst_wake_hint, heltec_cpu_mhz=heltec_cpu_mhz)
    app = Path(app)
    app.mkdir()
    for name in ('main.cpp', 'protocol.hpp', 'gfsk_profile.hpp', 'gfsk250_profile.hpp',
                 'burst_adapter.hpp', 'experimental/tx_burst.hpp', 'native_profile.hpp', 'burst_wake_hint.hpp'):
        (app / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(HERE / name, app / name)
    # Forced inclusion makes an external -D override an error, not a different
    # binary silently using the same application identity.
    (app / 'native_profile_config.hpp').write_text(header)
    if heltec_cpu_mhz == 160:
        (app / 'main.cpp').write_text(cpu160_main((app / 'main.cpp').read_text()))
    application = tree(app)
    identity = 'mtb1-' + hashlib.sha256(json.dumps(application, sort_keys=True).encode()).hexdigest()[:10]
    return application, identity


def apply_esp32_bulk_patch(vendor):
    patch=ESP32_BULK_PATCH
    require(sha(HERE / patch['path']) == patch['patch_sha256'], 'esp32_bulk_patch_source_mismatch')
    target=Path(vendor)/patch['target']
    require(sha(target) == patch['original_sha256'], 'wrong_esp32_bulk_patch_base')
    subprocess.run(['/usr/bin/patch','-p1','-F0','-i',str(HERE/patch['path'])],cwd=vendor,check=True)
    require(sha(target) == patch['patched_sha256'], 'wrong_esp32_bulk_patch_result')


def apply_erratum_writes_patch(vendor):
    patch=ERRATUM_WRITES_PATCH
    require(sha(HERE / patch['path']) == patch['patch_sha256'], 'erratum_writes_patch_source_mismatch')
    target=Path(vendor)/patch['target']
    require(sha(target) == patch['original_sha256'], 'wrong_erratum_writes_patch_base')
    subprocess.run(['/usr/bin/patch','-p1','-F0','-i',str(HERE/patch['path'])],cwd=vendor,check=True)
    require(sha(target) == patch['patched_sha256'], 'wrong_erratum_writes_patch_result')


def prepare(archive_directory, output, *, native_profile=False, heltec_spi_hz=None, rx_header_preserve=False, esp32_bulk_spi=False, skip_unchanged_erratum_writes=False, burst_wake_hint=False, heltec_cpu_mhz=None):
    options = build_options(native_profile, heltec_spi_hz, rx_header_preserve=rx_header_preserve, esp32_bulk_spi=esp32_bulk_spi, skip_unchanged_erratum_writes=skip_unchanged_erratum_writes, burst_wake_hint=burst_wake_hint, heltec_cpu_mhz=heltec_cpu_mhz)
    record = lock()
    require(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'recipe_requires_darwin_arm64')
    archives = {row['id']: check_archive(archive_directory, row) for row in record['inputs']}
    patch = record['framework_patch']
    require(sha(HERE / patch['path']) == patch['patch_sha256'], 'framework_patch_source_mismatch')
    if esp32_bulk_spi:
        require(sha(HERE / ESP32_BULK_PATCH['path']) == ESP32_BULK_PATCH['patch_sha256'], 'esp32_bulk_patch_source_mismatch')
    if skip_unchanged_erratum_writes:
        require(sha(HERE / ERRATUM_WRITES_PATCH['path']) == ERRATUM_WRITES_PATCH['patch_sha256'], 'erratum_writes_patch_source_mismatch')
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
    if esp32_bulk_spi:
        apply_esp32_bulk_patch(locations['radiolib'])
    if skip_unchanged_erratum_writes:
        apply_erratum_writes_patch(locations['radiolib'])
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
    application, identity = prepare_application(app, native_profile=native_profile,
                                                heltec_spi_hz=heltec_spi_hz,
                                                rx_header_preserve=rx_header_preserve, esp32_bulk_spi=esp32_bulk_spi, skip_unchanged_erratum_writes=skip_unchanged_erratum_writes, burst_wake_hint=burst_wake_hint, heltec_cpu_mhz=heltec_cpu_mhz)
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
        application=application, build_options=options,
        input_archives=record['inputs'], source=tree(output),
        build_scope='Source preparation; no compile, installation, RF or device authority.',
        source_notices_complete=False)
    (output / 'SOURCE.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
    return dict(output=str(output), identity=identity, prepared=True, device_actions=0)


def build(output, python, targets):
    require(type(targets) in (list, tuple) and bool(targets)
            and all(type(target) is str and target in TARGETS for target in targets),
            'unreviewed_firmware_target')
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
    require(type(options) is dict
            and 'native_profile' in options
            and set(options) <= {'native_profile', 'heltec_spi_hz', 'rx_header_preserve', 'esp32_bulk_spi', 'skip_unchanged_erratum_writes', 'burst_wake_hint', 'heltec_cpu_mhz'}
            and type(options['native_profile']) is bool
            and ('heltec_spi_hz' not in options or type(options['heltec_spi_hz']) is int
                 and options['heltec_spi_hz'] in (2000000, 8000000))
            and ('rx_header_preserve' not in options
                 or type(options['rx_header_preserve']) is bool and options['rx_header_preserve'] is True)
            and ('esp32_bulk_spi' not in options or options['esp32_bulk_spi'] is True)
            and ('skip_unchanged_erratum_writes' not in options or options['skip_unchanged_erratum_writes'] is True)
            and ('burst_wake_hint' not in options or options['burst_wake_hint'] is True)
            and ('heltec_cpu_mhz' not in options or type(options['heltec_cpu_mhz']) is int and options['heltec_cpu_mhz'] == 160 and options['native_profile']),
            'firmware_build_options_mismatch')
    app = output / 'project/examples/meshticulum_bench'
    application = tree(app)
    require(application == manifest.get('application')
            and (app / 'native_profile_config.hpp').read_text()
                == native_profile_config(options['native_profile'],
                                         heltec_spi_hz=options.get('heltec_spi_hz'),
                                         rx_header_preserve=options.get('rx_header_preserve', False),
                                         esp32_bulk_spi=options.get('esp32_bulk_spi', False),
                                         skip_unchanged_erratum_writes=options.get('skip_unchanged_erratum_writes', False),
                                         burst_wake_hint=options.get('burst_wake_hint', False),
                                         heltec_cpu_mhz=options.get('heltec_cpu_mhz'))
            and manifest.get('identity') == 'mtb1-' + hashlib.sha256(
                json.dumps(application, sort_keys=True).encode()).hexdigest()[:10],
            'firmware_application_identity_mismatch')
    if 'heltec_cpu_mhz' in options:
        require((app / 'main.cpp').read_text() == cpu160_main((HERE / 'main.cpp').read_text()), 'cpu160_main_mismatch')
    if 'esp32_bulk_spi' in options:
        require(sha(output / 'vendor/radiolib' / ESP32_BULK_PATCH['target']) == ESP32_BULK_PATCH['patched_sha256'], 'prepared_esp32_bulk_patch_mismatch')
    if 'skip_unchanged_erratum_writes' in options:
        require(sha(output / 'vendor/radiolib' / ERRATUM_WRITES_PATCH['target']) == ERRATUM_WRITES_PATCH['patched_sha256'], 'prepared_erratum_writes_patch_mismatch')
    # The wake option is pinned even when off; every build must include it.
    config = configparser.ConfigParser(interpolation=None)
    try:
        config.read(output / 'project/platformio.local.ini')
        expected_include = '-include ' + str(app / 'native_profile_config.hpp')
        forced = all(config.has_section('env:' + target)
            and [line.strip() for line in config.get('env:' + target, 'build_flags').splitlines()]
                .count(expected_include) == 1 for target in TARGETS)
    except configparser.Error:
        forced = False
    require(forced, 'firmware_burst_wake_forced_include_missing' if 'burst_wake_hint' in options else 'firmware_erratum_writes_forced_include_missing' if 'skip_unchanged_erratum_writes' in options else 'firmware_bulk_spi_forced_include_missing' if 'esp32_bulk_spi' in options else 'firmware_rx_header_forced_include_missing'
            if 'rx_header_preserve' in options else 'firmware_spi_forced_include_missing')
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
    prep.add_argument('--heltec-spi-hz', type=int, choices=(2000000, 8000000),
                      help='explicit Heltec-only SPI experiment; omission keeps the locked 2 MHz default')
    prep.add_argument('--rx-header-preserve', action='store_true',
                      help='opt in to conservative continuous-LoRa header IRQ preservation')
    prep.add_argument('--esp32-bulk-spi', action='store_true',
                      help='opt in to pinned ESP32 Heltec bulk SPI; XIAO keeps byte transfers')
    prep.add_argument('--skip-unchanged-erratum-writes', action='store_true',
                      help='opt in to Heltec fresh-read conditional erratum writes')
    prep.add_argument('--burst-wake-hint', action='store_true', help='enable bounded timer wake hints with unchanged periodic fallback')
    prep.add_argument('--heltec-cpu-mhz', type=int, choices=(80, 160), help='explicit CPU160 diagnostic mode; omission/80 preserves baseline')
    run = commands.add_parser('build')
    run.add_argument('--output-dir', required=True, type=Path)
    run.add_argument('--pio-python', required=True, type=Path)
    run.add_argument('--target', choices=TARGETS, action='append')
    args = parser.parse_args()
    if args.action == 'fetch':
        result = fetch(args.archive_dir)
    elif args.action == 'prepare':
        result = prepare(args.archive_dir, args.output_dir, native_profile=args.native_profile,
                         heltec_spi_hz=args.heltec_spi_hz,
                         rx_header_preserve=args.rx_header_preserve,
                         **({'esp32_bulk_spi': True} if args.esp32_bulk_spi else {}),
                         **({'skip_unchanged_erratum_writes': True} if args.skip_unchanged_erratum_writes else {}),
                         **({'burst_wake_hint': True} if args.burst_wake_hint else {}),
                         **({'heltec_cpu_mhz': args.heltec_cpu_mhz} if args.heltec_cpu_mhz is not None else {}))
    else:
        result = build(args.output_dir, args.pio_python, args.target or list(TARGETS))
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
