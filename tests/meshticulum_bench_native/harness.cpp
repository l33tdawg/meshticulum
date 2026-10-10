// Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
#include <cassert>
#include <iostream>
#include <string>
#include "../../firmware/meshticulum-bench/main.cpp"

static Frame request(uint8_t type,uint64_t id) {
    Frame f; f.type=type; f.request=id; memcpy(f.boot,boot,16); return f;
}
static Frame armRequest(uint64_t id=1) {
    auto f=request(ARM,id); f.size=30;
    put32(f.body,917375000); put32(f.body+4,62500); f.body[8]=8;
    f.body[9]=5; f.body[10]=0; put16(f.body+11,8); f.body[13]=0x42;
    put32(f.body+14,2000); put32(f.body+18,3); put64(f.body+22,300000);
    return f;
}
static Frame txRequest(uint64_t id=2) {
    auto f=request(TRANSMIT,id); f.size=3; f.body[0]=0xc0; f.body[1]=0xdb; f.body[2]=7; return f;
}
static void armAndTx() {
    command(armRequest()); assert(armed);
    command(txRequest()); assert(attempted==1 && reservedUs==100000);
}
static std::vector<Frame> drain() {
    output.flush(Serial); Decoder d; Frame f; std::vector<Frame> result;
    for(auto byte:Serial.bytes) if(d.feed(byte,f)) result.push_back(f);
    Serial.bytes.clear(); return result;
}

int main(int argc,char** argv) {
    assert(argc==2); const std::string mode=argv[1];
    if(mode=="codec") {
        Decoder d; Frame f; char byte;
        while(std::cin.get(byte)) if(d.feed(uint8_t(byte),f)) {
            uint8_t encoded[MAX_ENCODED]; size_t n=encode(f,encoded,sizeof(encoded));
            std::cout.write(reinterpret_cast<char*>(encoded),n);
        }
        return 0;
    }
    if(mode=="boot_failure") preference_ok=false;
    setup();
    if(mode=="boot_failure") {
        command(armRequest()); command(txRequest());
        assert(!ready && !armed && !radio.starts); return 0;
    }
    assert(ready && !armed && !radio.starts && u64(boot+8)==18);
    if(mode=="idle") {
        for(unsigned i=0;i<1000;i++) loop();
        assert(!radio.starts && Serial.bytes.empty());
    } else if(mode=="complete_replay") {
        radio.flags=RADIOLIB_SX126X_IRQ_TX_DONE;
        armAndTx(); assert(transmitting && previousResult.body[0]==STARTED);
        loop(); assert(transmitting); // stale pre-TX flag was cleared
        radio.flags=RADIOLIB_SX126X_IRQ_RX_DONE; loop(); assert(transmitting);
        radio.flags=RADIOLIB_SX126X_IRQ_TX_DONE; loop();
        assert(!transmitting && previousResult.body[0]==RF_DONE);
        command(txRequest()); assert(radio.starts==1 && attempted==1);
        auto conflict=txRequest(); conflict.body[2]++; command(conflict);
        auto frames=drain(); assert(frames.back().type==REFUSED && frames.back().body[0]==ATTEMPT_CONFLICT);
    } else if(mode=="timeout" || mode=="conflicting_flags" || mode=="lost_interrupt") {
        armAndTx();
        if(mode=="timeout") radio.flags=RADIOLIB_SX126X_IRQ_TIMEOUT;
        if(mode=="conflicting_flags") radio.flags=RADIOLIB_SX126X_IRQ_TIMEOUT|RADIOLIB_SX126X_IRQ_TX_DONE;
        if(mode=="lost_interrupt") fake_time+=1100001;
        loop(); assert(fenced && !armed && !transmitting && previousResult.body[0]==UNKNOWN);
        command(txRequest()); assert(radio.starts==1 && attempted==1 && reservedUs==100000);
    } else if(mode=="irq_read_failure") {
        armAndTx(); radio.flags=RADIOLIB_SX126X_IRQ_TX_DONE; radio.irq_error=-11;
        loop(); assert(fenced && !armed && !transmitting && previousResult.body[0]==UNKNOWN);
        assert(u32(previousResult.body+49)==0);
    } else if(mode=="start_failure" || mode=="finish_failure" || mode=="clear_failure") {
        if(mode=="start_failure") radio.start_error=-5;
        if(mode=="clear_failure") { radio.clear_error=-6; radio.flags=RADIOLIB_SX126X_IRQ_TX_DONE; }
        armAndTx();
        if(mode=="finish_failure") { radio.finish_error=-7; radio.flags=RADIOLIB_SX126X_IRQ_TX_DONE; loop(); }
        assert(fenced && !armed && previousResult.body[0]==UNKNOWN && reservedUs==100000);
        if(mode=="clear_failure") assert(radio.starts==0);
    } else if(mode=="wrong_boot") {
        command(armRequest()); auto f=txRequest(); f.boot[0]^=1; command(f);
        assert(!radio.starts && attempted==0); auto frames=drain(); assert(frames.back().body[0]==WRONG_BOOT);
    } else if(mode=="lease_replay") {
        command(armRequest()); const auto old=deadlineUs; fake_time=deadlineUs+1; loop();
        assert(!armed); command(armRequest()); assert(!armed && deadlineUs==old);
        auto frames=drain(); assert(frames.back().body[0]==OLD_ATTEMPT);
    } else if(mode=="budget") {
        auto f=armRequest(); put32(f.body+18,1); put64(f.body+22,100000); command(f);
        command(txRequest()); radio.flags=RADIOLIB_SX126X_IRQ_TX_DONE; loop();
        command(txRequest(3)); assert(radio.starts==1 && attempted==1 && reservedUs==100000);
        auto frames=drain(); assert(frames.back().type==REFUSED && frames.back().body[0]==BUDGET);
    } else if(mode=="receive") {
        command(armRequest()); radio.flags=RADIOLIB_SX126X_IRQ_RX_DONE; loop();
        auto frames=drain(); auto f=frames.back(); assert(f.type==RECEIVE && f.size==8);
        assert(int16_t(u16(f.body))==-925 && memcmp(f.body+4,radio.received.data(),4)==0);
        assert(!radio.starts);
    } else if(mode=="backpressure") {
        auto f=txRequest(); OutputQueue queue; assert(queue.offer(f));
        FakeSerial serial; serial.stalled=true; queue.flush(serial); assert(serial.bytes.empty());
        serial.stalled=false; serial.write_limit=3;
        for(unsigned i=0;i<500;i++) queue.flush(serial);
        Decoder d; Frame result; unsigned count=0;
        for(auto byte:serial.bytes) if(d.feed(byte,result)) count++;
        assert(count==1 && result.request==f.request && result.size==f.size);
        assert(memcmp(result.body,f.body,f.size)==0 && queue.freeSlots()==8);
    } else if(mode=="config_failure") {
        radio.config_error=-9; command(armRequest()); assert(!armed && !radio.starts);
    } else if(mode=="frequency_bounds") {
        for(uint32_t hz:{901999999u,902000000u,928000000u,928000001u}) {
            auto f=armRequest(); put32(f.body,hz); assert(!configure(f.body));
        }
        auto f=armRequest(); put32(f.body,921375000u); assert(configure(f.body));
        assert(!armed && !radio.starts);
    } else assert(false);
    return 0;
}
