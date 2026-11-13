"""Source recipe identity, target gating and fail-closed vendor patch controls."""
import hashlib,json,subprocess
from pathlib import Path
import pytest
from tests.test_firmware_recipe import recipe

@pytest.mark.parametrize('v',[0,1,None,'true',[],{}])
def test_strict_before_output(recipe,tmp_path,v):
 with pytest.raises(RuntimeError,match='invalid_skip_unchanged_erratum_writes_option'):
  recipe.prepare_application(tmp_path/'app',skip_unchanged_erratum_writes=v)
 assert not (tmp_path/'app').exists()

def test_default_bytes_and_identity(recipe,tmp_path):
 old,oldid=recipe.prepare_application(tmp_path/'old')
 off,offid=recipe.prepare_application(tmp_path/'off',skip_unchanged_erratum_writes=False)
 on,onid=recipe.prepare_application(tmp_path/'on',skip_unchanged_erratum_writes=True)
 assert old==off and oldid==offid and onid!=oldid
 assert recipe.build_options(False)=={'native_profile':False}
 assert recipe.build_options(False,skip_unchanged_erratum_writes=True)=={'native_profile':False,'skip_unchanged_erratum_writes':True}
 assert {k for k in on if on[k]!=old[k]}=={'native_profile_config.hpp'}
 assert recipe.ERRATUM_WRITES_PATCH['patch_sha256'] in (tmp_path/'on/native_profile_config.hpp').read_text()

@pytest.mark.parametrize('defs,expected',[(('ARDUINO_ARCH_ESP32','HELTEC_LORA_V4','MESHTICULUM_BOARD_ID=1'),1),(('ARDUINO_ARCH_ESP32','SEEED_XIAO_S3','MESHTICULUM_BOARD_ID=2'),0),(('HELTEC_LORA_V4','MESHTICULUM_BOARD_ID=1'),None),(('ARDUINO_ARCH_ESP32','HELTEC_LORA_V4','MESHTICULUM_BOARD_ID=2'),None),(('ARDUINO_ARCH_ESP32','HELTEC_LORA_V4','SEEED_XIAO_S3','MESHTICULUM_BOARD_ID=1'),None),(('ARDUINO_ARCH_ESP32','HELTEC_LORA_V4','MESHTICULUM_BOARD_ID=1','MTBENCH_SKIP_UNCHANGED_ERRATUM_WRITES=0'),None),(('ARDUINO_ARCH_ESP32',),None)])
def test_target_and_external_override(recipe,tmp_path,defs,expected):
 h=tmp_path/'config.hpp';h.write_text(recipe.native_profile_config(False,skip_unchanged_erratum_writes=True))
 result=subprocess.run(['clang++','-x','c++','-std=c++17','-fsyntax-only','-include',str(h),*['-D'+x for x in defs],'-'],input='static_assert(MTBENCH_SKIP_UNCHANGED_ERRATUM_WRITES=='+str(expected or 0)+');',text=True,capture_output=True,timeout=10)
 assert (result.returncode==0)==(expected is not None),result.stderr

def fixture(recipe,tmp_path):
 out=tmp_path/'out';app=out/'project/examples/meshticulum_bench';app.parent.mkdir(parents=True)
 application,identity=recipe.prepare_application(app,skip_unchanged_erratum_writes=True)
 vendor=out/'vendor/radiolib'/recipe.ERRATUM_WRITES_PATCH['target'];vendor.parent.mkdir(parents=True);vendor.write_text('test vendor')
 cfg=out/'project/platformio.local.ini';cfg.write_text(''.join('[env:'+target+']\nbuild_flags=\n -include '+str(app/'native_profile_config.hpp')+'\n' for target in recipe.TARGETS))
 record=dict(schema='meshticulum.raw-firmware-prepared.v1',lock_sha256=recipe.sha(recipe.HERE/'inputs.lock.json'),recipe_sha256=recipe.sha(recipe.__file__),source=recipe.tree(out),application=application,identity=identity,build_options={'native_profile':False,'skip_unchanged_erratum_writes':True})
 return out,app,vendor,cfg,record

@pytest.mark.parametrize('change,reason',[('vendor','prepared_erratum_writes_patch_mismatch'),('option','firmware_build_options_mismatch'),('identity','firmware_application_identity_mismatch'),('header','firmware_application_identity_mismatch')])
def test_build_refuses_rehashed_drift_before_compiler(recipe,tmp_path,monkeypatch,change,reason):
 out,app,vendor,cfg,r=fixture(recipe,tmp_path)
 if change=='option':r['build_options']['skip_unchanged_erratum_writes']=1
 if change=='identity':r['identity']='mtb1-invalid'
 if change=='header':(app/'native_profile_config.hpp').write_text(recipe.native_profile_config(False));r['application']=recipe.tree(app)
 r['source']=recipe.tree(out);(out/'SOURCE.json').write_text(json.dumps(r))
 monkeypatch.setattr(recipe.subprocess,'check_output',lambda *a,**k:pytest.fail('compiler invoked'))
 with pytest.raises(RuntimeError,match=reason):recipe.build(out,'unused',list(recipe.TARGETS))
 assert not (out/'evidence').exists()

