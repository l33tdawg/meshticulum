"""Optional Heltec SPI setting: source identity and host-compiler controls only."""
import hashlib
import json
import subprocess

import pytest

from tests.test_firmware_recipe import recipe


def old_header(enabled):
    return ('#pragma once\n#ifdef MTBENCH_NATIVE_PROFILE\n'
            '#error "native profiling is pinned by the prepared application"\n#endif\n'
            f'#define MTBENCH_NATIVE_PROFILE {int(enabled)}\n')


@pytest.mark.parametrize('enabled', [False, True])
def test_omitted_setting_keeps_exact_predecessor_header_options_and_application(recipe,tmp_path,enabled):
    assert recipe.native_profile_config(enabled)==old_header(enabled)
    assert recipe.build_options(enabled)=={'native_profile':enabled}
    app,identity=recipe.prepare_application(tmp_path/'app',native_profile=enabled)
    assert len(app)==8
    assert app['native_profile_config.hpp']==hashlib.sha256(old_header(enabled).encode()).hexdigest()
    expected={name:recipe.sha(recipe.HERE/name) for name in app if name!='native_profile_config.hpp'}
    expected['native_profile_config.hpp']=hashlib.sha256(old_header(enabled).encode()).hexdigest()
    assert app==expected
    assert identity=='mtb1-'+hashlib.sha256(json.dumps(expected,sort_keys=True).encode()).hexdigest()[:10]
    if enabled:assert identity=='mtb1-e18a633806'


@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('hz', [2000000,8000000])
def test_explicit_option_changes_only_eighth_input_and_is_repeatable(recipe,tmp_path,enabled,hz):
    old,old_id=recipe.prepare_application(tmp_path/'old',native_profile=enabled)
    one,identity=recipe.prepare_application(tmp_path/'one',native_profile=enabled,heltec_spi_hz=hz)
    two,again=recipe.prepare_application(tmp_path/'two',native_profile=enabled,heltec_spi_hz=hz)
    assert one==two and identity==again and identity!=old_id
    assert set(one)==set(old) and len(one)==8
    assert {name for name in one if one[name]!=old[name]}=={'native_profile_config.hpp'}
    assert recipe.build_options(enabled,hz)==dict(native_profile=enabled,heltec_spi_hz=hz)


@pytest.mark.parametrize('value', [True,False,0,1,4000000,16000000,8000000.0,'8000000',[],{}])
def test_invalid_spi_refuses_before_app_or_archive_preparation(recipe,tmp_path,monkeypatch,value):
    monkeypatch.setattr(recipe,'lock',lambda:pytest.fail('read lock before option refusal'))
    with pytest.raises(RuntimeError,match='invalid_heltec_spi_option'):
        recipe.prepare_application(tmp_path/'app',heltec_spi_hz=value)
    with pytest.raises(RuntimeError,match='invalid_heltec_spi_option'):
        recipe.prepare(tmp_path/'archives',tmp_path/'out',heltec_spi_hz=value)
    assert not (tmp_path/'app').exists() and not (tmp_path/'out').exists()


# A small constructor spy exercises the pinned RadioLib public default-argument
# convention; this is not compilation of RadioLib or a board qualification.
SPY='''
#define MSBFIRST 1
#define SPI_MODE0 0
struct SPISettings {
  int hz, order, mode;
  constexpr SPISettings(int h,int o,int m):hz(h),order(o),mode(m){}
};
#ifndef RADIOLIB_DEFAULT_SPI_SETTINGS
#define RADIOLIB_DEFAULT_SPI_SETTINGS SPISettings(2000000,MSBFIRST,SPI_MODE0)
#endif
struct SPIClass {};
SPIClass spi;
struct Module {
  SPISettings settings;
  constexpr Module(int,int,int,int,SPIClass&,SPISettings s=RADIOLIB_DEFAULT_SPI_SETTINGS):settings(s){}
};
constexpr Module module(1,2,3,4,spi);
static_assert(module.settings.hz==EXPECTED_HZ,"clock");
static_assert(module.settings.order==MSBFIRST && module.settings.mode==SPI_MODE0,"mode");
static_assert(MTBENCH_NATIVE_PROFILE==EXPECTED_PROFILE,"profiling");
'''


