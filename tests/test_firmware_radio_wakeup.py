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


def test_every_original_radio_command_service_and_serial_containment_body_is_unchanged():
    source = MAIN.read_text()
    expected = {
        'static int16_t readIrqFlags(uint32_t& flags)': '500c5d67b827509f7601db9571030e03dc1aeae9757ee18ea47d732c3712bef7',
        'static bool drainCompletedReceive(bool rearm,bool alreadyStandby=false)': 'ee4f4b6bb456fa12507d524adf68447a09d83b6fd19d9ce90c983442ddbf24b8',
        'static void command(const Frame& frame)': '31b94c310c0b9c2fad6237d098abfad04418b5887a7e503b2d7e4b6dec27ac55',
        'static void serviceRadio()': 'ac1b950452f5479ce4f610d798aeaa0c8fdd5c4d747b6e3fc3a2db523c4b0c99',
        'static void serialReadFailed()': 'c60e79c1639a43858a413f33c61c9ee1e56f511ceb1a9dfc5bdfa55af968e6a2',
    }
    for signature, pin in expected.items():
        assert hashlib.sha256(function(source, signature).encode()).hexdigest() == pin
    assert source.index('radioServiceTask=xTaskGetCurrentTaskHandle()') < source.index('radio.setDio1Action(radioWake)')
    loop = function(source, 'void loop()')
    assert loop.count('waitForRadioOrTick()') == 3 and 'delay(1)' not in loop
    assert loop.index('serviceRadio()') < loop.index('Serial.read(') < loop.index('command(frame)')
