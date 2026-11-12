// Opt-in dynamic 24-admission train: real native adapter, synthetic driver/time.
#define main existing_fault_main
#include "harness.cpp"
#undef main

static Frame dynamicOpen(uint64_t id=2,uint8_t train=24) {
    auto f=burstOpenRequest(id);memmove(f.body+2,f.body,48);
    f.body[0]=8;f.body[1]=train;f.size=50;return f;
}
static void prepareDynamic() {
    radio.airtime_us=35920;
    auto arm=armRequest();put32(arm.body+4,500000);arm.body[8]=5;put16(arm.body+11,32);
    put32(arm.body+14,10000);put32(arm.body+18,64);put64(arm.body+22,10000000);
    command(arm);assert(armed);drain();command(dynamicOpen());drain();
    assert(burstQueue && burstDepth==8 && burstTrainFrames==24 && burstOpenSize==50);
}
static Frame dynamicData(uint64_t id,uint64_t end) {
    auto f=burstDataRequest(id);put64(f.body+8,end);put64(f.body+16,radio.airtime_us);return f;
}
static void dynamicDone() {
    fake_time+=38920;radio.flags=RADIOLIB_SX126X_IRQ_TX_DONE;loop();assert(!transmitting);
}
int main(int argc,char** argv) {
    assert(argc==2);std::string mode=argv[1];setup();assert(ready);
    if(mode=="capability") {
        auto f=request(BURST_CAPABILITIES,90);f.size=2;f.body[0]=8;f.body[1]=24;
        command(f);auto rows=drain();assert(rows.size()==1 && rows[0].size==25);
        assert(!memcmp(rows[0].body,"MBT2\x08\x18\x07",7));
        assert(u32(rows[0].body+9)==2000000 && u32(rows[0].body+13)==100000
            && u32(rows[0].body+17)==5000 && u32(rows[0].body+21)==100000);
        assert(!attempted && !radio.starts && !lastMutation);return 0;
    }
    prepareDynamic();const auto end=fake_time+1900000;const auto originalOpen=burstOpenDeadline;
    if(mode=="open_binding") {
        command(dynamicOpen());auto rows=drain();assert(rows.back().type==BURST_OPEN_REPLY);
        command(dynamicOpen(3,12));rows=drain();assert(rows.back().type==REFUSED);
        auto extend=dynamicOpen(4);put64(extend.body+42,originalOpen+1);command(extend);rows=drain();
        assert(rows.back().type==REFUSED && !attempted && burstTrainFrames==24
            && burstOpenDeadline==originalOpen);return 0;
    }
    for(uint64_t id=3;id<=10;id++){command(dynamicData(id,end));drain();}
    assert(attempted==8 && burstQueue->retained()==8);
    command(dynamicData(11,end));auto rows=drain();assert(rows.back().type==REFUSED && attempted==8);
    if(mode=="expiry") {
        fake_time=end;loop();assert(!radio.starts && attempted==8 && !burstQueue->pending());
        for(uint64_t id=3;id<=10;id++)assert(burstEvidence(id).state==burst_native::State::NotSent);
        assert(burstOpenDeadline==originalOpen);return 0;
    }
    fake_time=std::max(fake_time,burstNextStartUs);loop();assert(radio.starts==1);
    if(mode=="cancel") {
        auto f=request(BURST_CANCEL,20);f.size=8;put64(f.body,burstToken);command(f);drain();
        assert(fenced && !armed && !transmitting && attempted==8
            && burstEvidence(3).state==burst_native::State::Unknown);return 0;
    }
    if(mode=="late_budget") {
        dynamicDone();command(burstAckRequest(20,3));drain();const auto debt=reservedUs;
        fake_time=end-1000000;command(dynamicData(21,end));rows=drain();
        assert(rows.back().type==REFUSED && rows.back().body[0]==BUDGET);
        assert(attempted==8 && reservedUs==debt && burstQueue->retained()==7);
        // Keeping the same OPEN cannot make a refill extend the train deadline.
        command(dynamicData(22,end+1));rows=drain();assert(rows.back().type==REFUSED);
        fake_time=end;loop();assert(!burstQueue->pending() && attempted==8
            && reservedUs==debt && burstOpenDeadline==originalOpen);return 0;
    }
    assert(mode=="twentyfour");uint64_t next=20;
    for(unsigned i=1;i<=24;i++) {
        const auto active=burstActiveRequest;assert(active);dynamicDone();
        assert(burstNextStartUs==burstLastRxStartUs+(i==24?BURST_REVERSE_US:BURST_RX_US));
        command(burstAckRequest(next++,active));drain();
        if(i<=16) {
            assert(burstQueue->retained()==7);command(dynamicData(next++,end));rows=drain();
            assert(rows.back().type==BURST_EVENT && attempted==8+i && burstQueue->retained()==8);
        } else if(i==17) {
            const auto debt=reservedUs;command(dynamicData(next++,end));rows=drain();
            assert(rows.back().type==REFUSED && attempted==24 && reservedUs==debt);
        }
        if(i<24){fake_time=burstNextStartUs-1;loop();assert(radio.starts==i);
            fake_time=burstNextStartUs;loop();assert(radio.starts==i+1);}
    }
    assert(!burstQueue->pending() && !burstQueue->retained() && !fenced && attempted==24);
    const auto floor=burstNextStartUs, debt=reservedUs;
    command(dynamicOpen(next++));drain();assert(burstNextStartUs==floor && reservedUs==debt
        && burstOpenDeadline==originalOpen);
    auto fresh=dynamicData(next++,fake_time+1900000);put64(fresh.body,burstToken);command(fresh);drain();
    fake_time=floor-1;loop();assert(radio.starts==24);fake_time=floor;loop();
    assert(radio.starts==25 && attempted==25);
}
