"""Persistent original deadline survives route changes and clock movement."""
import pytest
from meshticulum.bench.pilot_context import original_context
from meshticulum.bench.wire import WireError
from tests.test_meshticulum_bench_runtime import BOOT, Clock


def context(directory, clock, expires, **changes):
    args = dict(unit='unit-c', host_boot_id=BOOT, resource_sha256='a' * 64,
                expires_epoch_us=expires, clock=clock)
    args.update(changes)
    return original_context(directory, **args)


def test_repeated_admission_keeps_exact_original_monotonic_deadline(tmp_path):
    directory = tmp_path / 'context'
    directory.mkdir(mode=0o700)
    clock = Clock()
    expires = clock()[0] + 60000000
    first = context(directory, clock, expires)
    clock.advance(10000000)
    assert context(directory, clock, expires) == first
    assert first['application_deadline_monotonic_us'] == first['original_monotonic_us'] + 60000000


@pytest.mark.parametrize('changes', [{'unit': 'unit-b'}, {'resource_sha256': 'b' * 64},
    {'host_boot_id': '31f9c178-6090-40d9-9359-ecf1c7b950b1'}])
def test_another_subject_cannot_rebind_original_context(tmp_path, changes):
    directory = tmp_path / 'context'
    directory.mkdir(mode=0o700)
    clock = Clock(); expires = clock()[0] + 60000000
    context(directory, clock, expires)
    with pytest.raises(WireError, match='pilot_context_changed'):
        context(directory, clock, expires, **changes)


def test_later_expiry_cannot_renew_context_even_with_a_new_path(tmp_path):
    directory = tmp_path / 'context'
    directory.mkdir(mode=0o700)
    clock = Clock(); expires = clock()[0] + 60000000
    first = context(directory, clock, expires)
    with pytest.raises(WireError, match='pilot_context_changed'):
        context(directory, clock, expires + 10000000)
    clock.advance(60000000)
    with pytest.raises(WireError, match='pilot_application_expired'):
        context(directory, clock, expires)


def test_backward_epoch_cannot_renew_expired_original_monotonic_time(tmp_path):
    directory = tmp_path / 'context'
    directory.mkdir(mode=0o700)
    clock = Clock(); epoch, mono = clock(); expires = epoch + 60000000
    context(directory, clock, expires)
    # The wall clock remains inside the original epoch window, but the same
    # boot's monotonic time has passed its original deadline.
    with pytest.raises(WireError, match='pilot_original_deadline_expired_or_clock_regressed'):
        context(directory, lambda: (epoch + 1000000, mono + 60000000), expires)
