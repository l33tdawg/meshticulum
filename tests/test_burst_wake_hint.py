"""Compile actual wake helper with deterministic timer failure/race delivery."""
from pathlib import Path
import subprocess
import pytest
from tests.test_firmware_recipe import recipe

def test_timer_lifecycle_and_stale_hint_never_dispatches(tmp_path):
 root=Path(__file__).parents[1];inc=tmp_path/'inc';(inc/'freertos').mkdir(parents=True)
 (inc/'freertos/FreeRTOS.h').write_text('#pragma once\nusing TaskHandle_t=void*;\n')
 (inc/'freertos/task.h').write_text('#pragma once\n#include "FreeRTOS.h"\nextern int notifications;inline void xTaskNotifyGive(TaskHandle_t){++notifications;}\n')
 (inc/'esp_timer.h').write_text('''#pragma once
#include <stdint.h>
using esp_err_t=int;using esp_timer_handle_t=void*;
constexpr int ESP_OK=0,ESP_ERR_INVALID_STATE=1,ESP_TIMER_TASK=0;
struct esp_timer_create_args_t{void(*callback)(void*);void*arg;int dispatch_method;const char*name;bool skip_unhandled_events;};
extern esp_timer_create_args_t saved;extern int creates,starts,stops,create_result,start_result,stop_result;extern uint64_t delay;
inline int esp_timer_create(const esp_timer_create_args_t*a,esp_timer_handle_t*h){++creates;saved=*a;*h=(void*)1;return create_result;}
inline int esp_timer_start_once(esp_timer_handle_t,uint64_t d){++starts;delay=d;return start_result;}
inline int esp_timer_stop(esp_timer_handle_t){++stops;return stop_result;}
''')
 cpp=tmp_path/'test.cpp';cpp.write_text('''#include <cassert>
#include "burst_wake_hint.hpp"
esp_timer_create_args_t saved{};int notifications=0,creates=0,starts=0,stops=0,create_result=0,start_result=0,stop_result=0;uint64_t delay=0;
using mtbench::experimental::BurstWakeHint;
void reset(){creates=starts=stops=notifications=create_result=start_result=stop_result=0;}
int main(){
 {reset();BurstWakeHint h;h.initialize((void*)9);h.initialize((void*)8);assert(creates==1&&saved.arg==(void*)9);
 h.update(100,5100);assert(starts==1&&delay==5000);h.update(200,5100);assert(starts==1);
 auto cb=saved.callback;auto arg=saved.arg;h.update(300,0);assert(stops==1);cb(arg);assert(notifications==1);
 // No timer callback invokes queue/dispatch; stale cancellation wakes only.
 h.update(400,5400);assert(starts==2);stop_result=ESP_ERR_INVALID_STATE;h.update(5500,5400);assert(stops==2);
 h.update(6000,106000);assert(delay==100000);cb(arg);assert(notifications==2);
 }
 {reset();BurstWakeHint h;create_result=2;h.initialize((void*)9);h.update(0,5);assert(starts==0);h.initialize((void*)9);assert(creates==1);}
 {reset();BurstWakeHint h;h.initialize(nullptr);h.update(0,5);assert(creates==0&&starts==0);}
 {reset();BurstWakeHint h;h.initialize((void*)9);start_result=2;h.update(0,5);h.update(0,6);assert(starts==1);}
 {reset();BurstWakeHint h;h.initialize((void*)9);h.update(0,5);stop_result=2;h.update(1,6);h.update(2,7);assert(starts==1&&stops==1);saved.callback(saved.arg);assert(notifications==1);}
}
''')
 binary=tmp_path/'test'
 subprocess.run(['clang++','-std=c++17','-fsanitize=address,undefined','-I'+str(inc),'-I'+str(root/'firmware/meshticulum-bench'),str(cpp),'-o',str(binary)],check=True,capture_output=True,timeout=30)
 subprocess.run([str(binary)],check=True,capture_output=True,timeout=10)

@pytest.mark.parametrize('value',[0,1,None,'true'])
def test_option_refuses_before_output(recipe,tmp_path,value):
 with pytest.raises(RuntimeError,match='invalid_burst_wake_hint_option'):
  recipe.prepare_application(tmp_path/'app',burst_wake_hint=value)
 assert not (tmp_path/'app').exists()

def test_optin_identity_and_unchanged_guard_path(recipe,tmp_path):
 off,old=recipe.prepare_application(tmp_path/'off');on,new=recipe.prepare_application(tmp_path/'on',burst_wake_hint=True)
 assert old!=new and {k for k in on if on[k]!=off[k]}=={'native_profile_config.hpp'}
 main=(recipe.HERE/'main.cpp').read_text()
 assert 'ulTaskNotifyTake(pdTRUE,1)' in main
 assert main.index('serviceRadio(); // Drain')<main.index('burstService(true);')
 burst=(recipe.HERE/'burst_adapter.hpp').read_text()
 assert 'nowUs()<burstNextStartUs' in burst and 'const uint64_t next=now+delay;' in burst

@pytest.mark.parametrize('enabled',[False,True])
def test_external_override_is_compile_error(recipe,tmp_path,enabled):
 h=tmp_path/'config.hpp';h.write_text(recipe.native_profile_config(False,burst_wake_hint=enabled))
 result=subprocess.run(['clang++','-x','c++','-fsyntax-only','-DMTBENCH_BURST_WAKE_HINT=0','-include',str(h),'-'],input='',capture_output=True,text=True)
 assert result.returncode!=0 and 'pinned' in result.stderr

def test_cli_enables_only_explicit_option(recipe,monkeypatch,capsys):
 import sys
 seen={};monkeypatch.setattr(recipe,'prepare',lambda *a,**k:seen.update(k) or {})
 for enabled in (False,True):
  seen.clear();monkeypatch.setattr(sys,'argv',['prepare_firmware.py','prepare','--archive-dir','unused','--output-dir','unused']+(['--burst-wake-hint'] if enabled else []))
  recipe.main();assert seen.get('burst_wake_hint',False) is enabled

@pytest.mark.parametrize('target',['mtbench_heltec_v4','mtbench_xiao_s3_wio'])
def test_all_options_off_still_requires_forced_include(recipe,tmp_path,monkeypatch,target):
 import json
 out=tmp_path/'out';app=out/'project/examples/meshticulum_bench';app.parent.mkdir(parents=True)
 application,identity=recipe.prepare_application(app)
 config=''.join('[env:'+t+']\nbuild_flags=\n '+('-missing ' if t==target else '-include ')+str(app/'native_profile_config.hpp')+'\n' for t in recipe.TARGETS)
 (out/'project/platformio.local.ini').write_text(config)
 record=dict(schema='meshticulum.raw-firmware-prepared.v1',lock_sha256=recipe.sha(recipe.HERE/'inputs.lock.json'),recipe_sha256=recipe.sha(recipe.__file__),application=application,identity=identity,build_options={'native_profile':False},source=recipe.tree(out))
 (out/'SOURCE.json').write_text(json.dumps(record))
 monkeypatch.setattr(recipe.subprocess,'check_output',lambda *a,**k:pytest.fail('compiler invoked'))
 with pytest.raises(RuntimeError,match='forced_include_missing'):recipe.build(out,'unused',list(recipe.TARGETS))
