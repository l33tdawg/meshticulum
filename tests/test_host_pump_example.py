"""Public example wake/fallback/cleanup wiring with owned model resources."""
import pytest

from tests.test_host_timing_example import CLEANUP, examples, run_model


@pytest.mark.parametrize('supported', [False, True])
def test_example_uses_optional_readiness_or_a_real_bounded_sleep(examples, run_model, monkeypatch, supported):
    invoke, calls, _ = run_model()
    example = examples[0]
    waits = []
    def wait(self, timeout):
        waits.append(timeout)
        return False if supported else None
    monkeypatch.setattr(example.SerialWorker, 'wait_for_activity', wait, raising=False)
    assert invoke()['sender_peer_receipt'] is True
    assert waits == [.005]
    assert [v for v in calls if isinstance(v, tuple)] == ([] if supported else [('sleep', .005)])
    assert calls[-len(CLEANUP):] == CLEANUP


def test_example_preserves_wait_cancellation_and_cleanup_failure(examples, run_model, monkeypatch):
    cancellation, cleanup = KeyboardInterrupt('wait cancelled'), OSError('cleanup failed')
    invoke, calls, _ = run_model(cleanup_error=cleanup)
    def wait(self, timeout):
        raise cancellation
    monkeypatch.setattr(examples[0].SerialWorker, 'wait_for_activity', wait, raising=False)
    with pytest.raises(BaseExceptionGroup) as caught:
        invoke()
    assert caught.value.exceptions == (cancellation, cleanup)
    assert calls[-len(CLEANUP):] == CLEANUP


def test_example_caps_wait_at_original_file_and_authority_deadline(examples, run_model, monkeypatch):
    invoke, calls, _ = run_model()
    monkeypatch.setattr(examples[0], 'clock', lambda: (1_000_000, 59_999_800))
    assert invoke()['sender_peer_receipt'] is True
    assert ('sleep', .0002) in calls
