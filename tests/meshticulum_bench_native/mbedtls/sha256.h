#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
// Portable test SHA-256 permits real host ciphertext hashes in the native loop.
// It does not replace qualification of target mbedTLS or the physical radio.
inline int mbedtls_sha256_ret(const uint8_t* data,size_t n,uint8_t* out,int mode) {
    if(mode || n>320) return -1;
    static constexpr uint32_t k[]={
        0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
        0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
        0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
        0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
        0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
        0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
        0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
        0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2};
    uint32_t h[]={0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
    uint8_t padded[384]={};std::memcpy(padded,data,n);padded[n]=0x80;
    const size_t length=((n+9+63)/64)*64;
    for(unsigned i=0;i<8;i++) padded[length-1-i]=uint8_t((uint64_t(n)*8)>>(i*8));
    const auto rotr=[](uint32_t x,unsigned shift) { return (x>>shift)|(x<<(32-shift)); };
    for(size_t block=0;block<length;block+=64) {
        uint32_t w[64]={};
        for(unsigned i=0;i<16;i++) { const uint8_t* p=padded+block+i*4;w[i]=(uint32_t(p[0])<<24)|(uint32_t(p[1])<<16)|(uint32_t(p[2])<<8)|p[3]; }
        for(unsigned i=16;i<64;i++) {
            const uint32_t a=rotr(w[i-15],7)^rotr(w[i-15],18)^(w[i-15]>>3);
            const uint32_t b=rotr(w[i-2],17)^rotr(w[i-2],19)^(w[i-2]>>10);
            w[i]=w[i-16]+a+w[i-7]+b;
        }
        uint32_t a=h[0],b=h[1],c=h[2],d=h[3],e=h[4],f=h[5],g=h[6],z=h[7];
        for(unsigned i=0;i<64;i++) {
            const uint32_t s1=rotr(e,6)^rotr(e,11)^rotr(e,25),ch=(e&f)^(~e&g);
            const uint32_t t1=z+s1+ch+k[i]+w[i],s0=rotr(a,2)^rotr(a,13)^rotr(a,22);
            const uint32_t t2=s0+((a&b)^(a&c)^(b&c));
            z=g;g=f;f=e;e=d+t1;d=c;c=b;b=a;a=t1+t2;
        }
        h[0]+=a;h[1]+=b;h[2]+=c;h[3]+=d;h[4]+=e;h[5]+=f;h[6]+=g;h[7]+=z;
    }
    for(unsigned i=0;i<8;i++) for(unsigned b=0;b<4;b++) out[i*4+b]=uint8_t(h[i]>>(24-b*8));
    return 0;
}
