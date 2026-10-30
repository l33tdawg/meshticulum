"""Explicit header preservation is an eighth-input source experiment only."""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from tests.test_firmware_recipe import recipe


@pytest.mark.parametrize('profile', [False, True])
@pytest.mark.parametrize('hz', [None, 2000000, 8000000])
def test_omitted_and_false_keep_header_bytes_options_and_eight_input_identity(recipe,tmp_path,profile,hz):
    omitted=recipe.native_profile_config(profile,heltec_spi_hz=hz)
    explicit=recipe.native_profile_config(profile,heltec_spi_hz=hz,rx_header_preserve=False)
    assert omitted==explicit and 'MTBENCH_RX_HEADER_PRESERVE' not in omitted
    assert recipe.build_options(profile,hz)==recipe.build_options(profile,hz,rx_header_preserve=False)
    off,off_id=recipe.prepare_application(tmp_path/'off',native_profile=profile,heltec_spi_hz=hz)
    same,same_id=recipe.prepare_application(tmp_path/'same',native_profile=profile,
                                           heltec_spi_hz=hz,rx_header_preserve=False)
    on,on_id=recipe.prepare_application(tmp_path/'on',native_profile=profile,
                                       heltec_spi_hz=hz,rx_header_preserve=True)
    assert off==same and off_id==same_id and on_id!=off_id
    assert len(on)==len(off)==8
    assert {key for key in on if on[key]!=off[key]}=={'native_profile_config.hpp'}
    header=(tmp_path/'on/native_profile_config.hpp').read_text()
    assert header.startswith(omitted) and '#define MTBENCH_RX_HEADER_PRESERVE 1\n' in header
    assert on['native_profile_config.hpp']==hashlib.sha256(header.encode()).hexdigest()
    assert on_id=='mtb1-'+hashlib.sha256(json.dumps(on,sort_keys=True).encode()).hexdigest()[:10]
    assert recipe.build_options(profile,hz,rx_header_preserve=True)==dict(
        recipe.build_options(profile,hz),rx_header_preserve=True)


@pytest.mark.parametrize('value', [None,0,1,'true',[],{},1.0])
def test_strict_boolean_refuses_before_any_preparation(recipe,tmp_path,monkeypatch,value):
    monkeypatch.setattr(recipe,'lock',lambda:pytest.fail('read lock before refusal'))
    with pytest.raises(RuntimeError,match='invalid_rx_header_preserve_option'):
        recipe.prepare_application(tmp_path/'app',rx_header_preserve=value)
    with pytest.raises(RuntimeError,match='invalid_rx_header_preserve_option'):
        recipe.prepare(tmp_path/'archives',tmp_path/'out',rx_header_preserve=value)
    assert not (tmp_path/'app').exists() and not (tmp_path/'out').exists()


