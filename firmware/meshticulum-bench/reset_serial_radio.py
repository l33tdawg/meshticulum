# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Recorded MCU-only reset between finite benchmark batches; never flash or arm."""
import array
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import termios
import time
import uuid


def main():
    unit, digest, manifest_pin = sys.argv[1:]
    assert os.geteuid() == 0 and sys.flags.isolated
    assert unit in ('unit-a', 'unit-b')
    for pin in (digest, manifest_pin):
        assert len(pin) == 64 and bytes.fromhex(pin).hex() == pin
    root = Path('/opt/meshticulum-bench/runtime-' + digest[:16])
    raw = (root / 'manifest.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == manifest_pin
    for name, pin in json.loads(raw)['files'].items():
        part = Path(name)
        assert not part.is_absolute() and '..' not in part.parts
        assert hashlib.sha256((root / part).read_bytes()).hexdigest() == pin
    sys.path.insert(0, str(root))
    from meshticulum.bench.node import _serial, roles
    before_services = roles(unit)
    host_boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    channel, worker, before = _serial(unit)
    directory = Path('/var/lib/meshticulum-bench/resets')
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    assert info.st_uid == 0 and info.st_mode & 0o777 == 0o700
    path = directory / (uuid.uuid4().hex + '.json')
    receipt = {'schema': 'meshticulum.recorded-mcu-reset.v1', 'unit': unit,
               'host_boot_id': host_boot, 'runtime_manifest_sha256': manifest_pin,
               'reason': 'New bounded benchmark batch; preserve prior run and airtime records.',
               'before': before, 'phase': 'prepared', 'epoch_before': time.time(),
               'services_before': before_services, 'flash_writes': 0, 'arm_commands': 0,
               'tx_commands': 0, 'receipt_path': str(path)}
    def save():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as out:
            json.dump(receipt, out, indent=2); out.write('\n'); out.flush(); os.fsync(out.fileno())
    save()
    if before['boot_attempts'] == 0 and before['boot_reserved_estimated_airtime_us'] == 0:
        channel.close()
        receipt.update(phase='verified_disarmed_no_reset', after=before,
                       services_after=roles(unit), epoch_after=time.time())
        assert receipt['services_after'] == before_services
        save()
        print(json.dumps(receipt))
        return
    try:
        assert before['disarmed'] and before['boot_attempts'] > 0
        # USB-JTAG serial: DTR deasserted selects the installed application;
        # one RTS pulse resets only the ESP32-S3. No bootloader or flash command.
        fcntl.ioctl(channel.fd, termios.TIOCMBIC, array.array('i', [termios.TIOCM_DTR]))
        try:
            fcntl.ioctl(channel.fd, termios.TIOCMBIS, array.array('i', [termios.TIOCM_RTS]))
            time.sleep(.1)
        finally:
            fcntl.ioctl(channel.fd, termios.TIOCMBIC, array.array('i', [termios.TIOCM_RTS]))
        receipt['phase'] = 'reset_pulsed'
        save()
    except BaseException as error:
        receipt.update(phase='failed', error=type(error).__name__ + ':' + str(error))
        save()
        raise
    finally:
        channel.close()
    until = time.monotonic() + 12
    after = None
    while time.monotonic() < until:
        time.sleep(.5)
        try:
            channel, worker, observed = _serial(unit)
            channel.close()
            if observed['mcu_boot'] != before['mcu_boot']:
                after = observed
                break
        except (OSError, RuntimeError, ValueError):
            pass
    receipt['after'] = after
    receipt['services_after'] = roles(unit)
    good = (after is not None and after['disarmed'] and after['boot_attempts'] == 0
            and after['boot_reserved_estimated_airtime_us'] == 0
            and after['usb_serial'] == before['usb_serial']
            and receipt['services_after'] == before_services
            and Path('/proc/sys/kernel/random/boot_id').read_text().strip() == host_boot)
    receipt.update(phase='verified_disarmed' if good else 'reset_verification_failed', epoch_after=time.time())
    save()
    print(json.dumps(receipt))
    if not good: raise SystemExit(1)


if __name__ == '__main__': main()
