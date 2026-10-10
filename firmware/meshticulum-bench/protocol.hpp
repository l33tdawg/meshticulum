#pragma once
// Meshticulum bench serial protocol. Fixed buffers; no heap allocation.
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace mtbench {
constexpr uint8_t END = 0xc0, ESC = 0xdb, ESC_END = 0xdc, ESC_ESC = 0xdd;
constexpr size_t HEADER = 30, MAX_BODY = 320, MAX_RAW = HEADER + MAX_BODY + 4;
constexpr size_t MAX_ENCODED = 2 * MAX_RAW + 2;
enum Command : uint8_t { HELLO=1, ARM=2, TRANSMIT=3, STATUS=4, DISARM=5,
    HELLO_REPLY=0x81, ARM_REPLY=0x82, TX_RESULT=0x83, STATUS_REPLY=0x84,
    DISARM_REPLY=0x85, RECEIVE=0x90, REFUSED=0xff };
enum Outcome : uint8_t { STARTED=1, RF_DONE=2, UNKNOWN=3 };
enum Refusal : uint8_t { BAD_COMMAND=1, WRONG_BOOT=2, NOT_ARMED=3, BUSY=4,
    BAD_PROFILE=5, BUDGET=6, OLD_ATTEMPT=7, ATTEMPT_CONFLICT=8, FENCED=9 };

inline uint16_t u16(const uint8_t* p) { return uint16_t(p[0])<<8 | p[1]; }
inline uint32_t u32(const uint8_t* p) { return uint32_t(u16(p))<<16 | u16(p+2); }
inline uint64_t u64(const uint8_t* p) { return uint64_t(u32(p))<<32 | u32(p+4); }
inline void put16(uint8_t* p,uint16_t n) { p[0]=n>>8; p[1]=n; }
inline void put32(uint8_t* p,uint32_t n) { put16(p,n>>16); put16(p+2,n); }
inline void put64(uint8_t* p,uint64_t n) { put32(p,n>>32); put32(p+4,n); }
inline uint32_t crc32(const uint8_t* p,size_t n) {
    uint32_t value=0xffffffff;
    for(size_t i=0;i<n;i++) {
        value^=p[i];
        for(unsigned bit=0;bit<8;bit++) value=(value>>1)^(0xedb88320u & (0u-(value&1u)));
    }
    return value^0xffffffff;
}

struct Frame {
    uint8_t type=0, boot[16]={};
    uint64_t request=0;
    uint16_t size=0;
    uint8_t body[MAX_BODY]={};
};

inline size_t encode(const Frame& frame,uint8_t* output,size_t capacity) {
    if(frame.size>MAX_BODY || capacity<MAX_ENCODED) return 0;
    uint8_t raw[MAX_RAW]; raw[0]='M'; raw[1]='B'; raw[2]=1; raw[3]=frame.type;
    memcpy(raw+4,frame.boot,16); put64(raw+20,frame.request); put16(raw+28,frame.size);
    memcpy(raw+HEADER,frame.body,frame.size);
    const size_t length=HEADER+frame.size;
    put32(raw+length,crc32(raw,length));
    size_t count=0; output[count++]=END;
    for(size_t i=0;i<length+4;i++) {
        if(raw[i]==END || raw[i]==ESC) {
            output[count++]=ESC; output[count++]=raw[i]==END ? ESC_END : ESC_ESC;
        } else output[count++]=raw[i];
    }
    output[count++]=END;
    return count;
}

class Decoder {
    uint8_t bytes_[MAX_RAW]={};
    size_t size_=0;
    bool started_=false, escaped_=false, discarded_=false;
public:
    // Invalid/truncated/oversized frames are discarded through the next delimiter.
    bool feed(uint8_t byte,Frame& result) {
        if(byte==END) {
            bool good=started_ && !escaped_ && !discarded_ && size_>=HEADER+4;
            if(good) good=bytes_[0]=='M' && bytes_[1]=='B' && bytes_[2]==1
                && u16(bytes_+28)<=MAX_BODY && size_==HEADER+u16(bytes_+28)+4
                && u32(bytes_+size_-4)==crc32(bytes_,size_-4);
            if(good) {
                result.type=bytes_[3]; memcpy(result.boot,bytes_+4,16);
                result.request=u64(bytes_+20); result.size=u16(bytes_+28);
                memcpy(result.body,bytes_+HEADER,result.size);
            }
            size_=0; started_=true; escaped_=false; discarded_=false;
            return good;
        }
        if(!started_ || discarded_) return false;
        if(escaped_) {
            escaped_=false;
            if(byte==ESC_END) byte=END;
            else if(byte==ESC_ESC) byte=ESC;
            else { discarded_=true; return false; }
        } else if(byte==ESC) { escaped_=true; return false; }
        if(size_==MAX_RAW) { discarded_=true; return false; }
        bytes_[size_++]=byte;
        return false;
    }
};

class OutputQueue {
    static constexpr size_t DEPTH=8;
    uint8_t data_[DEPTH][MAX_ENCODED]={};
    size_t length_[DEPTH]={}, written_=0, head_=0, count_=0;
public:
    size_t freeSlots() const { return DEPTH-count_; }
    bool offer(const Frame& frame) {
        if(count_==DEPTH) return false;
        const size_t slot=(head_+count_)%DEPTH;
        size_t length=encode(frame,data_[slot],MAX_ENCODED);
        if(!length) return false;
        length_[slot]=length; count_++; return true;
    }
    template<class Serial> void flush(Serial& serial) {
        while(count_) {
            const int available=serial.availableForWrite();
            if(available<=0) return;
            const size_t left=length_[head_]-written_;
            const size_t want=size_t(available)<left ? size_t(available) : left;
            const size_t sent=serial.write(data_[head_]+written_,want);
            if(!sent || sent>want) return;
            written_+=sent;
            if(written_!=length_[head_]) return;
            written_=0; head_=(head_+1)%DEPTH; count_--;
        }
    }
};
}  // namespace mtbench
