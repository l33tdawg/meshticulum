"""Immutable host-local resource deadline for fresh finite pilot admissions.

This record grants no socket, radio, identity or private-data authority. The
native provisioner supplies its independently observed unit, boot and clocks.
Every subsequent radio/IP child must use this original host deadline or tighten
it, while retaining the common original application epoch across hosts.
"""
import fcntl
import json
import os
from pathlib import Path
import re
import uuid

from .wire import require
from ..ip.journal import private


def original_context(directory, *, unit, host_boot_id, resource_sha256, expires_epoch_us, clock):
    require(unit in ('unit-a', 'unit-b', 'unit-c')
            and str(uuid.UUID(host_boot_id)) == host_boot_id, 'invalid_pilot_host')
    require(type(resource_sha256) is str and re.fullmatch('[0-9a-f]{64}', resource_sha256),
            'invalid_pilot_resource')
    require(type(expires_epoch_us) is int and 1 <= expires_epoch_us < 2**63 and callable(clock),
            'invalid_pilot_expiry')
    epoch, mono = clock()
    require(all(type(v) is int and 0 <= v < 2**63 for v in (epoch, mono)), 'invalid_pilot_clock')
    require(epoch < expires_epoch_us, 'pilot_application_expired')
    directory = Path(directory).absolute()
    private(directory.lstat(), True)
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    lock_fd = context_fd = None
    try:
        private(os.fstat(directory_fd), True)
        lock_fd = os.open('context.owner', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                          0o600, dir_fd=directory_fd)
        private(os.fstat(lock_fd))
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        binding = dict(schema='meshticulum.pilot-original-context.v1', unit=unit, host_boot_id=host_boot_id,
                       resource_sha256=resource_sha256, application_expires_epoch_us=expires_epoch_us)
        try:
            context_fd = os.open('context.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
        except FileNotFoundError:
            require(expires_epoch_us - epoch <= 180000000, 'pilot_window_too_large')
            value = dict(binding, application_deadline_monotonic_us=mono + expires_epoch_us - epoch,
                         original_epoch_us=epoch, original_monotonic_us=mono)
            context_fd = os.open('context.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 0o600, dir_fd=directory_fd)
            raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode() + b'\n'
            with os.fdopen(context_fd, 'wb', closefd=False) as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            os.fsync(directory_fd)
        else:
            private(os.fstat(context_fd))
            raw = os.read(context_fd, 2049)
            require(len(raw) <= 2048, 'pilot_context_too_large')
            value = json.loads(raw)
        fields = set(binding) | {'application_deadline_monotonic_us', 'original_epoch_us', 'original_monotonic_us'}
        require(type(value) is dict and set(value) == fields
                and all(value[k] == v for k, v in binding.items()), 'pilot_context_changed')
        require(all(type(value[k]) is int and 0 <= value[k] < 2**63
                    for k in ('original_epoch_us', 'original_monotonic_us', 'application_deadline_monotonic_us')),
                'invalid_pilot_original_clock')
        require(value['application_deadline_monotonic_us'] == value['original_monotonic_us']
                + value['application_expires_epoch_us'] - value['original_epoch_us'], 'invalid_pilot_original_deadline')
        require(epoch >= value['original_epoch_us'] and mono >= value['original_monotonic_us']
                and mono < value['application_deadline_monotonic_us'], 'pilot_original_deadline_expired_or_clock_regressed')
        return value
    finally:
        if context_fd is not None:
            os.close(context_fd)
        if lock_fd is not None:
            os.close(lock_fd)
        os.close(directory_fd)
