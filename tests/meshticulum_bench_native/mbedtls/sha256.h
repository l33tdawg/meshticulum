#pragma once
#include <cstddef>
#include <cstdint>
// Deliberately fake digest for driver fault tests only. No crypto acceptance.
inline int mbedtls_sha256_ret(const uint8_t* data,size_t n,uint8_t* out,int) {
    for(unsigned i=0;i<32;i++) out[i]=0;
    for(size_t i=0;i<n;i++) out[i%32]^=data[i];
    return 0;
}
