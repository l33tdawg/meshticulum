"""Freeze an isolated bench build using the retained, locally patched r7 BSP.

Does not flash, fetch, alter the retained firmware source or erase partitions.
The donor is a reconstructed MeshCore-derived BSP, not a pristine upstream tree.
Its original license files and dependencies remain in the build snapshot.
"""
from pathlib import Path
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parents[2]
RETAINED = ROOT / 'work/ab-serial-20260927'
SOURCE = RETAINED / 'firmware-source'
DEST = ROOT / 'work/meshticulum-abc-deployment-20261009/firmware-build'
HERE = Path(__file__).resolve().parent
CORE = DEST.parent / 'firmware-tool-core'
VENDOR = DEST.parent / 'firmware-vendor-libs'


def prepare_core():
    # Keep package-manager writes away from the retained build's core.
    shutil.copytree(RETAINED / 'pio-core/platforms', CORE / 'platforms')
    for name in ('tool-esptoolpy', 'toolchain-riscv32-esp'):
        shutil.copytree(RETAINED / 'pio-core/packages' / name, CORE / 'packages' / name)
    shutil.copytree(Path.home() / '.platformio/packages/tool-scons', CORE / 'packages/tool-scons')
    config = (DEST / 'platformio.ini').read_text()
    config = config.replace(str(RETAINED / 'pio-core'), str(CORE))
    (DEST / 'platformio.ini').write_text(config)


def hashes(root, excluded=()):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()
            and not any(part in excluded for part in p.relative_to(root).parts)}


def main():
    if DEST.exists():
        raise SystemExit('Build snapshot already exists; retain it and use a new evidence directory.')
    base = hashes(SOURCE, ('.pio', '.git'))
    shutil.copytree(SOURCE, DEST, ignore=shutil.ignore_patterns('.pio', '.git'))
    prepare_core()
    shutil.copytree(SOURCE / '.pio/libdeps/lantern_heltec_usb_r7', VENDOR)
    shutil.copy2(HERE / 'test_custom_runner.py', DEST / 'test/test_custom_runner.py')
    app = DEST / 'examples/meshticulum_bench'
    app.mkdir()
    for name in ('main.cpp', 'protocol.hpp'):
        shutil.copy2(HERE / name, app / name)
    own = hashes(app)
    digest = hashlib.sha256(json.dumps(own, sort_keys=True).encode()).hexdigest()
    shared = '''platform_packages =
  toolchain-xtensa-esp32s3 @ symlink://{compiler}
  framework-arduinoespressif32 @ symlink://{framework}
lib_deps =
lib_extra_dirs = {vendor}
test_framework = custom
'''.format(compiler=RETAINED / 'compiler/xtensa-esp32s3-elf',
           framework=RETAINED / 'framework-arduinoespressif32', vendor=VENDOR)
    sections = []
    for env, parent, variant, board_id, max_dbm in (
            ('mtbench_heltec_v4', 'Heltec_lora32_v4', 'heltec_v4', 1, 10),
            ('mtbench_xiao_s3_wio', 'Xiao_S3_WIO', 'xiao_s3_wio', 2, 22)):
        sections.append(f'''[env:{env}]
extends = {parent}
{shared}build_src_filter = ${{{parent}.build_src_filter}}
  +<../examples/meshticulum_bench/main.cpp>
build_flags = ${{{parent}.build_flags}}
  -UENABLE_ADVERT_ON_BOOT
  -UENABLE_PRIVATE_KEY_IMPORT
  -UENABLE_PRIVATE_KEY_EXPORT
  -UENV_INCLUDE_GPS
  -UENV_INCLUDE_AHTX0
  -UENV_INCLUDE_BME280
  -UENV_INCLUDE_BMP280
  -UENV_INCLUDE_SHTC3
  -UENV_INCLUDE_SHT4X
  -UENV_INCLUDE_LPS22HB
  -UENV_INCLUDE_INA3221
  -UENV_INCLUDE_INA219
  -UENV_INCLUDE_INA226
  -UENV_INCLUDE_INA260
  -UENV_INCLUDE_MLX90614
  -UENV_INCLUDE_VL53L0X
  -UENV_INCLUDE_BME680
  -UENV_INCLUDE_BMP085
  -D ARDUINO_USB_MODE=1
  -D ARDUINO_USB_CDC_ON_BOOT=1
  -D MESHTICULUM_BOARD_ID={board_id}
  -D MESHTICULUM_MAX_CHIP_DBM={max_dbm}
  '-D MESHTICULUM_FIRMWARE="mtb1-{digest[:10]}"'
''')
    (DEST / 'platformio.local.ini').write_text('\n'.join(sections))
    manifest = {
        'schema': 'meshticulum.bench-build-source.v1',
        'status': 'prepared_not_compiled_not_flashed',
        'donor': str(SOURCE),
        'provenance': 'Retained reconstructed r7 source derived from MeshCore d9296435 with local patches; not asserted byte-identical to upstream.',
        'upstream_license': 'license.txt: MIT, Copyright (c) 2025 Scott Powell / rippleradios.com',
        'preserved_donor_files': base,
        'bench_source': own,
        'bench_source_digest': digest,
        'firmware_identity': 'mtb1-' + digest[:10],
        'snapshot_source': hashes(DEST, ('.pio', '.git')),
        'dependency_files': hashes(VENDOR, ('.git',)),
        'tool_core': str(CORE),
        'toolchain': str(RETAINED / 'compiler/xtensa-esp32s3-elf'),
        'framework': str(RETAINED / 'framework-arduinoespressif32'),
        'deployment': 'No flash command. Application-only flashing requires actual board, current partition table, rollback backup, exclusive owner and host admission. Never use a merged image to erase existing NVS/identity.',
    }
    (DEST.parent / 'FIRMWARE_SOURCE_MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'build_directory': str(DEST), 'identity': manifest['firmware_identity']}))


if __name__ == '__main__':
    main()
