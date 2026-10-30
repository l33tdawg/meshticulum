// Actual production queue/adapter under deterministic radio/time faults.
#define main existing_fault_main
#include "harness.cpp"
#undef main

static Frame trainOpen(uint64_t id=2,uint8_t train=12) {
    auto f=burstOpenRequest(id);memmove(f.body+2,f.body,48);f.body[0]=8;f.body[1]=train;f.size=50;return f;
}
static void prepareTrain() {
    radio.airtime_us=34640;
    auto arm=armRequest();put32(arm.body+4,500000);arm.body[8]=5;put16(arm.body+11,12);
    put32(arm.body+14,10000);put32(arm.body+18,64);put64(arm.body+22,10000000);
    command(arm);assert(armed);drain();command(trainOpen());drain();
    assert(burstQueue && burstDepth==8 && burstTrainFrames==12 && burstOpenSize==50);
}
static Frame data(uint64_t id,uint64_t end) { auto f=burstDataRequest(id);put64(f.body+8,end);put64(f.body+16,radio.airtime_us);return f; }
static void queue(uint64_t end,unsigned n=8) {
    for(unsigned i=0;i<n;i++){command(data(i+3,end));drain();}
    assert(attempted==n && burstQueue->retained()==n && !radio.starts);
}
int main(int argc,char** argv) {
    assert(argc==2);std::string mode=argv[1];setup();assert(ready);
    if(mode=="capability") {
        command(request(BURST_CAPABILITIES,90));auto old=request(BURST_CAPABILITIES,91);old.size=1;old.body[0]=8;command(old);
        auto fresh=old;fresh.request=92;fresh.size=2;fresh.body[1]=12;command(fresh);auto rows=drain();
        assert(rows.size()==3 && rows[0].size==24 && rows[1].size==24 && rows[2].size==25);
        assert(!memcmp(rows[0].body,"MBQ1\x04",5) && !memcmp(rows[1].body,"MBQ8\x08",5));
        assert(!memcmp(rows[2].body,"MBT1\x08\x0c\x07",7) && u16(rows[2].body+7)==255);
        assert(u32(rows[2].body+9)==2000000 && u32(rows[2].body+13)==100000
            && u32(rows[2].body+17)==5000 && u32(rows[2].body+21)==100000);
        assert(!attempted && !radio.starts && !lastMutation);return 0;
    }
    if(mode=="invalid_admission") {
        for(auto pair:{std::pair<uint8_t,uint8_t>{4,12},{8,13},{8,255},{255,12}}) {
            burst_native::BootState state;memcpy(state.boot,boot,16);
            burst_native::Admission a;memcpy(a.binding.boot,boot,16);a.binding.context_sha256[0]=1;
            a.lease_deadline_us=fake_time+2000000;a.max_attempts=64;a.max_airtime_us=10000000;
            a.depth=pair.first;a.train_frames=pair.second;burst_native::TxBurstQueue q(a,state);
            assert(q.fenced());burst_native::Evidence e;assert(!q.evidenceAt(255,e));
            assert(q.issue(fake_time,false,true).code==burst_native::Code::Fenced && !state.counters.attempts);
        }return 0;
    }
    prepareTrain();const auto end=fake_time+1900000;
    if(mode=="open_binding") {
        command(trainOpen());auto rows=drain();assert(rows.back().type==BURST_OPEN_REPLY && rows.back().size==50);
        auto bad=trainOpen();bad.body[1]=8;command(bad);rows=drain();assert(rows.back().type==REFUSED);
        auto legacy=burstOpenRequest(3);command(legacy);rows=drain();assert(rows.back().type==REFUSED);
        auto extended=trainOpen(4);put64(extended.body+42,burstOpenDeadline+1);command(extended);rows=drain();
        assert(rows.back().type==REFUSED && !attempted && burstTrainFrames==12);return 0;
    }
    if(mode=="singleton") {
        queue(end,1);fake_time=std::max(fake_time,burstNextStartUs);loop();burstDone();
        assert(burstTrainCount==0 && burstNextStartUs==burstLastRxStartUs+BURST_REVERSE_US);
        const auto floor=burstNextStartUs;command(data(4,end));drain();fake_time=floor-1;loop();assert(radio.starts==1);
        fake_time=floor;loop();assert(radio.starts==2);return 0;
    }
    queue(end);command(data(11,end));auto rows=drain();assert(rows.back().type==REFUSED && attempted==8);
    if(mode=="expiry") {
        fake_time=end;loop();assert(!radio.starts && attempted==8 && !burstQueue->pending());
        for(uint64_t id=3;id<=10;id++)assert(burstEvidence(id).state==burst_native::State::NotSent);return 0;
    }
    fake_time=std::max(fake_time,burstNextStartUs);loop();assert(radio.starts==1);
    if(mode=="rx_failure") {
        radio.receive_error=-9;burstDone();assert(fenced && !armed && attempted==8);
        fake_time+=100000;loop();assert(radio.starts==1);return 0;
    }
    if(mode=="cancel") {
        auto f=request(BURST_CANCEL,20);f.size=8;put64(f.body,burstToken);command(f);drain();
        assert(fenced && !armed && !transmitting && attempted==8 && burstEvidence(3).state==burst_native::State::Unknown);return 0;
    }
    if(mode=="suffix") {
        fake_time=end-110000;burstDone();const auto rx=burstLastRxStartUs;
        assert(burstNextStartUs==rx+BURST_RX_US);fake_time=burstNextStartUs;loop();
        assert(!burstQueue->pending() && burstNextStartUs==rx+BURST_REVERSE_US && radio.starts==1);return 0;
    }
    assert(mode=="twelve");uint64_t next=20;radio.receive_advance_us=7000;
    for(unsigned i=1;i<=12;i++) {
        const auto active=burstActiveRequest;assert(active);burstDone();
        assert(burstNextStartUs==burstLastRxStartUs+(i==12?BURST_REVERSE_US:BURST_RX_US));
        if(i<=4) {
            command(burstAckRequest(next++,active));drain();assert(burstQueue->retained()==7);
            command(data(next++,end));drain();assert(burstQueue->retained()==8 && attempted==8+i);
        } else if(i==5) {
            command(burstAckRequest(next++,active));drain();const auto debt=reservedUs;
            command(data(next++,end));rows=drain();assert(rows.back().type==REFUSED && attempted==12 && reservedUs==debt);
        }
        if(i<12){fake_time=burstNextStartUs-1;loop();assert(radio.starts==i);
            fake_time=burstNextStartUs;loop();assert(radio.starts==i+1);}
    }
    assert(!burstQueue->pending() && !fenced && attempted==12);
    const auto floor=burstNextStartUs;const auto debt=reservedUs;
    for(size_t i=0;i<8;i++){burst_native::Evidence e;if(burstQueue->evidenceAt(i,e)){command(burstAckRequest(next++,e.request));drain();}}
    assert(!burstQueue->retained() && burstNextStartUs==floor && reservedUs==debt);
    auto reopened=trainOpen(next++);command(reopened);drain();assert(burstNextStartUs==floor && burstTrainFrames==12);
    auto fresh=data(next++,fake_time+1900000);put64(fresh.body,burstToken);command(fresh);drain();
    fake_time=floor-1;loop();assert(radio.starts==12);fake_time=floor;loop();assert(radio.starts==13 && attempted==13);
}
