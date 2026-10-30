// Actual scalar adapter and native queue, fake radio/time only.
#define main existing_fault_main
#include "harness.cpp"
#undef main

static Frame eightOpen(uint64_t id=2) {
    auto f=burstOpenRequest(id);memmove(f.body+1,f.body,48);f.body[0]=8;f.size=49;return f;
}
static void prepareEight(bool sf5=true) {
    auto arm=armRequest();put32(arm.body+4,500000);arm.body[8]=sf5 ? 5 : 7;
    put16(arm.body+11,12);put32(arm.body+14,10000);put32(arm.body+18,64);put64(arm.body+22,10000000);
    command(arm);assert(armed);drain();command(eightOpen());
    if(sf5) assert(burstQueue && burstDepth==8 && burstOpenSize==49);
    else assert(!burstQueue && !attempted && !radio.starts);
    drain();
}
static Frame eightData(uint64_t id,uint64_t end) {
    auto f=burstDataRequest(id);put64(f.body+8,end);return f;
}
static void queueEight(unsigned count=8) {
    const auto end=fake_time+1900000;
    for(unsigned n=0;n<count;n++) { command(eightData(3+n,end));drain(); }
    assert(attempted==count && burstQueue->retained()==count && !radio.starts);
}
static void fullTrain() {
    prepareEight();queueEight();const auto debt=reservedUs;
    auto extra=eightData(11,burstEvidence().deadline_us);command(extra);auto rows=drain();
    assert(rows.back().type==REFUSED && attempted==8 && reservedUs==debt);
    if(fake_time<burstNextStartUs) fake_time=burstNextStartUs;
    loop();assert(radio.starts==1);
    radio.receive_advance_us=7000;
    for(unsigned n=1;n<=8;n++) {
        const auto before=fake_time;burstDone();const auto rx=before+100000+7000;
        assert(burstNextStartUs==rx+(n==8 ? BURST_REVERSE_US : BURST_RX_US));
        assert(radio.starts==n);fake_time=burstNextStartUs-1;loop();assert(radio.starts==n);
        if(n<8) { fake_time=burstNextStartUs;loop();assert(radio.starts==n+1); }
    }
    assert(!burstQueue->pending() && burstQueue->retained()==8 && !fenced);
    Frame releases[8];
    for(unsigned n=0;n<8;n++) {
        releases[n]=burstAckRequest(20+n,3+n);command(releases[n]);drain();
        assert(burstQueue->retained()==7-n && attempted==8 && reservedUs==debt);
    }
    // Cache covers every exact release of the eight-member train, no RF replay.
    for(const auto& release:releases) {
        command(release);rows=drain();assert(rows.back().type==BURST_ACK_REPLY);
    }
    auto changed=releases[0];changed.body[25]^=1;command(changed);rows=drain();
    assert(rows.back().type==REFUSED && rows.back().body[0]==ATTEMPT_CONFLICT);
    assert(!burstQueue->retained() && radio.starts==8 && attempted==8 && reservedUs==debt);
    command(request(DISARM,30));drain();assert(!burstQueue && !armed && !fenced);
}
int main(int argc,char** argv) {
    assert(argc==2);const std::string mode=argv[1];setup();assert(ready);
    if(mode=="invalid_depth") {
        for(uint8_t depth:{0,5,255}) {
            burst_native::BootState state;memcpy(state.boot,boot,16);
            burst_native::Admission admission;memcpy(admission.binding.boot,boot,16);
            admission.binding.context_sha256[0]=1;admission.lease_deadline_us=fake_time+2000000;
            admission.max_attempts=64;admission.max_airtime_us=10000000;admission.depth=depth;
            burst_native::TxBurstQueue queue(admission,state);assert(queue.fenced());
            burst_native::Evidence evidence;evidence.binding=admission.binding;evidence.request=999;
            assert(queue.settle(evidence,fake_time)==burst_native::Code::Missing);
            assert(!queue.evidenceAt(128,evidence) && !queue.evidenceAt(7,evidence));
            assert(queue.issue(fake_time,false,true).code==burst_native::Code::Fenced);
            assert(!state.counters.attempts && !state.counters.reserved_airtime_us);
        }
        return 0;
    }
    if(mode=="capability") {
        command(request(BURST_CAPABILITIES,90));auto eight=request(BURST_CAPABILITIES,91);
        eight.size=1;eight.body[0]=8;command(eight);auto rows=drain();
        assert(rows.size()==2 && !memcmp(rows[0].body,"MBQ1\x04",5)
               && !memcmp(rows[1].body,"MBQ8\x08",5));
        assert(!armed && !attempted && !lastMutation && !radio.starts);return 0;
    }
    if(mode=="full") { fullTrain();return 0; }
    if(mode=="wrong_profile") { prepareEight(false);return 0; }
    if(mode=="open_replay") {
        prepareEight();const auto end=burstOpenDeadline;command(eightOpen());auto rows=drain();
        assert(rows.back().type==BURST_OPEN_REPLY && rows.back().size==49);
        command(burstOpenRequest());rows=drain();assert(rows.back().body[0]==ATTEMPT_CONFLICT);
        auto changed=eightOpen(3);put64(changed.body+41,end+1);command(changed);rows=drain();
        assert(rows.back().type==REFUSED && burstOpenDeadline==end && !radio.starts);return 0;
    }
    prepareEight();
    if(mode=="underfill") {
        queueEight(1);if(fake_time<burstNextStartUs) fake_time=burstNextStartUs;
        loop();assert(transmitting);burstDone();
        const auto floor=burstNextStartUs;command(eightData(4,burstEvidence().deadline_us));drain();
        fake_time=floor-1;loop();assert(radio.starts==1);
        fake_time=floor;loop();assert(radio.starts==2);return 0;
    }
    queueEight();
    if(mode=="expired_suffix") {
        loop();assert(radio.starts==1);
        // A delayed but exact TX_DONE is followed by successful RX. The paid
        // suffix no longer fits its original deadline when next considered.
        fake_time=burstEvidence().deadline_us-110000;burstDone();
        const auto rx=burstLastRxStartUs;
        assert(burstNextStartUs==rx+BURST_RX_US && burstTrainCount==1);
        fake_time=burstNextStartUs;loop();
        assert(!burstQueue->pending() && burstNextStartUs==rx+BURST_REVERSE_US);
        assert(radio.starts==1 && burstEvidence(3).state==burst_native::State::RfDone);
        for(uint64_t id=4;id<=10;id++) assert(burstEvidence(id).state==burst_native::State::NotSent);
        for(uint64_t id=3;id<=10;id++) { command(burstAckRequest(id+20,id));drain(); }
        const auto floor=burstNextStartUs;command(eightOpen(40));drain();
        assert(burstDepth==8 && burstNextStartUs==floor && !fenced);
        auto data=eightData(41,fake_time+1900000);put64(data.body,40);command(data);drain();
        fake_time=floor-1;loop();assert(radio.starts==1);
        fake_time=floor;loop();assert(radio.starts==2 && attempted==9);return 0;
    }
    if(mode=="output") {
        Serial.stalled=true;while(output.freeSlots()) output.offer(response(STATUS_REPLY,99));
        if(fake_time<burstNextStartUs) fake_time=burstNextStartUs;
        loop();assert(!radio.starts && attempted==8 && !fenced);
        Serial.stalled=false;drain();loop();assert(radio.starts==1);return 0;
    }
    if(mode=="expiry") {
        fake_time=burstEvidence().deadline_us;loop();
        for(uint64_t id=3;id<=10;id++) assert(burstEvidence(id).state==burst_native::State::NotSent);
        assert(attempted==8 && !radio.starts);return 0;
    }
    if(fake_time<burstNextStartUs) fake_time=burstNextStartUs;
    loop();assert(radio.starts==1);
    if(mode=="rx_failure") {
        radio.receive_error=-9;burstDone();assert(fenced && !armed && radio.starts==1);
        for(unsigned i=0;i<5;i++) { fake_time+=100000;loop(); }
        assert(radio.starts==1 && attempted==8);return 0;
    }
    if(mode=="cancel") {
        auto cancel=request(BURST_CANCEL,20);cancel.size=8;put64(cancel.body,burstToken);command(cancel);drain();
        assert(fenced && !armed && !transmitting && attempted==8 && reservedUs==800000);
        assert(burstEvidence(3).state==burst_native::State::Unknown);
        for(uint64_t id=4;id<=10;id++) assert(burstEvidence(id).state==burst_native::State::NotSent);
        assert(radio.starts==1);return 0;
    }
    assert(false);
}
