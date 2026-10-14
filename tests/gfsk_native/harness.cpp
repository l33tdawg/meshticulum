#include <iostream>
#include <iterator>
#include <vector>
#include <array>
#include <cassert>
#define RADIOLIB_SHAPING_0_5 2
#include "../../firmware/meshticulum-bench/gfsk_profile.hpp"
#include "../../firmware/meshticulum-bench/gfsk250_profile.hpp"
using namespace mtbench::gfsk;
struct ModelRadio {
    std::vector<int> calls;
    int failing=0;
    bool fast=false;
    int16_t step(int n) { calls.push_back(n); return failing==n ? -7 : 0; }
    int16_t beginFSK(float,float br,float dev,float bw,int8_t,uint16_t pre,float tcxo,bool ldo) {
        assert(br==(fast ? 250 : 125) && dev==(fast ? 62.5 : 31.25) && bw==(fast ? 467 : 312)
            && pre==32 && tcxo==1.8f && !ldo); return step(1);
    }
    int16_t setDataShaping(uint8_t value) { assert(value==RADIOLIB_SHAPING_0_5); return step(2); }
    int16_t setSyncWord(uint8_t* value,size_t n) { assert(n==8 && !memcmp(value,"MTGFSK01",8)); return step(3); }
    int16_t setPreambleLength(size_t value) { assert(value==32); return step(4); }
    int16_t variablePacketLengthMode(uint8_t value) { assert(value==255); return step(5); }
    int16_t setCRC(uint8_t n,uint16_t seed,uint16_t poly,bool inverse) { assert(n==2 && seed==0xFFFF && poly==0x1021 && !inverse); return step(6); }
    int16_t setWhitening(bool enabled,uint16_t seed) { assert(enabled && seed==0x01FF); return step(7); }
    int16_t setStandbyXOSC(bool enabled) { assert(enabled); return step(8); }
};
int main(int argc,char** argv) {
    std::vector<uint8_t> raw((std::istreambuf_iterator<char>(std::cin)),{});
    const bool fast=argc>1 && !strcmp(argv[1],"250");
    Profile p; if(!(fast ? mtbench::gfsk250::decode(raw.data(),raw.size(),p,1) : decode(raw.data(),raw.size(),p,1))) return 2;
    if((argc>1 && !strcmp(argv[1],"program")) || (fast && argc>2 && !strcmp(argv[2],"program"))) {
        for(int fail=0;fail<=8;fail++) {
            ModelRadio radio;radio.failing=fail;radio.fast=fast;
            const int result=fast ? mtbench::gfsk250::program(radio,p,1.8f,false) : program(radio,p,1.8f,false);
            assert(result==(fail ? -7 : 0));assert(radio.calls.size()==size_t(fail ? fail : 8));
        }
        ModelRadio invalid; p.rate=500000;
        assert((fast ? mtbench::gfsk250::program(invalid,p,1.8f,false) : program(invalid,p,1.8f,false))==-32000 && invalid.calls.empty());
        return 0;
    }
    for(size_t n=1;n<=255;n++) std::cout<<(fast ? mtbench::gfsk250::airtimeUs(n) : airtimeUs(n))<<"\n";
}