def compile_spy(recipe,tmp_path,enabled,hz,definitions,expected=8000000):
    header=tmp_path/'config.hpp'
    header.write_text(recipe.native_profile_config(enabled,heltec_spi_hz=hz))
    return subprocess.run(['clang++','-std=c++17','-fsyntax-only','-x','c++','-include',str(header),
        '-DEXPECTED_HZ='+str(expected),'-DEXPECTED_PROFILE='+str(int(enabled)),
        *['-D'+value for value in definitions],'-'],input=SPY,text=True,capture_output=True,timeout=10)


@pytest.mark.parametrize('enabled', [False,True])
@pytest.mark.parametrize('hz', [None,2000000,8000000])
@pytest.mark.parametrize('board', ['heltec','xiao'])
def test_constructor_receives_selected_heltec_clock_but_xiao_remains_2mhz(recipe,tmp_path,enabled,hz,board):
    definitions=['HELTEC_LORA_V4','MESHTICULUM_BOARD_ID=1'] if board=='heltec' else ['SEEED_XIAO_S3','MESHTICULUM_BOARD_ID=2']
    expected=hz if board=='heltec' and hz is not None else 2000000
    result=compile_spy(recipe,tmp_path,enabled,hz,definitions,expected)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('definitions,reason', [
    ([], 'unreviewed SPI experiment target'),
    (['HELTEC_LORA_V4','SEEED_XIAO_S3','MESHTICULUM_BOARD_ID=1'],'contradictory SPI experiment targets'),
    (['HELTEC_LORA_V4'], 'requires board ID 1'),
    (['HELTEC_LORA_V4','MESHTICULUM_BOARD_ID=2'], 'requires board ID 1'),
    (['SEEED_XIAO_S3','MESHTICULUM_BOARD_ID=1'], 'requires board ID 2'),
    (['HELTEC_LORA_V4','MESHTICULUM_BOARD_ID=1','MTBENCH_HELTEC_SPI_HZ=2000000'],'SPI clock is pinned'),
    (['HELTEC_LORA_V4','MESHTICULUM_BOARD_ID=1','RADIOLIB_DEFAULT_SPI_SETTINGS=unexpected'],'SPI settings must come'),
    (['SEEED_XIAO_S3','MESHTICULUM_BOARD_ID=2','RADIOLIB_DEFAULT_SPI_SETTINGS=unexpected'],'SPI settings must come'),
])
def test_unknown_conflicting_or_overridden_spi_target_refuses(recipe,tmp_path,definitions,reason):
    result=compile_spy(recipe,tmp_path,True,8000000,definitions)
    assert result.returncode!=0 and reason in result.stderr


def prepared_fixture(recipe,tmp_path):
    output=tmp_path/'prepared';app=output/'project/examples/meshticulum_bench';app.parent.mkdir(parents=True)
    application,identity=recipe.prepare_application(app,native_profile=True,heltec_spi_hz=8000000)
    config=output/'project/platformio.local.ini'
    config.write_text(''.join('[env:'+target+']\nbuild_flags =\n  -include '+str(app/'native_profile_config.hpp')+'\n' for target in recipe.TARGETS))
    record=dict(schema='meshticulum.raw-firmware-prepared.v1',lock_sha256=recipe.sha(recipe.HERE/'inputs.lock.json'),
        recipe_sha256=recipe.sha(recipe.__file__),source=recipe.tree(output),application=application,identity=identity,
        build_options=dict(native_profile=True,heltec_spi_hz=8000000))
    return output,app,config,record


