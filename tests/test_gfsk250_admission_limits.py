"""Cumulative admission bounds; no hardware or firmware actions.

Runnable without pytest: PYTHONPATH=src python tests/test_gfsk250_admission_limits.py
The package's normal cryptography dependency is required by bench.wire.
"""
import unittest

from meshticulum.bench.admission import GfskAdmission, Gfsk250Admission, RadioAdmission
from meshticulum.bench.gfsk_profile import GfskProfile, GfskProfileError
from meshticulum.bench.gfsk250_profile import Gfsk250Profile
from meshticulum.bench.wire import WireError


class Gfsk250AdmissionLimits(unittest.TestCase):
    def admission(self, cls=Gfsk250Admission, **changes):
        values = dict(country='MY', frequencies_hz=(921375000,), powers_dbm=(0,),
                      min_lease_ms=1000, max_lease_ms=180000,
                      max_boot_attempts=419, max_boot_estimated_airtime_us=60000000)
        values.update(changes)
        return cls(**values)

    def profile(self, **changes):
        values = dict(frequency_hz=921375000, chip_power_dbm=0, lease_ms=90000,
                      max_boot_attempts=419, max_boot_estimated_airtime_us=60000000)
        values.update(changes)
        return Gfsk250Profile(**values)

    def test_existing_163_attempts_plus_256_new_attempts_is_admissible(self):
        cumulative = 163 + 256
        admission = self.admission(max_boot_attempts=cumulative)
        profile = self.profile(max_boot_attempts=cumulative)
        admission.check('MY', True, profile)
        self.assertEqual(admission.max_boot_attempts, cumulative)
        self.assertEqual(profile.max_boot_attempts, cumulative)
        # Admission is inert; it neither subtracts old debt nor changes a lease.
        self.assertEqual(profile.lease_ms, 90000)

    def test_upper_cumulative_record_bounds_are_accepted(self):
        admission = self.admission(max_boot_attempts=100000,
                                   max_boot_estimated_airtime_us=300000000)
        profile = self.profile(max_boot_attempts=100000,
                               max_boot_estimated_airtime_us=300000000)
        admission.check('MY', True, profile)
        self.assertEqual(Gfsk250Profile.decode(profile.encode(board_id=1), board_id=1), profile)

    def test_cumulative_upper_bounds_are_not_exceeded(self):
        for field, value in (('max_boot_attempts', 100001),
                             ('max_boot_estimated_airtime_us', 300000001),
                             ('max_boot_attempts', 0),
                             ('max_boot_estimated_airtime_us', 0)):
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(WireError, 'invalid_gfsk_admission_limits'):
                    self.admission(**{field: value})

    def test_caller_lower_cumulative_limits_still_refuse_larger_profile(self):
        admission = self.admission(max_boot_attempts=419,
                                   max_boot_estimated_airtime_us=5000000)
        for changes in (dict(max_boot_attempts=420, max_boot_estimated_airtime_us=5000000),
                        dict(max_boot_attempts=419, max_boot_estimated_airtime_us=5000001)):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(WireError, 'unsupported_admitted_gfsk250_profile'):
                    admission.check('MY', True, self.profile(**changes))

    def test_lease_bounds_are_unchanged(self):
        for changes in (dict(min_lease_ms=999), dict(max_lease_ms=180001),
                        dict(min_lease_ms=3000, max_lease_ms=2000)):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(WireError, 'invalid_gfsk_admission_limits'):
                    self.admission(**changes)
        admission = self.admission(min_lease_ms=2000, max_lease_ms=60000)
        for lease in (1000, 60001):
            with self.subTest(lease=lease):
                with self.assertRaisesRegex(WireError, 'unsupported_admitted_gfsk250_profile'):
                    admission.check('MY', True, self.profile(lease_ms=lease))

    def test_integer_limits_remain_strict(self):
        for field in ('min_lease_ms', 'max_lease_ms', 'max_boot_attempts',
                      'max_boot_estimated_airtime_us'):
            for value in (True, 1000.0, '1000'):
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(WireError, 'invalid_gfsk_admission_limits'):
                        self.admission(**{field: value})

    def test_country_frequency_power_and_antenna_checks_are_retained(self):
        admission = self.admission()
        for country, antenna in (('US', True), ('MY', False), ('MY', 1), ('MY', None)):
            with self.subTest(country=country, antenna=antenna):
                with self.assertRaisesRegex(WireError, 'gfsk_country_or_antenna_unconfirmed'):
                    admission.check(country, antenna, self.profile())
        for changes in (dict(frequency_hz=922000000), dict(chip_power_dbm=1)):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(WireError, 'unsupported_admitted_gfsk250_profile'):
                    admission.check('MY', True, self.profile(**changes))
        for country in ('my', 'M', 'MYY', '1Y', 'ＭＹ'):
            with self.subTest(country=country):
                with self.assertRaisesRegex(WireError, 'invalid_gfsk_admission_country'):
                    self.admission(country=country)

    def test_new_admission_validates_250_kbps_waveform_not_legacy_waveform(self):
        # 902.2 MHz fits the narrower 125 kbps receive bandwidth, but not the
        # 250 kbps record's stricter in-band edge. No old-record conversion.
        with self.assertRaises(GfskProfileError):
            self.admission(frequencies_hz=(902200000,))
        old = self.admission(GfskAdmission, frequencies_hz=(902200000,), max_boot_attempts=256)
        old.check('MY', True, GfskProfile(902200000, 0, 90000, 256, 60000000))
        with self.assertRaises(GfskProfileError):
            self.admission(powers_dbm=(11,))

    def test_legacy_125_kbps_admission_keeps_256_and_60_second_limits(self):
        admission = self.admission(GfskAdmission, max_boot_attempts=256)
        admission.check('MY', True, GfskProfile(921375000, 0, 90000, 256, 60000000))
        for changes in (dict(max_boot_attempts=257),
                        dict(max_boot_attempts=256, max_boot_estimated_airtime_us=60000001)):
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(WireError, 'invalid_gfsk_admission_limits'):
                    self.admission(GfskAdmission, **changes)

    def test_cross_mode_profiles_still_refuse(self):
        old = self.admission(GfskAdmission, max_boot_attempts=256)
        with self.assertRaisesRegex(WireError, 'unsupported_admitted_gfsk_profile'):
            old.check('MY', True, self.profile(max_boot_attempts=256))
        with self.assertRaisesRegex(WireError, 'unsupported_admitted_gfsk250_profile'):
            self.admission().check('MY', True,
                                  GfskProfile(921375000, 0, 90000, 256, 60000000))

    def test_lora_admission_retains_firmware_cumulative_headroom(self):
        waveform = (921375000, 500000, 7, 5, 0, 8, 0x12)
        RadioAdmission('MY', (waveform,), 1000, 180000, 257, 60000000)
        with self.assertRaisesRegex(WireError, 'invalid_radio_admission_limits'):
            RadioAdmission('MY', (waveform,), 1000, 180000, 100001, 60000000)


if __name__ == '__main__':
    unittest.main()