def test_patch_source_drift_before_output(recipe,tmp_path,monkeypatch):
 monkeypatch.setitem(recipe.ERRATUM_WRITES_PATCH,'patch_sha256','0'*64)
 with pytest.raises(RuntimeError,match='erratum_writes_patch_source_mismatch'):recipe.prepare_application(tmp_path/'app',skip_unchanged_erratum_writes=True)
 assert not (tmp_path/'app').exists()

@pytest.mark.parametrize('target',['mtbench_heltec_v4','mtbench_xiao_s3_wio'])
def test_both_forced_includes_before_compiler(recipe,tmp_path,monkeypatch,target):
 out,app,vendor,cfg,r=fixture(recipe,tmp_path)
 monkeypatch.setitem(recipe.ERRATUM_WRITES_PATCH,'patched_sha256',recipe.sha(vendor))
 text=cfg.read_text();start=text.index('[env:'+target+']');text=text[:start]+text[start:].replace('-include','-not-include',1);cfg.write_text(text)
 r['source']=recipe.tree(out);(out/'SOURCE.json').write_text(json.dumps(r))
 monkeypatch.setattr(recipe.subprocess,'check_output',lambda *a,**k:pytest.fail('compiler invoked'))
 with pytest.raises(RuntimeError,match='firmware_erratum_writes_forced_include_missing'):recipe.build(out,'unused',list(recipe.TARGETS))

def test_patch_exact_base_result_and_no_reapplication(recipe,tmp_path):
 source=recipe.HERE.parents[2]/'meshticulum-alpha5-firmware-bulk-20261113/vendor/radiolib'/recipe.ERRATUM_WRITES_PATCH['target']
 if not source.exists():pytest.skip('local pinned upstream vendor unavailable')
 vendor=tmp_path/'vendor';target=vendor/recipe.ERRATUM_WRITES_PATCH['target'];target.parent.mkdir(parents=True);target.write_bytes(source.read_bytes())
 assert recipe.sha(target)==recipe.ERRATUM_WRITES_PATCH['original_sha256']
 recipe.apply_erratum_writes_patch(vendor)
 assert recipe.sha(target)==recipe.ERRATUM_WRITES_PATCH['patched_sha256']
 with pytest.raises(RuntimeError,match='wrong_erratum_writes_patch_base'):recipe.apply_erratum_writes_patch(vendor)

@pytest.mark.parametrize('enabled',[False,True])
def test_cli_default_and_optin(recipe,monkeypatch,enabled,capsys):
 import sys
 seen={}
 monkeypatch.setattr(recipe,'prepare',lambda *a,**kw:seen.update(kw) or {})
 monkeypatch.setattr(sys,'argv',['prepare_firmware.py','prepare','--archive-dir','unused','--output-dir','unused']+(['--skip-unchanged-erratum-writes'] if enabled else []))
 recipe.main();assert seen.get('skip_unchanged_erratum_writes',False) is enabled

def test_composed_bulk_option_keeps_current_source_off_identity(recipe,tmp_path):
 options=dict(native_profile=True,heltec_spi_hz=8000000,rx_header_preserve=True,esp32_bulk_spi=True)
 old,oldid=recipe.prepare_application(tmp_path/'old',**options)
 off,offid=recipe.prepare_application(tmp_path/'off',**options,skip_unchanged_erratum_writes=False)
 new,newid=recipe.prepare_application(tmp_path/'new',**options,skip_unchanged_erratum_writes=True)
 assert old==off and oldid==offid
 assert newid!=oldid and {k for k in new if new[k]!=old[k]}=={'native_profile_config.hpp'}

def test_both_vendor_patches_compose_without_mutual_changes(recipe,tmp_path):
 source=recipe.HERE.parents[2]/'meshticulum-alpha5-firmware-bulk-20261113/vendor/radiolib'
 if not source.exists():pytest.skip('local pinned upstream vendor unavailable')
 vendor=tmp_path/'vendor'
 for patch in (recipe.ESP32_BULK_PATCH,recipe.ERRATUM_WRITES_PATCH):
  target=vendor/patch['target'];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((source/patch['target']).read_bytes())
 subprocess.run(['/usr/bin/patch','-R','-p1','-F0','-i',str(recipe.HERE/recipe.ESP32_BULK_PATCH['path'])],cwd=vendor,check=True,capture_output=True)
 recipe.apply_esp32_bulk_patch(vendor);recipe.apply_erratum_writes_patch(vendor)
 for patch in (recipe.ESP32_BULK_PATCH,recipe.ERRATUM_WRITES_PATCH):assert recipe.sha(vendor/patch['target'])==patch['patched_sha256']
