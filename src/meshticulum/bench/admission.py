# SPDX-License-Identifier: Apache-2.0
"""Explicit radio limits supplied by an independently admitted native owner.

This value checks a provisioned plan; it does not authorize a device, country,
antenna, service or RF window. The consuming application's native admission must
establish those facts before constructing a transport authority.
"""
from dataclasses import dataclass
from .serial_protocol import RadioProfile
from .wire import require


@dataclass(frozen=True)
class RadioAdmission:
    country: str
    waveforms: tuple
    min_lease_ms: int
    max_lease_ms: int
    max_boot_attempts: int
    max_boot_estimated_airtime_us: int

    def __post_init__(self):
        require(type(self.country) is str and len(self.country) == 2
                and self.country.isascii() and self.country.isalpha() and self.country.isupper(),
                'invalid_radio_admission_country')
        require(type(self.waveforms) is tuple and 0 < len(self.waveforms) <= 16
                and all(type(row) is tuple and len(row) == 7
                        and all(type(value) is int for value in row) for row in self.waveforms),
                'invalid_radio_admission_waveforms')
        require(all(type(value) is int for value in (self.min_lease_ms, self.max_lease_ms,
                    self.max_boot_attempts, self.max_boot_estimated_airtime_us))
                and 1 <= self.min_lease_ms <= self.max_lease_ms <= 180000
                and 1 <= self.max_boot_attempts <= 256
                and 1 <= self.max_boot_estimated_airtime_us <= 60000000,
                'invalid_radio_admission_limits')
        for row in self.waveforms:
            RadioProfile(*row, self.max_lease_ms, self.max_boot_attempts,
                         self.max_boot_estimated_airtime_us).encode(board_id=1)

    def check(self, country, antenna_confirmed, profile):
        require(country == self.country and antenna_confirmed is True,
                'bench_country_or_antenna_unconfirmed')
        require(type(profile) is RadioProfile and (
                    profile.frequency_hz, profile.bandwidth_hz, profile.spreading_factor,
                    profile.coding_rate, profile.chip_power_dbm, profile.preamble, profile.sync_word)
                in self.waveforms
                and self.min_lease_ms <= profile.lease_ms <= self.max_lease_ms
                and profile.max_boot_attempts <= self.max_boot_attempts
                and profile.max_boot_estimated_airtime_us <= self.max_boot_estimated_airtime_us,
                'unsupported_admitted_bench_profile')