@pytest.mark.parametrize('profile', [False,True])
@pytest.mark.parametrize('board', ['heltec','xiao'])
@pytest.mark.parametrize('hz', [None,8000000])
def test_generated_header_and_board_selection_keep_the_feature_explicit(recipe,tmp_path,profile,board,hz):
    from tests.test_firmware_spi_option import SPY
    header=tmp_path/'config.hpp'
    header.write_text(recipe.native_profile_config(profile,heltec_spi_hz=hz,rx_header_preserve=True))
    definitions=['HELTEC_LORA_V4','MESHTICULUM_BOARD_ID=1'] if board=='heltec' else ['SEEED_XIAO_S3','MESHTICULUM_BOARD_ID=2']
    command=['clang++','-std=c++17','-fsyntax-only','-x','c++','-include',str(header),
             '-DEXPECTED_HZ='+str(hz if board=='heltec' and hz is not None else 2000000),
             '-DEXPECTED_PROFILE='+str(int(profile)),*['-D'+item for item in definitions]]
    source=SPY+'\nstatic_assert(MTBENCH_RX_HEADER_PRESERVE==1,"explicit header option");\n'
    result=subprocess.run(command+['-'],input=source,text=True,capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr
    changed=subprocess.run(command+['-DMTBENCH_RX_HEADER_PRESERVE=0','-'],
                           input=source,text=True,capture_output=True,timeout=10)
    assert changed.returncode!=0 and 'pinned by the prepared application' in changed.stderr


def prepared_fixture(recipe,tmp_path):
    output=tmp_path/'prepared';app=output/'project/examples/meshticulum_bench'
    app.parent.mkdir(parents=True)
    application,identity=recipe.prepare_application(app,native_profile=True,rx_header_preserve=True)
    config=output/'project/platformio.local.ini'
    config.write_text(''.join('[env:'+target+']\nbuild_flags =\n  -include '+str(app/'native_profile_config.hpp')+'\n'
                              for target in recipe.TARGETS))
    record=dict(schema='meshticulum.raw-firmware-prepared.v1',lock_sha256=recipe.sha(recipe.HERE/'inputs.lock.json'),
                recipe_sha256=recipe.sha(recipe.__file__),source=recipe.tree(output),application=application,
                identity=identity,build_options=dict(native_profile=True,rx_header_preserve=True))
    return output,app,config,record


@pytest.mark.parametrize('change,reason', [
    ('remove','firmware_application_identity_mismatch'),
    ('false','firmware_build_options_mismatch'),('integer','firmware_build_options_mismatch'),
    ('extra','firmware_build_options_mismatch'),('header','firmware_application_identity_mismatch'),
    ('identity','firmware_application_identity_mismatch'),
    ('heltec_include','firmware_rx_header_forced_include_missing'),
    ('xiao_include','firmware_rx_header_forced_include_missing'),
    ('duplicate_include','firmware_rx_header_forced_include_missing')])
def test_option_header_identity_and_both_target_includes_are_required_before_platformio(recipe,tmp_path,monkeypatch,change,reason):
    output,app,config,record=prepared_fixture(recipe,tmp_path)
    if change=='remove':record['build_options'].pop('rx_header_preserve')
    if change=='false':record['build_options']['rx_header_preserve']=False
    if change=='integer':record['build_options']['rx_header_preserve']=1
    if change=='extra':record['build_options']['other_option']=True
    if change=='header':
        (app/'native_profile_config.hpp').write_text(recipe.native_profile_config(True))
        record['application']=recipe.tree(app)
    if change=='identity':record['identity']='mtb1-'+'0'*10
    if change.endswith('include'):
        lines=config.read_text().splitlines(keepends=True)
        index=2 if change=='heltec_include' else 5
        if change=='duplicate_include':lines.insert(3,lines[2])
        else:lines[index]=lines[index].replace('-include','-not-include')
        config.write_text(''.join(lines))
    record['source']=recipe.tree(output)
    (output/'SOURCE.json').write_text(json.dumps(record))
    monkeypatch.setattr(recipe.subprocess,'check_output',lambda *a,**k:pytest.fail('PlatformIO invoked'))
    with pytest.raises(RuntimeError,match=reason):
        recipe.build(output,tmp_path/'python',list(recipe.TARGETS))
    assert not (output/'evidence').exists()


@pytest.mark.parametrize('enabled',[False,True])
def test_cli_adds_only_explicit_header_option(recipe,monkeypatch,capsys,enabled):
    calls=[]
    monkeypatch.setattr(recipe,'prepare',lambda *a,**k:calls.append(k) or dict(source_only=True))
    args=['prepare_firmware.py','prepare','--archive-dir','archives','--output-dir','output']
    if enabled:args.append('--rx-header-preserve')
    monkeypatch.setattr('sys.argv',args);recipe.main()
    assert calls==[dict(native_profile=False,heltec_spi_hz=None,rx_header_preserve=enabled)]
    assert json.loads(capsys.readouterr().out)==dict(source_only=True)


@pytest.mark.parametrize('definitions,reason', [
    (['MTBENCH_RX_HEADER_PRESERVE=1'], 'must come from the pinned recipe'),
    (['MTBENCH_RX_HEADER_PRESERVE=0'], 'must come from the pinned recipe'),
    (['MTBENCH_RX_HEADER_PRESERVE=2','MTBENCH_RX_HEADER_PRESERVE_CONFIGURED=1'], 'must be 0 or 1'),
    (['MTBENCH_RX_HEADER_PRESERVE=1','MTBENCH_RX_HEADER_PRESERVE_CONFIGURED=0'], 'invalid RX header recipe marker')])
def test_lone_or_invalid_compile_override_is_not_a_default_enable(definitions,reason):
    source=(Path(__file__).resolve().parents[1]/'firmware/meshticulum-bench/main.cpp').read_text()
    prelude=source[source.index('#if defined(MTBENCH_RX_HEADER_PRESERVE)'):source.index('#if MTBENCH_NATIVE_PROFILE')]
    result=subprocess.run(['clang++','-x','c++','-E',*['-D'+item for item in definitions],'-'],
                          input=prelude,text=True,capture_output=True,timeout=10)
    assert result.returncode!=0 and reason in result.stderr
