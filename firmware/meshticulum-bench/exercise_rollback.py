"""Verify the retained A/B rollback in ROM, then boot only verified bench bytes.

Uses the installed, hash-pinned flash adapter and exact on-device backup. The
prior application is restored and hashed but is never intentionally started.
An error leaves a durable recovery-required receipt and requests no app reset.
This proves flash restoration, not successful operation of the prior firmware.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import time

BUNDLE = Path('/opt/meshticulum-bench/ab-flash-b4b82bc1b556d0fb')
MANIFEST_SHA256 = 'a83d2b5b4819b2b5f5a67b7f719e64f3d505c506c97a9bb045a425a3c321ccca'
INSTALLER_SHA256 = '67695315881076a9294d12af043521c3a8d262549bfc4fd97af36cf6d2a56483'


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def cycle(flash, rom, receipt, binding, image, version, application_before):
    state = receipt.state()
    require(state['phase'] == 'active_disarmed' and state['binding'] == binding,
            'exercise_requires_current_installed_receipt')
    require('rollback_exercise' not in state, 'exercise_already_recorded_review_before_retry')
    require(application_before['disarmed'] and application_before['firmware'] == version,
            'exercise_requires_disarmed_bench')
    original = receipt.read('app-before.bin')
    selected = state['plan']
    payload = image + original[len(image):]
    require(flash.sha(image) == selected['new_sha256'] and len(image) == selected['new_bytes']
            and flash.sha(original) == state['backup_sha256']
            and flash.md5(original) == selected['range_md5']
            and len(payload) == len(original) == selected['span']
            and flash.md5(payload) == state['installed_range_md5'], 'exercise_bytes_changed')
    require(rom.md5(flash.APP_OFFSET, len(payload)) == state['installed_range_md5'],
            'exercise_current_app_changed')
    outside = flash.outside_hashes(rom, len(payload))
    state['rollback_exercise'] = {
        'schema': 'meshticulum.rollback-exercise.v1', 'started_epoch_ns': time.time_ns(),
        'phase': 'prepared', 'outside_before': outside, 'application_before': application_before,
        'prior_application_reset_commands': 0, 'bench_application_reset_commands': 0,
        'bench_arm_commands': 0, 'bench_tx_commands': 0,
    }
    receipt.save(state)
    try:
        # This exact installed rollback path commits before erase and verifies
        # both the overwritten sectors and the entire pinned former image.
        state = flash.rollback(rom, receipt, binding)
        evidence = state['rollback_exercise']
        evidence.update(phase='prior_verified_in_rom', prior_verified=True,
                        prior_full_image_md5=rom.md5(flash.APP_OFFSET, selected['prior_bytes']),
                        outside_after_rollback=flash.outside_hashes(rom, len(payload)))
        require(evidence['outside_after_rollback'] == outside, 'exercise_outside_changed')
        receipt.save(state)
        state['phase'] = 'writing'
        evidence['phase'] = 'bench_writing'
        receipt.save(state)  # durable intent before overwriting the restored app
        rom.write(flash.APP_OFFSET, payload)
        require(rom.md5(flash.APP_OFFSET, len(payload)) == state['installed_range_md5']
                and rom.md5(flash.APP_OFFSET, len(image)) == flash.md5(image),
                'exercise_bench_verification_failed')
        evidence['outside_after_bench_restore'] = flash.outside_hashes(rom, len(payload))
        require(evidence['outside_after_bench_restore'] == outside, 'exercise_outside_changed')
        state['phase'], evidence['phase'] = 'flash_verified', 'bench_verified_in_rom'
        evidence['bench_verified'] = True
        receipt.save(state)
        # The only application reset occurs after bench-byte verification.
        evidence['bench_application_reset_commands'] = 1
        receipt.save(state)
        rom.reset_app()
        application = rom.probe_app(version)
        require(application['disarmed'] and application['firmware'] == version
                and application['mcu_boot'] != application_before['mcu_boot'],
                'exercise_missing_fresh_disarmed_boot')
        state['phase'], state['application'] = 'active_disarmed', application
        evidence.update(phase='complete', completed_epoch_ns=time.time_ns())
        receipt.save(state)
        return state
    except BaseException as error:
        # No fallback may boot the restored former radio firmware. Its exact
        # bytes and the installed bench image remain available for recovery.
        failed = receipt.state()
        failed['phase'] = 'recovery_required'
        failed['rollback_exercise']['error_type'] = type(error).__name__
        failed['rollback_exercise']['error'] = str(error)
        receipt.save(failed)
        raise


def trusted(path):
    for parent in (path.parent, *path.parent.parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
                'untrusted_bundle_parent')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o022 and info.st_size <= 8 * 1024 * 1024,
                'untrusted_bundle_file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            return stream.read(8 * 1024 * 1024 + 1)
    finally:
        os.close(fd)


def main():
    require(os.geteuid() == 0 and sys.flags.isolated, 'requires_isolated_root_python')
    require(len(sys.argv) == 3 and sys.argv[1] in ('unit-a', 'unit-b'), 'unit_and_host_boot_required')
    unit, expected_boot = sys.argv[1:]
    require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == expected_boot,
            'host_boot_changed')
    os.umask(0o077)
    manifest_bytes = trusted(BUNDLE / 'manifest.json')
    require(hashlib.sha256(manifest_bytes).hexdigest() == MANIFEST_SHA256
            and hashlib.sha256(trusted(BUNDLE / 'install_ab.py')).hexdigest() == INSTALLER_SHA256,
            'installed_bundle_identity_changed')
    for name, digest in json.loads(manifest_bytes)['files'].items():
        relative = Path(name)
        require(not relative.is_absolute() and '..' not in relative.parts,
                'invalid_bundle_member')
        require(hashlib.sha256(trusted(BUNDLE / relative)).hexdigest() == digest,
                'installed_bundle_member_changed')
    spec = importlib.util.spec_from_file_location('frozen_ab_installer', BUNDLE / 'install_ab.py')
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    manifest, binding = installer.verify_bundle(unit)
    path = Path('/var/lib/meshticulum-bench/flash') / (unit + '-' + MANIFEST_SHA256[:16])
    receipt = installer.flash_app.Receipt(path)
    # Reject a repeated/stale exercise before entering ROM. Cycle rechecks
    # these conditions after exclusive ownership for the actual transaction.
    current = receipt.state()
    require(current['phase'] == 'active_disarmed' and current['binding'] == binding,
            'exercise_requires_current_installed_receipt')
    require('rollback_exercise' not in current, 'exercise_already_recorded_review_before_retry')
    image = installer.trusted_file(BUNDLE / 'firmware.bin')
    with installer.PinnedROM(binding['usb_serial']) as rom:
        application_before = rom.probe_app(manifest['firmware_identity'])
        rom.connect()
        state = cycle(installer.flash_app, rom, receipt, binding, image,
                      manifest['firmware_identity'], application_before)
    print(json.dumps({'schema': 'meshticulum.hardware-rollback-exercise.v1',
        'unit': unit, 'host_boot_id': expected_boot, 'receipt': str(path / 'receipt.json'),
        'binding': binding, 'phase': state['phase'], 'application': state['application'],
        'exercise': state['rollback_exercise']}))


if __name__ == '__main__':
    main()
