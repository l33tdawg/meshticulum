"""Real firmware with deterministic IRQ races; no target build or device I/O."""
from pathlib import Path
import shutil
import subprocess

import pytest

SDK = Path(__file__).resolve().parents[1]


def replace_once(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new)


@pytest.fixture(scope='module')
def binaries(tmp_path_factory):
    outputs = {}
    for candidate in (0, 1):
        root = tmp_path_factory.mktemp(f'header-design-{candidate}')
        shutil.copytree(SDK / 'firmware/meshticulum-bench', root / 'firmware/meshticulum-bench')
        directory = root / 'tests/meshticulum_bench_native'
        shutil.copytree(SDK / 'tests/meshticulum_bench_native', directory)
        target = (directory / 'target.h').read_text()
        target += '\n#define RADIOLIB_SX126X_IRQ_PREAMBLE_DETECTED 0x04\n'
        target = replace_once(target, '    bool warm=false;', '''    uint32_t standbys=0,last_clear_mask=0,irq_after_read=0,irq_on_clear=0;
    bool warm=false;''')
        target = replace_once(target, 'standby() { receiving=false;',
                              'standby() { standbys++;receiving=false;')
        target = replace_once(target,
            'int16_t clearIrqFlags(uint32_t) { if(!clear_error) flags=0;',
            'int16_t clearIrqFlags(uint32_t mask) { last_clear_mask=mask;flags|=irq_on_clear;irq_on_clear=0;if(!clear_error) flags&=~mask;')
        target = replace_once(target,
            'int16_t startReceive() { receive_starts++; flags=irq_on_receive;irq_on_receive=0;receiving=true;fake_time+=receive_advance_us;return receive_error; }',
            'int16_t startReceive() { receive_starts++;auto err=standby();if(err)return err;err=clearIrqFlags(RADIOLIB_SX126X_IRQ_ALL);if(err)return err;flags=irq_on_receive;irq_on_receive=0;receiving=!receive_error;fake_time+=receive_advance_us;return receive_error; }')
        target = replace_once(target, 'data[0]=flags>>8; data[1]=flags;fake_time+=irq_advance_us;',
            'data[0]=flags>>8; data[1]=flags;flags|=irq_after_read;irq_after_read=0;fake_time+=irq_advance_us;')
        (directory / 'target.h').write_text(target)
        for profile in (0, 1):
            output = root / f'controls-{profile}'
            subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-g', f'-DCANDIDATE={candidate}',
                            *(['-DMTBENCH_RX_HEADER_PRESERVE=1','-DMTBENCH_RX_HEADER_PRESERVE_CONFIGURED=1'] if candidate else []),
                            f'-DMTBENCH_NATIVE_PROFILE={profile}', '-I', str(directory),
                            str(directory / 'rx_header_preserve.cpp'), '-o', str(output)],
                           check=True, capture_output=True, text=True)
            outputs[candidate, profile] = output
    return outputs


CASES = ('header', 'valid_initial', 'done_initial', 'crc', 'timeout', 'unknown',
         'gfsk', 'preamble', 'late_valid', 'late_done', 'late_crc', 'late_unknown',
         'clear_valid', 'clear_done', 'clear_crc', 'clear_failure', 'expired', 'irq_failure',
         'late_done_invalid', 'clear_done_invalid', 'shadow_restart', 'shadow_restart_failure')


@pytest.mark.parametrize('candidate', (0, 1))
@pytest.mark.parametrize('case', CASES)
def test_real_service_branch_with_driver_interleavings(binaries, candidate, case):
    outputs = [subprocess.run([str(binaries[candidate, profile]), case], check=True,
                              capture_output=True, timeout=10).stdout for profile in (0, 1)]
    assert outputs[0] == outputs[1]


@pytest.mark.parametrize('case', ('valid_initial', 'done_initial', 'crc', 'timeout',
                                 'unknown', 'gfsk', 'expired', 'irq_failure'))
def test_legacy_non_header_only_paths_remain_identical(binaries, case):
    outputs = [subprocess.run([str(binaries[candidate, 1]), case], check=True,
                              capture_output=True, timeout=10).stdout for candidate in (0, 1)]
    assert outputs[0] == outputs[1]
