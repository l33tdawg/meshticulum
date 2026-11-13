"""CPU160 generated app identity and exact source snippets with fake clock APIs."""
import json,subprocess
from pathlib import Path
import pytest
from tests.test_firmware_recipe import recipe

@pytest.mark.parametrize('v',[True,False,0,159,240,'160',[],{}])
def test_invalid_option_before_output(recipe,tmp_path,v):
    with pytest.raises(RuntimeError,match='invalid_heltec_cpu_option'):
        recipe.prepare_application(tmp_path/'app',native_profile=True,heltec_cpu_mhz=v)
    assert not (tmp_path/'app').exists()

def test_requires_profile(recipe,tmp_path):
    with pytest.raises(RuntimeError,match='cpu160_requires_native_profile'):
        recipe.prepare_application(tmp_path/'app',heltec_cpu_mhz=160)
    assert not (tmp_path/'app').exists()

def test_default_matches_real_installed_5f_application(recipe,tmp_path):
    options=dict(native_profile=True,heltec_spi_hz=8000000,rx_header_preserve=True,esp32_bulk_spi=True,skip_unchanged_erratum_writes=True,burst_wake_hint=True)
    old,oldid=recipe.prepare_application(tmp_path/'old',**options)
    off,offid=recipe.prepare_application(tmp_path/'off',heltec_cpu_mhz=80,**options)
    on,onid=recipe.prepare_application(tmp_path/'on',heltec_cpu_mhz=160,**options)
    assert old==off and oldid==offid=='mtb1-5f064e6a31'
    assert onid!=oldid and {k for k in on if on[k]!=old[k]}=={'main.cpp','native_profile_config.hpp'}
    assert recipe.build_options(True,heltec_cpu_mhz=80)=={'native_profile':True}
    assert recipe.build_options(True,heltec_cpu_mhz=160)=={'native_profile':True,'heltec_cpu_mhz':160}

@pytest.mark.parametrize('defs,expected',[
    (['ARDUINO_ARCH_ESP32','HELTEC_LORA_V4','MESHTICULUM_BOARD_ID=1'],1),
    (['ARDUINO_ARCH_ESP32','SEEED_XIAO_S3','MESHTICULUM_BOARD_ID=2'],0),
    (['HELTEC_LORA_V4','MESHTICULUM_BOARD_ID=1'],None),
    (['ARDUINO_ARCH_ESP32','HELTEC_LORA_V4','MESHTICULUM_BOARD_ID=2'],None),
    (['ARDUINO_ARCH_ESP32','HELTEC_LORA_V4','SEEED_XIAO_S3','MESHTICULUM_BOARD_ID=1'],None),
    (['ARDUINO_ARCH_ESP32','HELTEC_LORA_V4','MESHTICULUM_BOARD_ID=1','MTBENCH_CPU160=0'],None)])
def test_board_override_gates(recipe,tmp_path,defs,expected):
    p=tmp_path/'h.hpp';p.write_text(recipe.native_profile_config(True,heltec_cpu_mhz=160))
    r=subprocess.run(['clang++','-x','c++','-std=c++17','-fsyntax-only','-include',str(p),*['-D'+d for d in defs],'-'],input=f'static_assert(MTBENCH_CPU160=={expected or 0});',text=True,capture_output=True)
    assert (r.returncode==0)==(expected is not None),r.stderr

@pytest.mark.parametrize('anchor',['static void command(const Frame& frame) {','    if(frame.type==NATIVE_PROFILE) {','    board.begin();'])
def test_exact_single_anchor(recipe,anchor):
    source=(recipe.HERE/'main.cpp').read_text()
    for changed in (source.replace(anchor,''),source+'\n'+anchor):
        with pytest.raises(RuntimeError,match='anchor_drift'):recipe.cpu160_main(changed)

def test_exact_generated_setup_failures_and_clock_reply(recipe,tmp_path):
    source=recipe.cpu160_main((recipe.HERE/'main.cpp').read_text())
    helper=source[source.index('#if MTBENCH_CPU160\nstatic bool'):source.index('static void command(')]
    setup=source[source.index('    board.begin();'):source.index('    put64(boot,ESP.getEfuseMac());')]
    query=source[source.index('        if(frame.size==1 && frame.body[0]==0xfc)'):source.index('        const bool memory=')]
    assert source.index('if(!cpuClockVerified)')<source.index('Preferences state;')<source.index('ready=radio_init();')
    code=r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <vector>
