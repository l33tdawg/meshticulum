"""Explicit finite Malaysia bench choices; no automatic channel/rate changes."""
from .serial_protocol import RadioProfile

MODES = {'baseline': (8, 125000), 'sf7-250': (7, 250000), 'sf7-500': (7, 500000)}


def profile_for(name):
    sf, bandwidth = MODES[name]
    return RadioProfile(921375000, bandwidth, sf, 5, 0, 8, 0x42, 90000, 256, 60000000)
