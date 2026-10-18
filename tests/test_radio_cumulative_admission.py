import unittest
import tempfile
from pathlib import Path

from meshticulum.bench.admission import LoraAdmission, RadioAdmission
from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.serial_protocol import RadioProfile
from meshticulum.bench.wire import WireError
from tests.test_meshticulum_bench_runtime import BOOT, Clock, provision


WAVEFORM = (921375000, 500000, 7, 5, 0, 8, 0x42)


class CumulativeLoraAdmissionTests(unittest.TestCase):
    def test_cumulative_lora_admission_accepts_preserved_debt_headroom(self):
        admission = LoraAdmission(
            'MY', (WAVEFORM,), 1000, 180000, 6586, 138114528)
        profile = RadioProfile(
            *WAVEFORM, 90000, 6586, 138114528)
        admission.check('MY', True, profile)

    def test_legacy_lora_admission_keeps_its_per_run_ceiling(self):
        with self.assertRaisesRegex(
                WireError, 'invalid_radio_admission_limits'):
            RadioAdmission(
                'MY', (WAVEFORM,), 1000, 180000, 257, 60000000)

    def test_cumulative_lora_admission_is_still_bounded(self):
        with self.assertRaisesRegex(
                WireError, 'invalid_radio_admission_limits'):
            LoraAdmission(
                'MY', (WAVEFORM,), 1000, 180000, 100001, 300000000)

    def test_authority_accepts_cumulative_lora_admission(self):
        clock = Clock()
        profile = RadioProfile(*WAVEFORM, 90000, 6586, 138114528)
        admission = LoraAdmission(
            'MY', (WAVEFORM,), 1000, 180000, 6586, 138114528)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'unit-a'
            provision(path, 'unit-a', b'x' * 32, clock, profile)
            authority = RunAuthority(
                path, radio_admission=admission, expected_unit='unit-a',
                host_boot_id=BOOT, clock=clock, owner_probe=lambda: True)
            try:
                self.assertEqual(
                    authority.profile.max_boot_attempts, 6586)
            finally:
                authority.close()


if __name__ == '__main__':
    unittest.main()
