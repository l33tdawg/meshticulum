"""Source recipe identity, target gating and fail-closed vendor patch controls."""
import hashlib,json,subprocess
from pathlib import Path
import pytest
from tests.test_firmware_recipe import recipe

@pytest.mark.parametrize('v',[0,1,None,'true',[],{}])
def test_strict_before_output(recipe,tmp_path,v):
 with pytest.raises(RuntimeError,match='invalid_esp32_bulk_spi_option'):
  recipe.prepare_application(tmp_path/'app',esp32_bulk_spi=v)
 assert not (tmp_path/'app').exists()

def test_default_bytes_and_identity(recipe,tmp_path):
 old,oldid=recipe.prepare_application(tmp_path/'old')
 off,offid=recipe.prepare_application(tmp_path/'off',esp32_bulk_spi=False)
 on,onid=recipe.prepare_application(tmp_path/'on',esp32_bulk_spi=True)
 assert old==off and oldid==offid and onid!=oldid
 assert recipe.build_options(False)=={'native_profile':False}
 assert recipe.build_options(False,esp32_bulk_spi=True)=={'native_profile':False,'esp32_bulk_spi':True}
 assert {k for k in on if on[k]!=old[k]}=={'native_profile_config.hpp'}
 assert recipe.ESP32_BULK_PATCH['patch_sha256'] in (tmp_path/'on/native_profile_config.hpp').read_text()

@pytest.mark.parametrize('defs,expected',[(('ARDUINO_ARCH_ESP32','HELTEC_LORA_V4','MESHTICULUM_BOARD_ID=1'),1),(('ARDUINO_ARCH_ESP32','SEEED_XIAO_S3','MESHTICULUM_BOARD_ID=2'),0),(('HELTEC_LORA_V4','MESHTICULUM_BOARD_ID=1'),None),(('ARDUINO_ARCH_ESP32','HELTEC_LORA_V4','MESHTICULUM_BOARD_ID=2'),None),(('ARDUINO_ARCH_ESP32','HELTEC_LORA_V4','SEEED_XIAO_S3','MESHTICULUM_BOARD_ID=1'),None),(('ARDUINO_ARCH_ESP32','HELTEC_LORA_V4','MESHTICULUM_BOARD_ID=1','MTBENCH_ESP32_BULK_SPI=0'),None),(('ARDUINO_ARCH_ESP32',),None)])
def test_target_and_external_override(recipe,tmp_path,defs,expected):
 h=tmp_path/'config.hpp';h.write_text(recipe.native_profile_config(False,esp32_bulk_spi=True))
 result=subprocess.run(['clang++','-x','c++','-std=c++17','-fsyntax-only','-include',str(h),*['-D'+x for x in defs],'-'],input='static_assert(MTBENCH_ESP32_BULK_SPI=='+str(expected or 0)+');',text=True,capture_output=True,timeout=10)
 assert (result.returncode==0)==(expected is not None),result.stderr

def fixture(recipe,tmp_path):
 out=tmp_path/'out';app=out/'project/examples/meshticulum_bench';app.parent.mkdir(parents=True)
 application,identity=recipe.prepare_application(app,esp32_bulk_spi=True)
 vendor=out/'vendor/radiolib'/recipe.ESP32_BULK_PATCH['target'];vendor.parent.mkdir(parents=True);vendor.write_text('test vendor')
 cfg=out/'project/platformio.local.ini';cfg.write_text(''.join('[env:'+target+']\nbuild_flags=\n -include '+str(app/'native_profile_config.hpp')+'\n' for target in recipe.TARGETS))
 record=dict(schema='meshticulum.raw-firmware-prepared.v1',lock_sha256=recipe.sha(recipe.HERE/'inputs.lock.json'),recipe_sha256=recipe.sha(recipe.__file__),source=recipe.tree(out),application=application,identity=identity,build_options={'native_profile':False,'esp32_bulk_spi':True})
 return out,app,vendor,cfg,record

