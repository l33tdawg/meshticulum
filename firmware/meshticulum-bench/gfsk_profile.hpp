// SPDX-License-Identifier: Apache-2.0
#pragma once
// Typed GFSK configuration. The distinct firmware ARM contract supplies the
// finite lease, boot/request replay guard and persistent boot debt.
#include <cstdint>
#include <cstddef>
#include <cstring>
#include "protocol.hpp"

namespace mtbench::gfsk {
constexpr size_t PROFILE_BYTES=47;
struct Profile {
    uint32_t frequency=0, rate=0, deviation=0, bandwidth=0;
    int8_t power=0;
    uint16_t preambleBits=0;
    uint8_t sync[8]={}, shaping=0;
    uint32_t leaseMs=0, maxAttempts=0;
    uint64_t maxAirtimeUs=0;
};
inline bool valid(const Profile& p,uint8_t board) {
    if(board!=1 && board!=2) return false;
    if(p.rate!=125000 || p.deviation!=31250 || p.bandwidth!=312000
        || p.preambleBits!=32 || p.shaping!=0x09 || memcmp(p.sync,"MTGFSK01",8)
        || p.frequency<902156000 || p.frequency>927844000
        || p.power<0 || p.power>(board==1 ? 10 : 22)
        || p.leaseMs<1000 || p.leaseMs>300000 || !p.maxAttempts || p.maxAttempts>100000
        || !p.maxAirtimeUs || p.maxAirtimeUs>300000000ull) return false;
    return true;
}
inline bool decode(const uint8_t* p,size_t n,Profile& out,uint8_t board) {
    if(!p || n!=PROFILE_BYTES || (board!=1 && board!=2)
        || p[0]!='G' || p[1]!='F' || p[2]!=1) return false;
    Profile value; value.frequency=u32(p+3); value.rate=u32(p+7);
    value.deviation=u32(p+11); value.bandwidth=u32(p+15); value.power=int8_t(p[19]);
    value.preambleBits=u16(p+20); memcpy(value.sync,p+22,8); value.shaping=p[30];
    value.leaseMs=u32(p+31); value.maxAttempts=u32(p+35); value.maxAirtimeUs=u64(p+39);
    if(!valid(value,board)) return false;
    out=value; return true;
}
inline uint64_t airtimeUs(size_t size) {
    if(!size || size>255) return 0;
    //32 preamble bits +64 sync bits +8 length bits +16 CRC bits.
    return (120ull+8ull*size)*1000000ull/125000ull;
}
// Public RadioLib7.7.1 APIs. This bench path uses the common <=10 dBm profile.
template<class Radio> int16_t program(Radio& radio,const Profile& profile,float boardTcxoVoltage,bool boardUseLdo) {
    if(!valid(profile,1)) return -32000;
    int16_t result=radio.beginFSK(profile.frequency/1000000.0f,125.0f,31.25f,312.0f,
                                  profile.power,32,boardTcxoVoltage,boardUseLdo);
    if(result) return result;
    // beginFSK changes the default to no Gaussian filter: set it explicitly.
    if((result=radio.setDataShaping(RADIOLIB_SHAPING_0_5))) return result;
    uint8_t sync[8]; memcpy(sync,profile.sync,8);
    if((result=radio.setSyncWord(sync,8))) return result;
    // Explicitly recompute cached AUTO32 after sync64; detector32 < sync64.
    if((result=radio.setPreambleLength(32))) return result;
    if((result=radio.variablePacketLengthMode(255))) return result;
    if((result=radio.setCRC(2,0xFFFF,0x1021,false))) return result;
    if((result=radio.setWhitening(true,0x01FF))) return result;
    // Preserve the warm oscillator only within the admitted lease.
    return radio.setStandbyXOSC(true);
}
} // namespace mtbench::gfsk
