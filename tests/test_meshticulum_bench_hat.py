# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Source fault controls for the non-transmitting C bring-up probe."""
import pytest
import importlib.util
from pathlib import Path

from meshticulum.bench.hat_probe import Probe
from meshticulum.bench.linux_hat import HardwareError, gpio_request


class Chip:
    def __init__(self, fault=None):
        self.fault = fault
        self.time = 0.0
        self.mode = 2
        self.word = b'\x19\x24'
        self.commands = []
        self.resets = []
        self.switches = []

    def monotonic(self):
        return self.time

    def sleep(self, value):
        self.time += value

    def busy(self):
        return self.fault == 'busy'

    def reset(self, high):
        self.resets.append(high)

    def receive_switch(self, receive):
        self.switches.append(receive)

    def transfer(self, value):
        self.commands.append(value)
        if self.fault == 'io':
            raise OSError('broken transport')
        if self.fault in ('zero', 'ones'):
            return bytes([0 if self.fault == 'zero' else 255]) * len(value)
        if self.fault == 'short':
            return bytes(len(value) - 1)
        result = bytearray(len(value))
        if value[0] == 0x80:
            if self.fault != 'crystal':
                self.mode = 2 + value[1]
        elif value[0] == 0xC0:
            result[1] = self.mode << 4 | (8 if self.fault == 'command' else 0)
        elif value[:3] == b'\x1d\x07\x40':
            result[4:] = self.word
        elif value[:3] == b'\x0d\x07\x40':
            if self.fault != 'stale':
                self.word = value[3:]
        else:
            raise AssertionError('unrecognised chip command')
        return bytes(result)


def test_probe_checks_two_modes_and_changing_readback_then_restores():
    chip = Chip()
    result = Probe(chip).run()
    assert result['register_roundtrip'] and result['crystal_standby_observed']
    assert not result['rf_path_verified'] and result['rf_transmissions'] == 0
    assert chip.word == b'\x19\x24' and chip.mode == 2
    assert chip.resets == [False, True] and chip.switches == [True]
    assert {c[0] for c in chip.commands} == {0x80, 0xC0, 0x0D, 0x1D}
    assert chip.time < 0.1


@pytest.mark.parametrize('fault,reason', [
    ('busy', 'radio_busy_timeout'), ('zero', 'unexpected_chip_mode'),
    ('ones', 'unexpected_chip_mode'), ('crystal', 'unexpected_chip_mode'),
    ('command', 'chip_command_error'), ('stale', 'register_readback_mismatch'),
    ('short', 'short_spi_reply'),
])
def test_no_success_for_unresponsive_corrupted_or_stale_chip(fault, reason):
    chip = Chip(fault)
    with pytest.raises(HardwareError, match=reason):
        Probe(chip).run()
    assert all(c[0] not in (0x83, 0xD1, 0xD2) for c in chip.commands)
    assert chip.time < 0.35


def test_io_failure_propagates_and_never_transmits():
    chip = Chip('io')
    with pytest.raises(OSError):
        Probe(chip).run()
    assert all(c[0] == 0x80 for c in chip.commands)


@pytest.mark.parametrize('opcode', [0x83, 0xD1, 0xD2, 0x82])
def test_probe_cannot_issue_transmit_or_receive_commands(opcode):
    chip = Chip()
    with pytest.raises(HardwareError, match='probe_opcode_refused'):
        Probe(chip).transfer(bytes([opcode, 0, 0, 0]))
    assert not chip.commands


def test_gpio_claim_never_claims_gnss_spi_or_kernel_chip_select():
    request = gpio_request()
    assert list(request.offsets[:request.num_lines]) == [1, 0, 4, 5]
    assert request.config.flags == 4  # all input unless explicitly overridden
    attrs = request.config.attrs
    assert attrs[0].value == 8 and attrs[0].mask == 3  # only reset + RF output
    assert attrs[1].value == 3 and attrs[1].mask == 3  # reset high, RX selection


def overlay_tool():
    file = Path(__file__).resolve().parents[1] / 'firmware/meshticulum-bench/hat_overlay.py'
    spec = importlib.util.spec_from_file_location('mtbench_overlay', file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_overlay_rollback_preserves_later_unrelated_boot_edits():
    module = overlay_tool()
    before = b'console=both\nuser_overlays=camera\nfdtfile=original\n'
    after = module.edit_environment(before, True)
    later = after.replace(b'console=both', b'console=serial') + b'usbquirks=new\n'
    undone = module.edit_environment(later, False)
    assert undone == before.replace(b'console=both', b'console=serial') + b'usbquirks=new\n'
    assert module.edit_environment(undone, False) == undone


@pytest.mark.parametrize('raw', [b'user_overlays=a\nuser_overlays=b\n',
                                 b'user_overlays=a;reboot\n', b'user_overlays=a a\n'])
def test_ambiguous_boot_overlay_settings_are_refused(raw):
    with pytest.raises(RuntimeError):
        overlay_tool().edit_environment(raw, True)


def test_activation_refuses_stale_plan_before_any_state_directory(tmp_path, monkeypatch):
    module = overlay_tool()
    monkeypatch.setattr(module, 'STATE', tmp_path/'never-created')
    monkeypatch.setattr(module, 'create_plan', lambda overlay: {'boot_id': 'new-boot'})
    with pytest.raises(RuntimeError, match='plan_stale'):
        module.apply({'boot_id': 'old-boot'}, tmp_path/'not-opened.dtbo')
    assert not (tmp_path/'never-created').exists()
