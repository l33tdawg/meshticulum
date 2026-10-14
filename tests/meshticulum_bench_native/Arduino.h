#pragma once
#include <cassert>
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
    size_t rx_capacity=256, dropped_input=0, largest_read=0;
    size_t rx_buffer_calls=0, bulk_read_calls=0, forced_read_count=0;
    bool rx_allocation_failure=false, begun=false;
    bool stalled=false;
    void (*read_hook)()=nullptr;
    size_t setRxBufferSize(size_t size) {
        rx_buffer_calls++;
        assert(!begun); input.clear(); rx_capacity=rx_allocation_failure ? 0 : size;
        return rx_capacity;
    }
    void begin(int) { begun=true; }
    void setTxTimeoutMs(int) {}
    int available() { return input.size(); }
    int read() { uint8_t b=input.front(); input.pop_front(); if(read_hook) read_hook(); return b; }
    size_t read(uint8_t* buffer,size_t size) {
        bulk_read_calls++;
        if(forced_read_count) return forced_read_count;
        size_t count=0;
        while(count<size && !input.empty()) buffer[count++]=uint8_t(read());
        if(count>largest_read) largest_read=count;
        return count;
    }
    bool offerInput(uint8_t byte) {
        if(input.size()==rx_capacity) { dropped_input++; return false; }
        input.push_back(byte); return true;
    }
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
