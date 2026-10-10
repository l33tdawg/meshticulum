#!/usr/bin/env python3
"""Plan/apply/undo C's one boot overlay. Never reboots or opens the radio.

Run plan on C first and review its exact hashes. Apply requires that plan and
refuses any intervening boot, base DTB, boot loader, or environment-file change.
Rollback removes only this tool's user_overlays token, preserving other edits.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

NAME = 'meshticulum-sx1262-hf'
ENV = Path('/boot/armbianEnv.txt')
BASE = Path('/boot/dtb/rockchip/rk3588-orangepi-5-plus.dtb')
DEST = Path('/boot/overlay-user') / (NAME + '.dtbo')
STATE = Path('/var/lib/meshticulum-bench/spi-overlay')
EXPECTED_BASE = '5038ea42d49b305fe0d312829ef587d39a76565c887c0f02a45043dfa9087aae'
EXPECTED_OVERLAY = '8186506d0ae424ae9cb0a2affd89459d60753fdf988f40655b153922e4d0698b'


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def edit_environment(raw, enable):
    text = raw.decode('ascii')
    lines = text.splitlines(keepends=True)
    entries = [i for i, line in enumerate(lines) if line.startswith('user_overlays=')]
    require(len(entries) <= 1, 'duplicate_user_overlays_setting')
    tokens = [] if not entries else lines[entries[0]].strip().split('=', 1)[1].split()
    require(len(tokens) == len(set(tokens)) and all(re.fullmatch('[A-Za-z0-9_-]+', t)
                                                 for t in tokens), 'unsafe_overlay_setting')
    if (NAME in tokens) == enable:
        return raw
    if enable:
        tokens.append(NAME)
    else:
        tokens.remove(NAME)
    new_line = 'user_overlays=' + ' '.join(tokens) + '\n' if tokens else ''
    if entries:
        lines[entries[0]] = new_line
    else:
        if lines and not lines[-1].endswith('\n'):
            lines[-1] += '\n'
        lines.append(new_line)
    return ''.join(lines).encode('ascii')


def read_regular(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
            and info.st_nlink == 1 and not stat.S_IMODE(info.st_mode) & 0o022,
            'unsafe_root_file:' + str(path))
    return path.read_bytes()


def atomic_write(path, content, mode):
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def json_write(path, value):
    atomic_write(path, (json.dumps(value, indent=2, sort_keys=True) + '\n').encode(), 0o600)


def host_check():
    require(os.geteuid() == 0, 'root_required')
    require(b'rockchip,rk3588-orangepi-5-plus' in
            Path('/proc/device-tree/compatible').read_bytes().split(b'\0'), 'wrong_board')


def create_plan(overlay):
    host_check()
    overlay_data = read_regular(overlay)
    require(digest(overlay_data) == EXPECTED_OVERLAY, 'wrong_overlay_artifact')
    require(digest(BASE.read_bytes()) == EXPECTED_BASE, 'base_dtb_changed_revalidate_first')
    environment = read_regular(ENV)
    entries = environment.decode('ascii').splitlines()
    require([v for v in entries if v.startswith('fdtfile=')] ==
            ['fdtfile=rockchip/rk3588-orangepi-5-plus.dtb'], 'unexpected_fdtfile')
    require(NAME.encode() not in environment, 'overlay_already_configured')
    require(not DEST.exists(), 'overlay_destination_already_exists')
    require(Path('/proc/device-tree/spi@feb00000/status').read_bytes() == b'disabled\0',
            'spi0_already_enabled_revalidate_first')
    after = edit_environment(environment, True)
    def run(*args):
        return subprocess.check_output(args, stderr=subprocess.PIPE).decode().strip()
    with tempfile.TemporaryDirectory(prefix='mtbench-overlay-plan-') as temp:
        candidate = str(Path(temp) / 'merged.dtb')
        run('fdtoverlay', '-i', str(BASE), '-o', candidate, str(overlay))
        require(run('fdtget', '-t', 's', candidate, '/spi@feb00000', 'status') == 'okay',
                'overlay_merge_failed')
        gpio3 = run('fdtget', '-t', 'x', str(BASE), '/pinctrl/gpio@fec40000', 'phandle')
        require(run('fdtget', '-t', 'x', candidate, '/spi@feb00000', 'cs-gpios') ==
                gpio3 + ' 3 1', 'wrong_overlay_chip_select')
        require(run('fdtget', '-t', 'x', candidate, '/spi@feb00000/spidev@0',
                    'spi-max-frequency') == 'f4240', 'wrong_spi_speed')
    return {'schema': 'meshticulum.c-spi-activation.v1', 'name': NAME,
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'base_sha256': EXPECTED_BASE, 'overlay_sha256': digest(overlay_data),
            # boot.cmd is packaged source, not a file this tool writes. The
            # installed image retains its build UID; bind its bytes as evidence.
            'boot_cmd_sha256': digest(Path('/boot/boot.cmd').read_bytes()),
            'boot_scr_sha256': digest(Path('/boot/boot.scr').read_bytes()),
            'environment_before_sha256': digest(environment),
            'environment_after_sha256': digest(after),
            'environment_addition': 'user_overlays=' + NAME,
            'destination': str(DEST), 'reboot_required': True,
            'radio_opened': False, 'transmission_enabled': False}


def private_directory(path):
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
            and stat.S_IMODE(info.st_mode) == 0o700, 'unsafe_state_directory')


def apply(plan, overlay):
    require(create_plan(overlay) == plan, 'plan_stale')
    private_directory(STATE)
    lock_fd = os.open(STATE / 'activation.lock',
                     os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        receipt = STATE / 'receipt.json'
        require(not receipt.exists(), 'existing_activation_receipt')
        before = read_regular(ENV)
        require(digest(before) == plan['environment_before_sha256'], 'environment_changed')
        after = edit_environment(before, True)
        DEST.parent.mkdir(mode=0o755, exist_ok=True)
        info = DEST.parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
                and not stat.S_IMODE(info.st_mode) & 0o022, 'unsafe_overlay_directory')
        atomic_write(STATE / 'armbianEnv.before', before, 0o600)
        json_write(receipt, {'phase': 'prepared', 'plan': plan})
        # Exclusive create: never replace an unrelated overlay file.
        fd = os.open(DEST, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(read_regular(overlay))
            stream.flush()
            os.fsync(stream.fileno())
        require(digest(read_regular(ENV)) == plan['environment_before_sha256'], 'environment_changed')
        atomic_write(ENV, after, stat.S_IMODE(ENV.stat().st_mode))
        json_write(receipt, {'phase': 'enabled_pending_reboot', 'plan': plan})
        return {'phase': 'enabled_pending_reboot', 'receipt': str(receipt),
                'reboot_performed': False, 'radio_opened': False}
    finally:
        os.close(lock_fd)


def rollback():
    host_check()
    receipt = STATE / 'receipt.json'
    state = json.loads(read_regular(receipt))
    require(state['plan']['name'] == NAME and state['plan']['overlay_sha256'] == EXPECTED_OVERLAY,
            'wrong_activation_receipt')
    lock_fd = os.open(STATE / 'activation.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = read_regular(ENV)
        # If unchanged, recover exact bytes. Otherwise preserve newer unrelated edits.
        if digest(before) == state['plan']['environment_after_sha256']:
            after = read_regular(STATE / 'armbianEnv.before')
            require(digest(after) == state['plan']['environment_before_sha256'], 'backup_changed')
        else:
            after = edit_environment(before, False)
        require(read_regular(ENV) == before, 'environment_changed')
        atomic_write(ENV, after, stat.S_IMODE(ENV.stat().st_mode))
        state['phase'] = 'disabled_pending_reboot'
        json_write(receipt, state)
        return {'phase': state['phase'], 'reboot_performed': False,
                'unused_overlay_retained': str(DEST)}
    finally:
        os.close(lock_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'apply', 'rollback'])
    parser.add_argument('--overlay', type=Path)
    parser.add_argument('--plan', type=Path)
    args = parser.parse_args()
    if args.action == 'rollback':
        result = rollback()
    else:
        require(args.overlay is not None, '--overlay_required')
        if args.action == 'plan':
            result = create_plan(args.overlay)
        else:
            require(args.plan is not None, '--plan_required')
            result = apply(json.loads(read_regular(args.plan)), args.overlay)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