#define MTBENCH_CPU160 1
using namespace std;
vector<int> calls; bool setter=true;unsigned cpu=160,apb=80000000;
bool setCpuFrequencyMhz(unsigned n){assert(n==160);calls.push_back(1);return setter;}
unsigned getCpuFrequencyMhz(){calls.push_back(2);return cpu;}
unsigned getApbFrequency(){calls.push_back(3);return apb;}
struct Board{void begin(){calls.push_back(0);}}board;
bool fenced=false,ready=false,serialReady=true; int radioCalls=0;
'''+helper+'\nvoid setup_model(){\n'+setup+'\nradioCalls++;ready=true;\n}\n'+r'''
struct Frame{unsigned size=1;uint8_t body[320]={0xfc};uint64_t request=9;};
Frame response(int,uint64_t){Frame f;memset(f.body,0,sizeof(f.body));return f;}
void put32(uint8_t*p,uint32_t n){for(int i=3;i>=0;i--){p[i]=n&255;n>>=8;}}
uint32_t read32(uint8_t*p){return uint32_t(p[0])<<24|uint32_t(p[1])<<16|uint32_t(p[2])<<8|p[3];}
namespace mtbench{constexpr int BUSY=4;}
constexpr int NATIVE_PROFILE_REPLY=0x95,BAD_COMMAND=1;
bool armed=false,transmitting=false,resultPending=false,serialContainmentPending=false;
struct {bool containment_required=false;}burstBoot;
struct Q{bool p=false,r=false;bool pending(){return p;}bool retained(){return r;}}q;
Q*burstQueue=nullptr;
int refused=0;void refuse(const Frame&,int v){refused=v;}
struct{Frame last;int count=0;void offer(Frame f){last=f;count++;}}output;
void query(Frame frame){
'''+query+r'''
}
int main(){
for(int mode=0;mode<4;mode++){
 calls.clear();setter=mode!=1;cpu=mode==2?80:160;apb=mode==3?40000000:80000000;
 fenced=ready=false;serialReady=true;radioCalls=0;cpuClockVerified=false;setup_model();
 assert(ready==(mode==0));assert(radioCalls==(mode==0));assert(fenced==(mode!=0));assert(serialReady==(mode==0));
 assert(calls[0]==0&&calls[1]==1);
 if(mode==1)assert(calls.size()==2);
 if(mode==2)assert(calls.size()==3);
 if(mode==0||mode==3)assert(calls.size()==4);
}
setter=true;cpu=160;apb=80000000;cpuClockVerified=true;calls.clear();
query(Frame{});assert(output.count==1&&output.last.size==24);
assert(memcmp(output.last.body,"MNC1",4)==0&&output.last.body[4]==1);
assert(!output.last.body[5]&&!output.last.body[6]&&!output.last.body[7]);
assert(read32(output.last.body+8)==160&&read32(output.last.body+12)==160);
assert(read32(output.last.body+16)==80000000&&read32(output.last.body+20)==1);
assert((calls==vector<int>{2,3}));
for(bool* b:{&armed,&transmitting,&resultPending,&serialContainmentPending,&burstBoot.containment_required,&q.p,&q.r}){
 burstQueue=&q;*b=true;calls.clear();refused=0;query(Frame{});
 assert(refused==4&&calls.empty()&&output.count==1);*b=false;
}
}
'''
    p=tmp_path/'test.cpp';p.write_text(code);exe=tmp_path/'test'
    r=subprocess.run(['clang++','-std=c++17','-fsanitize=address,undefined',str(p),'-o',str(exe)],capture_output=True,text=True)
    assert r.returncode==0,r.stderr
    subprocess.run([str(exe)],check=True)

def test_failed_setup_exact_loop_never_decodes_or_touches_radio(recipe,tmp_path):
    source=recipe.cpu160_main((recipe.HERE/'main.cpp').read_text())
    loop=source[source.index('void loop() {'):]
    code=r'''
