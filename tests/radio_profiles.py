# Test fixture only; no physical authority.
"""Explicit finite Malaysia bench choices; no automatic channel/rate changes."""
from meshticulum.bench.serial_protocol import RadioProfile

MODES = {'baseline': (8, 125000), 'sf7-250': (7, 250000), 'sf7-500': (7, 500000)}


def profile_for(name):
    sf, bandwidth = MODES[name]
    return RadioProfile(921375000, bandwidth, sf, 5, 0, 8, 0x42, 90000, 256, 60000000)

from meshticulum.bench.admission import RadioAdmission
SOURCE_ADMISSION = RadioAdmission('MY', tuple((921375000, bw, sf, 5, 0, 8, 0x42)
    for sf, bw in MODES.values()), 10000, 180000, 256, 60000000)
