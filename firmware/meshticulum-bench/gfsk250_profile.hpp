// SPDX-License-Identifier: Apache-2.0
#pragma once
#include "gfsk_profile.hpp"

// Version 2 is admitted only by the distinct GFSK250 ARM command. The legacy
// namespace/parser/programmer retains its exact version-1 contract.
namespace mtbench::gfsk250 {
using Profile = mtbench::gfsk::Profile;
constexpr size_t PROFILE_BYTES = mtbench::gfsk::PROFILE_BYTES;
inline bool valid(const Profile& p,uint8_t board) {
    if(board!=1 && board!=2) return false;
    return p.rate==250000 && p.deviation==62500 && p.bandwidth==467000
        && p.preambleBits==32 && p.shaping==0x09 && !memcmp(p.sync,"MTGFSK01",8)
        && p.frequency>=902233500 && p.frequency<=927766500
        && p.power>=0 && p.power<=(board==1 ? 10 : 22)
        && p.leaseMs>=1000 && p.leaseMs<=300000 && p.maxAttempts && p.maxAttempts<=100000
        && p.maxAirtimeUs && p.maxAirtimeUs<=300000000ull;
}
inline bool decode(const uint8_t* p,size_t n,Profile& out,uint8_t board) {
    if(!p || n!=PROFILE_BYTES || (board!=1 && board!=2)
        || p[0]!='G' || p[1]!='F' || p[2]!=2) return false;
    Profile value; value.frequency=u32(p+3); value.rate=u32(p+7);
    value.deviation=u32(p+11); value.bandwidth=u32(p+15); value.power=int8_t(p[19]);
    value.preambleBits=u16(p+20); memcpy(value.sync,p+22,8); value.shaping=p[30];
    value.leaseMs=u32(p+31); value.maxAttempts=u32(p+35); value.maxAirtimeUs=u64(p+39);
    if(!mtbench::gfsk250::valid(value,board)) return false;
    out=value; return true;
}
inline uint64_t airtimeUs(size_t size) {
    if(!size || size>255) return 0;
    return (120ull+8ull*size)*1000000ull/250000ull;
}
template<class Radio> int16_t program(Radio& radio,const Profile& p,float tcxo,bool ldo) {
    if(!mtbench::gfsk250::valid(p,1)) return -32000;
    int16_t result=radio.beginFSK(p.frequency/1000000.0f,250.0f,62.5f,467.0f,
                                  p.power,32,tcxo,ldo);
    if(result) return result;
    if((result=radio.setDataShaping(RADIOLIB_SHAPING_0_5))) return result;
    uint8_t sync[8]; memcpy(sync,p.sync,8);
    if((result=radio.setSyncWord(sync,8))) return result;
    if((result=radio.setPreambleLength(32))) return result;
    if((result=radio.variablePacketLengthMode(255))) return result;
    if((result=radio.setCRC(2,0xFFFF,0x1021,false))) return result;
    if((result=radio.setWhitening(true,0x01FF))) return result;
    return radio.setStandbyXOSC(true);
}
inline void capability(uint8_t* out) {
    out[0]='G';out[1]='F';out[2]=2;out[3]=PROFILE_BYTES;
    put32(out+4,250000);put32(out+8,62500);put32(out+12,467000);
}
} // namespace mtbench::gfsk250