#include <cassert>
#include <cstdint>
#include <cstddef>
using std::size_t;
int radioActions=0,reads=0,decodes=0,commands=0,waits=0;
bool serialReady=false,resultPending=false,serialContainmentPending=false;
struct Frame{}previousResult;
struct{void flush(int){}bool offer(Frame){return true;}}output;
struct SerialFake{operator int(){return 0;}size_t read(uint8_t*,size_t){reads++;return 0;}}Serial;
struct{bool feed(uint8_t,Frame&){decodes++;return true;}}decoder;
constexpr size_t USB_RX_DRAIN_BYTES=64;
constexpr int RADIOLIB_ERR_NONE=0;
int idleRadio(){radioActions++;return 0;}
void serviceRadio(){radioActions++;}
void burstService(bool dispatch){assert(!dispatch);}
void command(Frame){commands++;radioActions++;}
void serialReadFailed(){assert(false);}
void waitForRadioOrTick(){waits++;}
'''+loop+r'''
int main(){for(int n=0;n<10;n++)loop();assert(!radioActions&&!reads&&!decodes&&!commands&&waits==10);}
'''
    p=tmp_path/'loop.cpp';p.write_text(code);exe=tmp_path/'loop'
    r=subprocess.run(['clang++','-std=c++17','-fsanitize=address,undefined',str(p),'-o',str(exe)],capture_output=True,text=True)
    assert r.returncode==0,r.stderr
    subprocess.run([str(exe)],check=True)

def test_strict_clock_codec():
    import struct
    from meshticulum.bench.native_profile import clock_query,NativeClockSnapshot
    from meshticulum.bench.serial_protocol import Frame,Message,SerialError
    boot=b'a'*16;request=19
    body=b'MNC1\x01\0\0\0'+struct.pack('!4I',160,160,80000000,1)
    frame=Frame(Message.NATIVE_PROFILE_REPLY,boot,request,body)
    assert clock_query(boot=boot,request=request).body==b'\xfc'
    assert NativeClockSnapshot.from_frame(frame,boot=boot,request=request).matches
    for bad in (body[:-1],body+b'\0',body[:4]+b'\x02'+body[5:],body[:7]+b'\x01'+body[8:],body[:20]+struct.pack('!I',2),body[:8]+struct.pack('!I',80)+body[12:]):
        with pytest.raises(SerialError):NativeClockSnapshot.from_frame(Frame(Message.NATIVE_PROFILE_REPLY,boot,request,bad),boot=boot,request=request)
    for key,value in [('boot',b'b'*16),('request',20)]:
        expected=dict(boot=boot,request=request);expected[key]=value
        with pytest.raises(SerialError):NativeClockSnapshot.from_frame(frame,**expected)
    for cpu,apb,flag in [(80,80000000,1),(160,40000000,1),(160,80000000,0)]:
        changed=Frame(Message.NATIVE_PROFILE_REPLY,boot,request,body[:8]+struct.pack('!4I',160,cpu,apb,flag))
        assert not NativeClockSnapshot.from_frame(changed,boot=boot,request=request).matches

def test_cli_cpu_option_forwarded(recipe,tmp_path,monkeypatch,capsys):
    import sys
    seen={}
    def prepare(*a,**kw):seen.update(kw);return {'test':'no effects'}
    monkeypatch.setattr(recipe,'prepare',prepare)
    monkeypatch.setattr(sys,'argv',['prepare_firmware.py','prepare','--archive-dir',str(tmp_path),'--output-dir',str(tmp_path/'out'),'--native-profile','--heltec-cpu-mhz','160'])
    recipe.main();assert seen['heltec_cpu_mhz']==160 and seen['native_profile']

@pytest.mark.parametrize('change,reason',[('main','cpu160_main_mismatch'),('header','firmware_application_identity_mismatch'),('option','firmware_build_options_mismatch')])
def test_rehashed_cpu_build_drift_rejected(recipe,tmp_path,monkeypatch,change,reason):
    out=tmp_path/'out';app=out/'project/examples/meshticulum_bench';app.parent.mkdir(parents=True)
    application,identity=recipe.prepare_application(app,native_profile=True,heltec_cpu_mhz=160)
    cfg=out/'project/platformio.local.ini';cfg.write_text(''.join('[env:'+target+']\nbuild_flags=\n -include '+str(app/'native_profile_config.hpp')+'\n' for target in recipe.TARGETS))
    record=dict(schema='meshticulum.raw-firmware-prepared.v1',lock_sha256=recipe.sha(recipe.HERE/'inputs.lock.json'),recipe_sha256=recipe.sha(recipe.__file__),application=application,identity=identity,build_options={'native_profile':True,'heltec_cpu_mhz':160})
    if change=='main':(app/'main.cpp').write_text((app/'main.cpp').read_text().replace('getCpuFrequencyMhz()==160','getCpuFrequencyMhz()==80'))
    if change=='header':(app/'native_profile_config.hpp').write_text(recipe.native_profile_config(True))
    if change=='option':record['build_options']['heltec_cpu_mhz']=True
    record['application']=recipe.tree(app)
    import hashlib
    record['identity']='mtb1-'+hashlib.sha256(json.dumps(record['application'],sort_keys=True).encode()).hexdigest()[:10]
    record['source']=recipe.tree(out)
    (out/'SOURCE.json').write_text(json.dumps(record))
    with pytest.raises(RuntimeError,match=reason):recipe.build(out,Path('/no/compiler'),list(recipe.TARGETS))
