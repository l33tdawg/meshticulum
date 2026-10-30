"""Wake scheduling controls; real pinned target builds are separate evidence."""
import hashlib
from pathlib import Path
import shutil
import subprocess

import pytest

MAIN = Path(__file__).resolve().parents[1] / 'firmware/meshticulum-bench/main.cpp'


def function(source, signature):
    start = source.index(signature); opening = source.index('{', start); depth = 1; end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}'); end += 1
    return source[start:end]


def test_real_wake_functions_never_issue_radio_actions_and_retain_one_tick_fallback(tmp_path):
    compiler = shutil.which('c++')
    if compiler is None:pytest.skip('host C++ compiler unavailable; no target qualification implied')
    source = MAIN.read_text()
    wake = function(source, 'static void IRAM_ATTR radioWake()')
    wait = function(source, 'static void waitForRadioOrTick()')
    stub = r'''
#include <cassert>
#include <cstdint>
#define IRAM_ATTR
using TaskHandle_t=void*;
using BaseType_t=int;
constexpr int pdFALSE=0,pdTRUE=1;
static TaskHandle_t radioServiceTask=nullptr;
static unsigned notifications=0,gives=0,yields=0,takes=0,periodic=0,delays=0;
static bool higher=true;
void vTaskNotifyGiveFromISR(TaskHandle_t task,BaseType_t* woke) {
 assert(task==radioServiceTask && task!=nullptr);++gives;++notifications;*woke=higher;
}
void portYIELD_FROM_ISR() {++yields;}
uint32_t ulTaskNotifyTake(BaseType_t clear,unsigned ticks) {
 assert(clear==pdTRUE && ticks==1);++takes;
 unsigned n=notifications;notifications=0;if(!n)++periodic;return n;
}
void delay(unsigned ms) {assert(ms==1);++delays;}
'''
    checks = r'''
int main() {
 radioWake();waitForRadioOrTick();assert(gives==0 && yields==0 && delays==1 && takes==0);
 radioServiceTask=reinterpret_cast<void*>(1);waitForRadioOrTick();assert(takes==1 && periodic==1);
 radioWake();assert(gives==1 && notifications==1 && yields==1);
 waitForRadioOrTick();assert(takes==2 && periodic==1 && notifications==0);
 higher=false;radioWake();assert(gives==2 && yields==1);
 waitForRadioOrTick();assert(takes==3 && periodic==1);
 waitForRadioOrTick();assert(takes==4 && periodic==2);
}
'''
    cpp = tmp_path / 'wake.cpp'; exe = tmp_path / 'wake'
    cpp.write_text(stub + wake + '\n' + wait + checks)
    subprocess.run([compiler, '-std=c++17', '-Wall', '-Werror', str(cpp), '-o', str(exe)], check=True, timeout=20)
    subprocess.run([str(exe)], check=True, timeout=5)


def test_reviewed_radio_helpers_and_burst_integration_preserve_wake_service_order():
    source = MAIN.read_text()
    expected = {
        'static int16_t readIrqFlags(uint32_t& flags)': '500c5d67b827509f7601db9571030e03dc1aeae9757ee18ea47d732c3712bef7',
        'static bool drainCompletedReceive(bool rearm,bool alreadyStandby=false)': 'ac6b66a6810ef7736c996a1e729baa9175088df8935f7010335c79f8482b85a7',
        'static void command(const Frame& frame)': '4602ccc7495950349dc7c4ebe53e90cf1d557334ea8b7c819eb21f69c0da9a91',
        'static void serviceRadio()': '9e32a33e4dda88020b54b8255f40a190c0748899729cebfd5c9dcb8e71837d2f',
        'static void serialReadFailed()': '632e5585b19508ba0d49c431e340bd29253e4bcc5292a28f1f2afcfa9fcb9108',
    }
    for signature, pin in expected.items():
        assert hashlib.sha256(function(source, signature).encode()).hexdigest() == pin
    assert source.index('radioServiceTask=xTaskGetCurrentTaskHandle()') < source.index('radio.setDio1Action(radioWake)')
    loop = function(source, 'void loop()')
    assert loop.count('waitForRadioOrTick()') == 3 and 'delay(1)' not in loop
    assert loop.index('serviceRadio()') < loop.index('Serial.read(') < loop.index('command(frame)')
    assert loop.index('command(frame)') < loop.rindex('burstService(true)')
