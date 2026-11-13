"""Compile the exact hash-checked patched vendor functions, not a mirrored implementation."""
import subprocess
import pytest
from tests.test_firmware_recipe import recipe
HEADER = '\n#include <cstdint>\n#include <cmath>\n#include <cassert>\n#include <cstdio>\n#define RADIOLIB_ERR_NONE 0\n#define RADIOLIB_ASSERT(x) if((x)!=0) return(x)\n#define RADIOLIB_SX126X_REG_SENSITIVITY_CONFIG 0x889\n#define RADIOLIB_SX126X_REG_IQ_CONFIG 0x736\n#define RADIOLIB_SX126X_PACKET_TYPE_LORA 1\n#define RADIOLIB_SX126X_LORA_IQ_INVERTED 1\n'
CLASS = '\nstruct SX126x {\n uint8_t reg=0,modem=1; float bandwidthKhz=500; int reads=0,writes=0; int16_t read_error=0,write_error=0;\n int16_t readRegister(int,uint8_t*p,int){++reads;if(read_error)return read_error;*p=reg;return 0;}\n int16_t writeRegister(int,uint8_t*p,int){++writes;if(write_error)return write_error;reg=*p;return 0;}\n uint8_t getPacketType(){return modem;}\n int16_t fixSensitivity();int16_t fixInvertedIQ(uint8_t);\n};\n'
MAIN = '\nint main(){unsigned cases=0;\n for(int b=0;b<256;b++)for(int option=0;option<2;option++)for(int which=0;which<2;which++){\n  old::SX126x a; proposed::SX126x z;a.reg=z.reg=b;a.modem=z.modem=option;\n  auto run=[&](auto& x){return which?x.fixInvertedIQ(option):x.fixSensitivity();};\n  assert(run(a)==0&&run(z)==0);assert(a.reg==z.reg&&a.reads==z.reads&&a.reads==1);\n  assert(a.writes==1);assert(z.writes==(ENABLED && z.reg==b?0:1));\n  old::SX126x ar;proposed::SX126x zr;ar.read_error=zr.read_error=-9;\n  assert(run(ar)==-9&&run(zr)==-9&&ar.writes==0&&zr.writes==0);\n  if(z.reg!=b){proposed::SX126x f;f.reg=b;f.modem=option;f.write_error=-8;assert(run(f)==-8);}\n  ++cases;\n }std::printf("%u actual-function parity/fault cases passed\\n",cases);\n}\n'

def extract(source):
 result=[]
 for fn in ('fixSensitivity','fixInvertedIQ'):
  a=source.index('int16_t SX126x::'+fn+'(');b=source.index('\n}\n',a)+3;result.append(source[a:b])
 return '\n'.join(result)

@pytest.mark.parametrize('enabled',[False,True])
def test_exact_patched_function_parity_and_faults(recipe,tmp_path,enabled):
 source=recipe.HERE.parents[2]/'meshticulum-alpha5-firmware-bulk-20261113/vendor/radiolib'/recipe.ERRATUM_WRITES_PATCH['target']
 if not source.exists():pytest.skip('local pinned upstream vendor unavailable')
 original=source.read_text();assert recipe.sha(source)==recipe.ERRATUM_WRITES_PATCH['original_sha256']
 vendor=tmp_path/'vendor';target=vendor/recipe.ERRATUM_WRITES_PATCH['target'];target.parent.mkdir(parents=True);target.write_text(original)
 recipe.apply_erratum_writes_patch(vendor);patched=target.read_text()
 # Use the patched file's actual default/strict architecture guard.
 prefix=patched[:patched.index('#include')]
 options='#define ARDUINO_ARCH_ESP32 1\n'+('#define MTBENCH_SKIP_UNCHANGED_ERRATUM_WRITES 1\n' if enabled else '')
 cpp=HEADER+'namespace old {\n'+CLASS+extract(original)+'\n}\n'+options+prefix+'namespace proposed {\n'+CLASS+extract(patched)+'\n}\n#define ENABLED '+str(int(enabled))+'\n'+MAIN
 file=tmp_path/'test.cpp';file.write_text(cpp);binary=tmp_path/'test'
 subprocess.run(['clang++','-std=c++17','-O2','-fsanitize=address,undefined',str(file),'-o',str(binary)],check=True,capture_output=True,timeout=30)
 result=subprocess.run([str(binary)],check=True,capture_output=True,text=True,timeout=30)
 assert '1024 actual-function parity/fault cases passed' in result.stdout
