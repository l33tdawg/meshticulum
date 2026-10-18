from meshticulum.bench.admission import RadioAdmission
from meshticulum.bench.serial_protocol import RadioProfile


def test_lora_admission_can_retain_cumulative_mcu_headroom():
    waveform = (921375000, 500000, 7, 5, 0, 8, 0x42)
    admission = RadioAdmission(
        'MY', (waveform,), 10000, 180000, 1024, 120000000)
    profile = RadioProfile(
        *waveform, 90000, 384, 60000000)
    admission.check('MY', True, profile)