@pytest.mark.parametrize('change,reason',[('vendor','prepared_esp32_bulk_patch_mismatch'),('option','firmware_build_options_mismatch'),('identity','firmware_application_identity_mismatch'),('header','firmware_application_identity_mismatch')])
def test_build_refuses_rehashed_drift_before_compiler(recipe,tmp_path,monkeypatch,change,reason):
 out,app,vendor,cfg,r=fixture(recipe,tmp_path)
 if change=='option':r['build_options']['esp32_bulk_spi']=1
 if change=='identity':r['identity']='mtb1-invalid'
 if change=='header':(app/'native_profile_config.hpp').write_text(recipe.native_profile_config(False));r['application']=recipe.tree(app)
 r['source']=recipe.tree(out);(out/'SOURCE.json').write_text(json.dumps(r))
 monkeypatch.setattr(recipe.subprocess,'check_output',lambda *a,**k:pytest.fail('compiler invoked'))
 with pytest.raises(RuntimeError,match=reason):recipe.build(out,'unused',list(recipe.TARGETS))
 assert not (out/'evidence').exists()

def test_patch_source_drift_before_output(recipe,tmp_path,monkeypatch):
 monkeypatch.setitem(recipe.ESP32_BULK_PATCH,'patch_sha256','0'*64)
 with pytest.raises(RuntimeError,match='esp32_bulk_patch_source_mismatch'):recipe.prepare_application(tmp_path/'app',esp32_bulk_spi=True)
 assert not (tmp_path/'app').exists()

@pytest.mark.parametrize('target',['mtbench_heltec_v4','mtbench_xiao_s3_wio'])
def test_both_forced_includes_before_compiler(recipe,tmp_path,monkeypatch,target):
 out,app,vendor,cfg,r=fixture(recipe,tmp_path)
 monkeypatch.setitem(recipe.ESP32_BULK_PATCH,'patched_sha256',recipe.sha(vendor))
 text=cfg.read_text();start=text.index('[env:'+target+']');text=text[:start]+text[start:].replace('-include','-not-include',1);cfg.write_text(text)
 r['source']=recipe.tree(out);(out/'SOURCE.json').write_text(json.dumps(r))
 monkeypatch.setattr(recipe.subprocess,'check_output',lambda *a,**k:pytest.fail('compiler invoked'))
 with pytest.raises(RuntimeError,match='firmware_bulk_spi_forced_include_missing'):recipe.build(out,'unused',list(recipe.TARGETS))

def test_patch_exact_base_result_and_no_reapplication(recipe,tmp_path):
 # Reverse the checked patch to construct its pinned original, then exercise
 # the production helper. This requires no vendor archive or network.
 patched=recipe.HERE/'radiolib-esp32-bulk.patch'
 import re
 # Locate retained candidate result only when running the local integration;
 # isolated distributions test pins/drift without relying on a worktree.
 source=recipe.HERE.parents[2]/'meshticulum-alpha5-spi-bulk-20261113/source/ArduinoHal.cpp'
 if not source.exists():pytest.skip('local reviewed vendor result unavailable')
 vendor=tmp_path/'vendor';target=vendor/recipe.ESP32_BULK_PATCH['target'];target.parent.mkdir(parents=True);target.write_bytes(source.read_bytes())
 assert recipe.sha(target)==recipe.ESP32_BULK_PATCH['patched_sha256']
 subprocess.run(['/usr/bin/patch','-R','-p1','-F0','-i',str(patched)],cwd=vendor,check=True,capture_output=True)
 assert recipe.sha(target)==recipe.ESP32_BULK_PATCH['original_sha256']
 recipe.apply_esp32_bulk_patch(vendor)
 assert recipe.sha(target)==recipe.ESP32_BULK_PATCH['patched_sha256']
 with pytest.raises(RuntimeError,match='wrong_esp32_bulk_patch_base'):recipe.apply_esp32_bulk_patch(vendor)

@pytest.mark.parametrize('enabled',[False,True])
def test_cli_default_and_optin(recipe,monkeypatch,enabled,capsys):
 import sys
 seen={}
 monkeypatch.setattr(recipe,'prepare',lambda *a,**kw:seen.update(kw) or {})
 monkeypatch.setattr(sys,'argv',['prepare_firmware.py','prepare','--archive-dir','unused','--output-dir','unused']+(['--esp32-bulk-spi'] if enabled else []))
 recipe.main();assert seen.get('esp32_bulk_spi',False) is enabled
