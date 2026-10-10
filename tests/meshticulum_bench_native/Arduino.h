#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <deque>
#include <vector>
inline uint64_t fake_time=1000000;
inline void delay(unsigned ms) { fake_time+=ms*1000; }
struct FakeSerial {
    std::deque<uint8_t> input;
    std::vector<uint8_t> bytes;
    size_t write_limit=710;
    bool stalled=false;
    void begin(int) {}
    void setTxTimeoutMs(int) {}
    int available() { return input.size(); }
    int read() { uint8_t b=input.front(); input.pop_front(); return b; }
    int availableForWrite() { return stalled ? 0 : 710; }
    size_t write(const uint8_t* p,size_t n) {
        if(stalled) return 0;
        size_t count=n<write_limit ? n : write_limit;
        bytes.insert(bytes.end(),p,p+count); return count;
    }
};
inline FakeSerial Serial;
struct FakeESP { uint64_t getEfuseMac() { return 0x545662f61b44; } };
inline FakeESP ESP;