@pytest.mark.parametrize('change,reason', [
    ('metadata2','firmware_application_identity_mismatch'),
    ('metadataNone','firmware_build_options_mismatch'),
    ('metadataBool','firmware_build_options_mismatch'),
    ('metadataFloat','firmware_build_options_mismatch'),
    ('metadataExtra','firmware_build_options_mismatch'),
    ('header','firmware_application_identity_mismatch'),
    ('identity','firmware_application_identity_mismatch'),
    ('include','firmware_spi_forced_include_missing'),
    ('duplicateInclude','firmware_spi_forced_include_missing'),
])
def test_changed_option_header_identity_or_include_cannot_reach_platformio(recipe,tmp_path,monkeypatch,change,reason):
    output,app,config,record=prepared_fixture(recipe,tmp_path)
    if change=='metadata2':record['build_options']['heltec_spi_hz']=2000000
    if change=='metadataNone':record['build_options']['heltec_spi_hz']=None
    if change=='metadataBool':record['build_options']['heltec_spi_hz']=True
    if change=='metadataFloat':record['build_options']['heltec_spi_hz']=8000000.0
    if change=='metadataExtra':record['build_options']['spi_hz']=8000000
    if change=='header':
        (app/'native_profile_config.hpp').write_text(recipe.native_profile_config(True,heltec_spi_hz=2000000))
        record['application']=recipe.tree(app)
    if change=='identity':record['identity']='mtb1-'+'0'*10
    if change=='include':config.write_text(config.read_text().replace('-include','-not-include'))
    if change=='duplicateInclude':
        text=config.read_text();line='  -include '+str(app/'native_profile_config.hpp')+'\n'
        config.write_text(text.replace(line,line+line))
    record['source']=recipe.tree(output)
    (output/'SOURCE.json').write_text(json.dumps(record))
    monkeypatch.setattr(recipe.subprocess,'check_output',lambda *a,**k:pytest.fail('PlatformIO invoked'))
    with pytest.raises(RuntimeError,match=reason):recipe.build(output,tmp_path/'python',list(recipe.TARGETS))
    assert not (output/'evidence').exists()


@pytest.mark.parametrize('targets', [[],['other'],'mtbench_heltec_v4',[None]])
def test_unknown_build_target_refuses_before_any_source_or_tool_read(recipe,tmp_path,monkeypatch,targets):
    monkeypatch.setattr(recipe.subprocess,'check_output',lambda *a,**k:pytest.fail('PlatformIO invoked'))
    with pytest.raises(RuntimeError,match='unreviewed_firmware_target'):
        recipe.build(tmp_path/'does-not-exist',tmp_path/'python',targets)


@pytest.mark.parametrize('enabled', [False,True])
@pytest.mark.parametrize('hz', [None,2000000,8000000])
def test_cli_forwards_only_explicit_spi_and_independent_profiler(recipe,monkeypatch,capsys,enabled,hz):
    calls=[]
    def fake_prepare(archives,output,**options):
        calls.append((str(archives),str(output),options));return {'source_control':True}
    monkeypatch.setattr(recipe,'prepare',fake_prepare)
    args=['prepare_firmware.py','prepare','--archive-dir','archives','--output-dir','output']
    if enabled:args.append('--native-profile')
    if hz is not None:args+=['--heltec-spi-hz',str(hz)]
    monkeypatch.setattr('sys.argv',args)
    recipe.main()
    assert calls==[('archives','output',dict(native_profile=enabled,heltec_spi_hz=hz))]
    assert json.loads(capsys.readouterr().out)=={'source_control':True}


@pytest.mark.parametrize('value', ['4000000','8000000.0','true'])
def test_cli_rejects_unreviewed_spi_without_preparation(recipe,monkeypatch,value):
    monkeypatch.setattr(recipe,'prepare',lambda *a,**k:pytest.fail('invalid CLI prepared'))
    monkeypatch.setattr('sys.argv',['prepare_firmware.py','prepare','--archive-dir','archives',
        '--output-dir','output','--heltec-spi-hz',value])
    with pytest.raises(SystemExit) as result:recipe.main()
    assert result.value.code==2
