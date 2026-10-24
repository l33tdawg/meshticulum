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
static Frame gfskArmRequest(uint64_t id=1) {
    auto f=request(GFSK_ARM,id);f.size=47;f.body[0]='G';f.body[1]='F';f.body[2]=1;
    put32(f.body+3,921375000);put32(f.body+7,125000);put32(f.body+11,31250);put32(f.body+15,312000);
    f.body[19]=0;put16(f.body+20,32);memcpy(f.body+22,"MTGFSK01",8);f.body[30]=9;
    put32(f.body+31,2000);put32(f.body+35,3);put64(f.body+39,300000);return f;
}
static Frame txRequest(uint64_t id=2) {
    auto f=request(TRANSMIT,id); f.size=3; f.body[0]=0xc0; f.body[1]=0xdb; f.body[2]=7; return f;
}
static Frame gfsk250ArmRequest(uint64_t id=1) {
    auto f=gfskArmRequest(id);f.type=GFSK250_ARM;f.body[2]=2;
    put32(f.body+7,250000);put32(f.body+11,62500);put32(f.body+15,467000);return f;
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
static void enqueue(const Frame& frame) {
    uint8_t encoded[MAX_ENCODED]; const size_t size=encode(frame,encoded,sizeof(encoded));
    assert(size); for(size_t i=0;i<size;i++) Serial.offerInput(encoded[i]);
}
static void receivedDuringDecode() {
    radio.flags=RADIOLIB_SX126X_IRQ_RX_DONE; Serial.read_hook=nullptr;
}
static void receiveWakeDuringDecode() {
    receivedDuringDecode();assert(radio.dio1_action);radio.dio1_action();
}
static Frame burstOpenRequest(uint64_t id=2,uint64_t arm=1) {
    auto f=request(BURST_OPEN,id);f.size=48;memset(f.body,0x42,32);put64(f.body+32,arm);put64(f.body+40,deadlineUs);return f;
}
static Frame burstDataRequest(uint64_t id=3,uint64_t token=2) {
    auto f=request(BURST_DATA,id);f.size=314;put64(f.body,token);put64(f.body+8,fake_time+1500000);
    put64(f.body+16,100000);f.body[24]=1;put16(f.body+25,255);
    for(size_t n=0;n<255;n++) f.body[59+n]=uint8_t(n);
    assert(mbedtls_sha256_ret(f.body+59,255,f.body+27,0)==0);return f;
}
static Frame burstAckRequest(uint64_t id,uint64_t attempt) {
    burst_native::Evidence e;assert(burstQueue->evidence(attempt,e));
    auto f=request(BURST_ACK,id);f.size=57;put64(f.body,burstToken);put64(f.body+8,attempt);
    f.body[16]=uint8_t(e.state);put64(f.body+17,e.event_us);memcpy(f.body+25,e.digest,32);return f;
}
static burst_native::Evidence burstEvidence(uint64_t id=3) {
    burst_native::Evidence e;assert(burstQueue->evidence(id,e));return e;
}
static void burstPrepare() {
    auto f=armRequest();put32(f.body+14,10000);put32(f.body+18,32);put64(f.body+22,10000000);
    command(f);assert(armed);auto open=burstOpenRequest();put64(open.body+40,deadlineUs-100000);
    command(open);assert(burstQueue && burstToken==2);drain();
}
static void burstDone() {
    fake_time+=100000;radio.flags=RADIOLIB_SX126X_IRQ_TX_DONE;loop();assert(!transmitting);
}
static bool burstScenario(const std::string& mode) {
    if(mode.rfind("burst_",0)!=0) return false;
    if(mode=="burst_capability") {
        command(request(BURST_CAPABILITIES,90));command(request(BURST_CLOCK,91));
        auto rows=drain();assert(rows.size()==2 && rows[0].size==24 && !memcmp(rows[0].body,"MBQ1",4));
        assert(rows[0].body[4]==4 && rows[0].body[5]==7 && u32(rows[0].body+20)==200000);
        assert(rows[1].type==BURST_CLOCK_REPLY && u64(rows[1].body)==fake_time && !u64(rows[1].body+8));
        assert(!armed && !lastMutation && !attempted && !reservedUs && !radio.starts);return true;
    }
    if(mode=="burst_closed") {
        command(burstDataRequest());auto rows=drain();assert(rows.back().type==REFUSED);
        assert(!burstQueue && !attempted && !reservedUs && !radio.starts);return true;
    }
    if(mode=="burst_open_mismatch") {
        command(armRequest());auto f=burstOpenRequest();put64(f.body+32,99);command(f);
        assert(!burstQueue && lastMutation==2 && !attempted && !radio.starts);return true;
    }
    if(mode=="burst_legacy_replay") {
        auto a=armRequest();put32(a.body+14,10000);put32(a.body+18,32);put64(a.body+22,10000000);command(a);
        const auto original=txRequest();command(original);fake_time+=100000;radio.flags=1;loop();
        const auto result=previousResult;drain();command(burstOpenRequest(3));auto data=burstDataRequest(4,3);command(data);loop();burstDone();
        assert(radio.starts==2);command(original);auto rows=drain();const auto& replay=rows.back();
        assert(replay.type==TX_RESULT && replay.request==original.request && replay.size==result.size);
        assert(!memcmp(replay.body,result.body,result.size) && radio.starts==2 && attempted==2);return true;
    }
    burstPrepare();auto data=burstDataRequest();
    if(mode=="burst_open_widen") {
        const auto original=burstOpenDeadline;auto open=burstOpenRequest(3);put64(open.body+40,original+1);command(open);
        auto rows=drain();assert(rows.back().type==REFUSED && burstToken==2 && burstOpenDeadline==original && !attempted);return true;
    }
    if(mode=="burst_refusal_order") {
        auto invalid=data;invalid.request=5;invalid.body[24]=2;command(invalid);assert(lastMutation==5);
        command(data);auto rows=drain();assert(rows.back().type==REFUSED && rows.back().body[0]==OLD_ATTEMPT && !attempted);return true;
    }
    if(mode=="burst_bad_digest" || mode=="burst_bad_estimate" || mode=="burst_bad_role" || mode=="burst_bad_token" || mode=="burst_bad_deadline") {
        if(mode=="burst_bad_digest") data.body[27]^=1;
        if(mode=="burst_bad_estimate") put64(data.body+16,99999);
        if(mode=="burst_bad_role") data.body[24]=2;
        if(mode=="burst_bad_token") put64(data.body,99);
        if(mode=="burst_bad_deadline") put64(data.body+8,deadlineUs+1);
        command(data);loop();assert(!attempted && !reservedUs && !radio.starts);return true;
    }
    command(data);assert(attempted==1 && reservedUs==100000 && !radio.starts);
    if(mode=="burst_replay") {
        const auto e=burstEvidence();fake_time+=10;command(data);assert(burstEvidence().event_us==e.event_us && attempted==1);
        data.body[59]^=1;command(data);auto rows=drain();assert(rows.back().type==REFUSED && rows.back().body[0]==ATTEMPT_CONFLICT);
        assert(attempted==1 && !radio.starts);return true;
    }
    if(mode=="burst_output_priority") {
        Serial.stalled=true;while(output.freeSlots()) assert(output.offer(response(STATUS_REPLY,90)));
        radio.flags=RADIOLIB_SX126X_IRQ_RX_DONE;const auto drops=droppedRx;loop();
        assert(!radio.starts && burstEvidence().state==burst_native::State::Queued && droppedRx==drops);
        assert((radio.flags&RADIOLIB_SX126X_IRQ_RX_DONE) && !radio.receiving);Serial.stalled=false;loop();auto rows=drain();
        bool rx=false;for(const auto& row:rows) { if(row.type==RECEIVE) rx=true;if(row.type==BURST_EVENT && row.body[8]==3) assert(rx); }
        assert(rx && !droppedRx);return true;
    }
    if(mode=="burst_header_priority") {
        radio.flags=RADIOLIB_SX126X_IRQ_HEADER_VALID;loop();assert(!radio.starts);
        fake_time+=100000;loop();assert(!radio.starts);
        radio.flags=RADIOLIB_SX126X_IRQ_RX_DONE;loop();assert(radio.starts==1);
        auto rows=drain();bool rx=false;for(const auto& row:rows) if(row.type==RECEIVE) rx=true;assert(rx);return true;
    }
    if(mode=="burst_header_rearm_age") {
        radio.flags=RADIOLIB_SX126X_IRQ_HEADER_VALID;loop();assert(!radio.starts && burstHeaderSeenUs);
        fake_time+=190000;radio.irq_on_receive=RADIOLIB_SX126X_IRQ_HEADER_VALID;
        radio.flags=RADIOLIB_SX126X_IRQ_RX_DONE;loop();assert(!radio.starts && burstHeaderSeenUs);
        fake_time+=20000;loop();assert(!radio.starts && !droppedRx);
        radio.flags=RADIOLIB_SX126X_IRQ_RX_DONE;loop();assert(radio.starts==1 && !droppedRx);return true;
    }
    if(mode=="burst_stale_header") {
        radio.flags=RADIOLIB_SX126X_IRQ_HEADER_VALID;loop();assert(!radio.starts);
        fake_time+=201000;loop();assert(!radio.starts && droppedRx==1);
        loop();assert(radio.starts==1 && !fenced);return true;
    }
    if(mode=="burst_cancel_queued" || mode=="burst_expiry_queued") {
        if(mode=="burst_cancel_queued") { auto f=request(BURST_CANCEL,4);f.size=8;put64(f.body,2);command(f); }
        else { fake_time=u64(data.body+8);loop(); }
        assert(burstEvidence().state==burst_native::State::NotSent && !radio.starts && attempted==1 && reservedUs==100000);
        assert(!burstQueue->containmentRequired());return true;
    }
    if(mode=="burst_four_train" || mode=="burst_dwell_rearm") {
        for(uint64_t id=4;id<=6;id++) { auto next=data;next.request=id;command(next);drain(); }
        assert(attempted==4 && !radio.starts);loop();assert(radio.starts==1);
        if(mode=="burst_dwell_rearm") radio.receive_advance_us=7000;
        for(unsigned n=1;n<=4;n++) {
            const uint64_t before=fake_time;burstDone();const uint64_t readyAt=before+100000+radio.receive_advance_us;
            assert(burstNextStartUs==readyAt+(n==4 ? BURST_REVERSE_US : BURST_RX_US));
            assert(radio.starts==n);fake_time=burstNextStartUs-1;loop();assert(radio.starts==n);
            if(n<4) { fake_time=burstNextStartUs;loop();assert(radio.starts==n+1); }
        }
        assert(!burstQueue->pending() && burstQueue->retained()==4 && attempted==4 && !fenced);return true;
    }
    if(mode=="burst_start_failure") radio.start_error=-9;
    if(mode=="burst_clear_failure") radio.clear_error=-9;
    if(mode=="burst_irq_failure") radio.irq_error=-9;
    if(mode=="burst_standby_rx") { radio.irq_on_standby=RADIOLIB_SX126X_IRQ_RX_DONE;radio.shared_fifo=true;radio.overwrite_during_read=true; }
    if(mode=="burst_standby_header") radio.irq_on_standby=RADIOLIB_SX126X_IRQ_HEADER_VALID;
    loop();
    if(mode=="burst_start_failure" || mode=="burst_clear_failure" || mode=="burst_irq_failure" || mode=="burst_standby_header") {
        assert(!transmitting && fenced && !burstQueue->containmentRequired() && attempted==1 && reservedUs==100000);
        assert(burstEvidence().state==(mode=="burst_irq_failure" ? burst_native::State::NotSent : burst_native::State::Unknown));
        assert(board.before==board.after && radio.starts==unsigned(mode=="burst_start_failure"));return true;
    }
    assert(radio.starts==1 && transmitting);
    if(mode=="burst_standby_rx") {
        auto rows=drain();bool preserved=false;for(const auto& row:rows) if(row.type==RECEIVE) {
            const uint8_t expected[]={0xc0,0xdb,0,255};assert(row.size==8 && !memcmp(row.body+4,expected,4));preserved=true;
        }
        assert(preserved && !droppedRx);return true;
    }
    if(mode=="burst_finish_failure") {
        radio.finish_error=-9;burstDone();assert(burstEvidence().state==burst_native::State::Unknown && fenced);
        assert(board.before==board.after && !burstQueue->containmentRequired());return true;
    }
    if(mode=="burst_clock_regression" || mode=="burst_debt_regression") {
        if(mode=="burst_clock_regression") fake_time-=5000;else reservedUs--;
        loop();assert(burstEvidence().state==burst_native::State::Unknown && fenced && !transmitting);return true;
    }
    if(mode=="burst_cancel_started" || mode=="burst_containment_retry" || mode=="burst_expiry_started" || mode=="burst_serial_failure") {
        if(mode=="burst_expiry_started") { fake_time=u64(data.body+8);loop(); }
        else if(mode=="burst_serial_failure") { Serial.forced_read_count=size_t(-1);loop(); }
        else {
            if(mode=="burst_containment_retry") radio.warm_error=-9;
            auto f=request(BURST_CANCEL,4);f.size=8;put64(f.body,2);command(f);
        }
        assert(burstEvidence().state==burst_native::State::Unknown && fenced && !transmitting && attempted==1);
        if(mode=="burst_containment_retry") {
            assert(burstQueue->containmentRequired());const auto receipt=burstCancelReceipt;
            loop();assert(burstQueue->containmentRequired());radio.warm_error=0;loop();assert(!burstQueue->containmentRequired());
            auto f=request(BURST_CANCEL,4);f.size=8;put64(f.body,2);command(f);auto rows=drain();
            assert(rows.back().type==BURST_CANCEL_REPLY && !memcmp(rows.back().body,receipt.body,10));
        }
        radio.flags=1;loop();assert(burstEvidence().state==burst_native::State::Unknown && radio.starts==1 && fenced);return true;
    }
    burstDone();assert(burstEvidence().state==burst_native::State::RfDone);
    if(mode=="burst_reverse_legacy") {
        command(txRequest(4));auto rows=drain();assert(rows.back().type==REFUSED && rows.back().body[0]==mtbench::BUSY);
        assert(radio.starts==1 && attempted==1);fake_time=burstNextStartUs;command(txRequest(5));
        assert(radio.starts==2 && attempted==2 && transmitting);
        auto cancel=request(BURST_CANCEL,6);cancel.size=8;put64(cancel.body,2);command(cancel);
        assert(previousResult.request==5 && previousResult.body[0]==UNKNOWN && !transmitting && fenced);
        assert(burstEvidence().state==burst_native::State::RfDone && board.before==board.after);return true;
    }
    if(mode=="burst_disarm_containment") {
        command(burstAckRequest(4,3));radio.warm_error=-9;command(request(DISARM,5));
        assert(burstQueue && burstQueue->containmentRequired() && serialContainmentPending && fenced);
        loop();assert(burstQueue->containmentRequired());radio.warm_error=0;loop();
        assert(!burstQueue->containmentRequired() && fenced);command(request(DISARM,6));assert(!burstQueue && fenced);return true;
    }
    if(mode=="burst_timestamp") {
        const auto event=burstEvent(burstEvidence());fake_time+=1234;
        auto query=request(BURST_QUERY,3);query.size=8;put64(query.body,2);command(query);auto rows=drain();
        assert(rows.back().size==81 && !memcmp(rows.back().body,event.body,81));return true;
    }
    if(mode=="burst_ack_replay" || mode=="burst_codec") {
        auto ack=burstAckRequest(4,3);command(ack);assert(!burstQueue->retained());
        command(ack);ack.request=5;command(ack);assert(!burstQueue->retained() && lastMutation==5);
        if(mode=="burst_ack_replay") {
            auto bad=ack;bad.request=6;bad.body[25]^=1;command(bad);auto rows=drain();assert(rows.back().body[0]==ATTEMPT_CONFLICT);
            command(request(DISARM,7));rows=drain();assert(rows.back().type==DISARM_REPLY && !rows.back().body[0] && !fenced && !burstQueue);
            command(armRequest(8));assert(armed);command(txRequest(9));assert(radio.starts==2 && attempted==2);return true;
        }
        auto status=request(BURST_STATUS,90);status.size=8;put64(status.body,2);command(status);
        auto rows=drain();for(const auto& row:rows) { uint8_t encoded[MAX_ENCODED];const auto n=encode(row,encoded,sizeof(encoded));std::cout.write(reinterpret_cast<char*>(encoded),n); }
        return true;
    }
    assert(false);return true;
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
    if(mode=="usb_rx_allocation_failure") Serial.rx_allocation_failure=true;
    setup();
    if(mode=="usb_rx_allocation_failure") {
        assert(!Serial.begun && !serialReady && !ready && !armed && !radio.starts);
        Serial.input.push_back(END); // Even unexpected input cannot enter decoding.
        loop(); assert(Serial.input.size()==1 && !radio.starts && !attempted && !reservedUs);
        assert(u64(boot+8)==0); return 0;
    }
    if(mode=="boot_failure") {
        command(armRequest()); command(txRequest());
        assert(!ready && !armed && !radio.starts); return 0;
    }
    assert(ready && !armed && !radio.starts && u64(boot+8)==18);
    assert(Serial.begun && serialReady && Serial.rx_capacity==2048);
    if(burstScenario(mode)) return 0;
    if(mode=="radio_notify_wakeup") {
        assert(radio.dio1_action==radioWake && radioServiceTask==fake_loop_task);
        const auto time=fake_time;
        radio.dio1_action();assert(fake_notifications==1 && fake_notify_yields==1);
        assert(!armed && !radio.starts && !attempted && !reservedUs);
        loop();assert(fake_time==time && fake_notifications==0 && fake_notify_waits==1);
        loop();assert(fake_time==time+1000 && fake_notify_waits==2);
        assert(!armed && !radio.starts && !attempted && !reservedUs);return 0;
    }
    if(mode=="radio_notify_decode") {
        command(armRequest());assert(armed);drain();
        const auto time=fake_time,end=deadlineUs,mutation=lastMutation;
        enqueue(request(STATUS,3));Serial.read_hook=receiveWakeDuringDecode;
        loop();assert(fake_time==time && fake_notifications==0 && !attempted && !reservedUs);
        loop();auto rows=drain();unsigned receive=0;
        for(const auto& row:rows) if(row.type==RECEIVE) receive++;
        assert(receive==1 && armed && !fenced && deadlineUs==end && lastMutation==mutation);
        assert(!radio.starts && !attempted && !reservedUs && fake_time==time+1000);return 0;
    }
    if(mode=="radio_notify_serial_failure") {
        armAndTx();const auto count=attempted,maximum=capCount;
        const auto paid=reservedUs,end=deadlineUs,mutation=lastMutation,budget=capUs;
        Serial.forced_read_count=size_t(-1);radio.dio1_action();loop();
        assert(!serialReady && fenced && !armed && !transmitting && previousResult.body[0]==UNKNOWN);
        enqueue(armRequest(3));enqueue(txRequest(4));const size_t queued=Serial.input.size();
        radio.flags=RADIOLIB_SX126X_IRQ_TX_DONE;radio.dio1_action();loop();
        assert(Serial.input.size()==queued && previousResult.body[0]==UNKNOWN && radio.starts==1);
        assert(attempted==count && reservedUs==paid && deadlineUs==end && lastMutation==mutation
            && capCount==maximum && capUs==budget);return 0;
    }
    if(mode=="usb_rx_read_error_idle" || mode=="usb_rx_read_error_started"
        || mode=="usb_rx_read_error_oversized" || mode=="usb_rx_read_error_standby_failure"
        || mode=="usb_rx_read_error_backpressure") {
        const bool started=mode!="usb_rx_read_error_idle" && mode!="usb_rx_read_error_oversized";
        if(started) armAndTx();
        enqueue(armRequest(3));enqueue(txRequest(4));
        const size_t queued=Serial.input.size();
        const auto paid=reservedUs,end=deadlineUs,mutation=lastMutation;
        const auto count=attempted,maximum=capCount;const auto budget=capUs;
        Serial.forced_read_count=mode=="usb_rx_read_error_oversized" ? USB_RX_DRAIN_BYTES+1 : size_t(-1);
        if(mode=="usb_rx_read_error_standby_failure") { radio.warm=true;radio.warm_error=-9; }
        if(mode=="usb_rx_read_error_backpressure") {
            Serial.stalled=true;
            while(output.freeSlots()) assert(output.offer(response(STATUS_REPLY,99)));
        }
        loop();
        assert(!serialReady && fenced && !armed && !transmitting);
        assert(Serial.input.size()==queued && Serial.rx_capacity==2048 && Serial.rx_buffer_calls==1);
        assert(attempted==count && reservedUs==paid && deadlineUs==end && lastMutation==mutation
            && capCount==maximum && capUs==budget && radio.starts==unsigned(started));
        if(started) {
            assert(previousRequest==2 && previousResult.body[0]==UNKNOWN);
            assert(u64(previousResult.body+33)==100000);
            assert(int16_t(u16(previousResult.body+53))==(mode=="usb_rx_read_error_standby_failure" ? -9 : -1));
        }
        const auto read_calls=Serial.bulk_read_calls;
        if(mode=="usb_rx_read_error_standby_failure") {
            assert(serialContainmentPending && radio.warm);loop();
            assert(serialContainmentPending && radio.warm);
            radio.warm_error=0;
        }
        if(mode=="usb_rx_read_error_backpressure") { assert(resultPending);Serial.stalled=false; }
        radio.flags=RADIOLIB_SX126X_IRQ_TX_DONE;if(fake_time<=end) fake_time=end+1;
        for(unsigned n=0;n<3;n++) loop();
        assert(!serialContainmentPending && !radio.warm && Serial.bulk_read_calls==read_calls);
        assert(Serial.input.size()==queued && Serial.rx_buffer_calls==1 && radio.starts==unsigned(started));
        assert(attempted==count && reservedUs==paid && deadlineUs==end && capCount==maximum && capUs==budget);
        auto rows=drain();unsigned unknown=0,completed=0;
        for(const auto& row:rows) if(row.type==TX_RESULT) {
            if(row.body[0]==UNKNOWN) { unknown++;assert(row.request==2); }
            if(row.body[0]==RF_DONE) completed++;
        }
        assert(unknown==unsigned(started) && completed==0 && !resultPending && fenced);
        return 0;
    }
    if(mode=="usb_rx_escaped_burst") {
        command(gfsk250ArmRequest()); auto first=txRequest(); first.size=255;
        for(size_t i=0;i<first.size;i++) first.body[i]=(i%2) ? ESC : END;
        auto second=first; second.request=3; second.body[254]=ESC;
        enqueue(first);enqueue(second);
        assert(Serial.input.size()>256 && Serial.input.size()<=USB_RX_BYTES && !Serial.dropped_input);
        for(unsigned n=0;n<32 && !Serial.input.empty();n++) {
            if(transmitting) radio.flags=RADIOLIB_SX126X_IRQ_TX_DONE;
            loop();
            if(previousRequest==first.request) assert(!memcmp(previousPacket,first.body,255));
        }
        assert(Serial.input.empty() && Serial.largest_read==USB_RX_DRAIN_BYTES);
        radio.flags=RADIOLIB_SX126X_IRQ_TX_DONE;loop();
        assert(radio.starts==2 && attempted==2 && reservedUs==2*mtbench::gfsk250::airtimeUs(255));
        assert(previousRequest==second.request && !memcmp(previousPacket,second.body,255));
        auto rows=drain();unsigned finished=0;
        for(const auto& row:rows) if(row.type==TX_RESULT && row.body[0]==RF_DONE) finished++;
        assert(finished==2 && !transmitting && !fenced);return 0;
    }
    if(mode=="usb_rx_queue_bound") {
        for(size_t n=0;n<USB_RX_BYTES+64;n++) Serial.offerInput(0x55);
        assert(Serial.input.size()==USB_RX_BYTES && Serial.dropped_input==64);
        const auto before=Serial.input.size();loop();
        assert(Serial.input.size()==before-USB_RX_DRAIN_BYTES && !radio.starts && !attempted && !reservedUs);
        return 0;
    }
    if(mode=="usb_rx_legacy_queue_overflow") {
        FakeSerial legacy;for(size_t n=0;n<MAX_ENCODED;n++) legacy.offerInput(END);
        assert(legacy.input.size()==256 && legacy.dropped_input==MAX_ENCODED-256);
        return 0;
    }
    if(mode=="gfsk250_roundtrip") {
        const auto oldMutation=lastMutation,oldDeadline=deadlineUs;
        command(request(GFSK250_CAPABILITIES,99));auto rows=drain();
        assert(rows.back().type==GFSK250_CAPABILITIES_REPLY && rows.back().size==16);
        uint8_t capability[16]={};mtbench::gfsk250::capability(capability);
        assert(!memcmp(rows.back().body,capability,16) && lastMutation==oldMutation
            && deadlineUs==oldDeadline && !armed && !attempted && !reservedUs && !radio.starts);
        command(gfsk250ArmRequest());assert(armed && gfskMode && gfsk250Mode && radio.gfsk_rate==250000);
        rows=drain();assert(rows.back().type==GFSK250_ARM_REPLY && rows.back().body[2]==2);
        command(request(GFSK250_STATUS,90));rows=drain();assert(rows.back().type==GFSK250_STATUS_REPLY && rows.back().size==66);
        command(request(GFSK_STATUS,91));rows=drain();assert(rows.back().type==REFUSED && rows.back().body[0]==BAD_COMMAND);
        radio.flags=RADIOLIB_SX126X_IRQ_RX_DONE;loop();rows=drain();assert(rows.back().type==GFSK250_RECEIVE && rows.back().size==6);
        command(txRequest());assert(attempted==1 && reservedUs==576);radio.flags=RADIOLIB_SX126X_IRQ_TX_DONE;loop();
        command(txRequest());assert(radio.starts==1 && attempted==1 && reservedUs==576);
        command(request(DISARM,3));command(gfskArmRequest(4));
        assert(armed && gfskMode && !gfsk250Mode && radio.gfsk_rate==125000 && attempted==1 && reservedUs==576);
        return 0;
    }
    if(mode=="gfsk250_version_mismatch" || mode=="gfsk250_opcode_mismatch") {
        auto f=gfsk250ArmRequest();if(mode=="gfsk250_version_mismatch") f.body[2]=1;else f.type=GFSK_ARM;
        command(f);assert(fenced && !armed && !attempted && !reservedUs && !radio.starts);
        return 0;
    }
    if(mode=="gfsk250_expiry_and_paid_budget") {
        command(gfsk250ArmRequest());const auto end=deadlineUs;
        command(txRequest());assert(attempted==1 && reservedUs==576);
        radio.flags=RADIOLIB_SX126X_IRQ_TX_DONE;loop();
        fake_time=end+1;loop();assert(!armed && !radio.warm && attempted==1 && reservedUs==576);
        command(gfsk250ArmRequest());assert(!armed && deadlineUs==end && attempted==1 && reservedUs==576);
        return 0;
    }
    if(mode=="gfsk_roundtrip") {
        command(gfskArmRequest());assert(armed && gfskMode && radio.gfsk);auto rows=drain();assert(rows.back().type==GFSK_ARM_REPLY && rows.back().size==47);
        auto status=request(GFSK_STATUS,50);command(status);rows=drain();assert(rows.back().type==GFSK_STATUS_REPLY && rows.back().size==66 && rows.back().body[63]);
        radio.flags=RADIOLIB_SX126X_IRQ_RX_DONE;loop();rows=drain();assert(rows.back().type==GFSK_RECEIVE && rows.back().size==6);
        command(txRequest());assert(attempted==1 && reservedUs==1152);radio.flags=RADIOLIB_SX126X_IRQ_TX_DONE;loop();
        command(txRequest());assert(radio.starts==1 && reservedUs==1152);
        command(request(DISARM,3));assert(!armed && !radio.warm);command(armRequest(4));assert(armed && !gfskMode && !radio.gfsk && attempted==1 && reservedUs==1152);
        return 0;
    }
    if(mode=="gfsk_failed_or_replayed") {
        auto f=gfskArmRequest();f.body[7]=1;command(f);assert(fenced && !armed && !radio.starts);
        command(gfskArmRequest(1));assert(!armed && !radio.starts);return 0;
    }
    if(mode=="gfsk_expiry") {
        command(gfskArmRequest());const auto end=deadlineUs;fake_time=end+1;loop();assert(!armed && !radio.warm);
        command(gfskArmRequest());assert(!armed && deadlineUs==end);return 0;
    }
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
    } else if(mode=="rx_before_queued_tx" || mode=="rx_during_decode" || mode=="rx_at_standby") {
        command(armRequest()); drain(); const auto expected=radio.received; radio.shared_fifo=true;
        if(mode=="rx_before_queued_tx") radio.flags=RADIOLIB_SX126X_IRQ_RX_DONE;
        if(mode=="rx_during_decode") Serial.read_hook=receivedDuringDecode;
        if(mode=="rx_at_standby") radio.irq_on_standby=RADIOLIB_SX126X_IRQ_RX_DONE;
        enqueue(txRequest()); loop(); auto frames=drain();
        assert(frames.size()==2 && frames[0].type==RECEIVE && frames[1].type==TX_RESULT);
        assert(frames[0].size==expected.size()+4 && memcmp(frames[0].body+4,expected.data(),expected.size())==0);
        assert(frames[1].body[0]==STARTED && radio.starts==1 && attempted==1 && reservedUs==100000);
    } else if(mode=="rx_fifo_frozen") {
        command(armRequest()); drain(); const auto expected=radio.received;
        radio.overwrite_during_read=true; radio.flags=RADIOLIB_SX126X_IRQ_RX_DONE;
        loop(); auto frames=drain();
        assert(frames.size()==1 && frames[0].type==RECEIVE);
        assert(memcmp(frames[0].body+4,expected.data(),expected.size())==0 && droppedRx==0);
    } else if(mode=="rx_priority_crc_rejected") {
        command(armRequest()); drain(); radio.flags=RADIOLIB_SX126X_IRQ_RX_DONE|RADIOLIB_SX126X_IRQ_CRC_ERR;
        enqueue(txRequest()); loop(); auto frames=drain();
        assert(frames.size()==1 && frames[0].type==TX_RESULT && frames[0].body[0]==STARTED);
        assert(droppedRx==1 && attempted==1 && radio.starts==1 && reservedUs==100000);
    } else if(mode=="rx_priority_irq_failure") {
        command(armRequest()); drain(); radio.flags=RADIOLIB_SX126X_IRQ_RX_DONE; radio.irq_error=-11;
        enqueue(txRequest()); loop(); auto frames=drain();
        assert(frames.size()==1 && frames[0].type==REFUSED && frames[0].body[0]==FENCED);
        assert(!armed && fenced && !radio.starts && attempted==0 && reservedUs==0);
    } else if(mode=="rx_priority_lease_expiry") {
        command(armRequest()); drain(); radio.flags=RADIOLIB_SX126X_IRQ_RX_DONE; radio.read_advance_us=2000001;
        enqueue(txRequest()); loop(); auto frames=drain();
        assert(!armed && !radio.starts && attempted==0 && reservedUs==0);
        assert(frames.back().type==REFUSED && frames.back().body[0]==NOT_ARMED);
    } else if(mode=="rx_boundary_lease_expiry") {
        command(armRequest()); drain(); radio.irq_on_standby=RADIOLIB_SX126X_IRQ_RX_DONE; radio.read_advance_us=2000001;
        command(txRequest());
        assert(!armed && fenced && !radio.starts && attempted==1 && reservedUs==100000);
        assert(previousResult.body[0]==UNKNOWN);
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
    } else if(mode=="sf5_profile") {
        auto f=armRequest(); put32(f.body,921375000u); put32(f.body+4,500000u); f.body[8]=5;
        put16(f.body+11,12);
        command(f); assert(armed && profile[8]==5 && radio.starts==0);
        assert(radio.warm);
        command(request(DISARM,2)); assert(!armed && !radio.warm && !radio.starts);
    } else if(mode=="sf5_short_preamble") {
        auto f=armRequest(); put32(f.body,921375000u); put32(f.body+4,500000u); f.body[8]=5;
        command(f); assert(!armed && !radio.warm && !radio.starts);
        auto frames=drain(); assert(frames.back().type==REFUSED && frames.back().body[0]==BAD_PROFILE);
    } else if(mode=="rx_error_then_good" || mode=="rx_crc_then_good" || mode=="rx_timeout_then_good" || mode=="rx_error_rearm_failure") {
        auto f=armRequest(); f.body[8]=5; put16(f.body+11,12); command(f); drain();
        assert(armed && radio.warm && radio.receive_starts==1);
        radio.flags=mode=="rx_timeout_then_good" ? RADIOLIB_SX126X_IRQ_TIMEOUT
                    : mode=="rx_crc_then_good" ? RADIOLIB_SX126X_IRQ_CRC_ERR : RADIOLIB_SX126X_IRQ_HEADER_ERR;
        if(mode=="rx_error_rearm_failure") radio.receive_error=-11;
        loop(); assert(drain().empty() && !radio.starts && radio.receive_starts==2);
        if(mode=="rx_error_rearm_failure") assert(!armed && fenced && !radio.warm);
        else {
            assert(armed && !fenced && radio.warm && radio.flags==0);
            assert(droppedRx==(mode=="rx_timeout_then_good" ? 0u : 1u));
            radio.flags=RADIOLIB_SX126X_IRQ_RX_DONE; loop();
            auto frames=drain(); assert(frames.size()==1 && frames[0].type==RECEIVE && frames[0].size==8);
            assert(radio.receive_starts==3 && armed && !radio.starts);
        }
    } else if(mode=="rx_stale_header_then_valid" || mode=="rx_crc_valid_header_in_progress") {
        command(armRequest()); drain(); radio.flags=RADIOLIB_SX126X_IRQ_HEADER_VALID
            | (mode=="rx_stale_header_then_valid" ? RADIOLIB_SX126X_IRQ_HEADER_ERR : RADIOLIB_SX126X_IRQ_CRC_ERR);
        loop(); assert(droppedRx==0 && radio.receive_starts==1 && drain().empty());
        radio.flags|=RADIOLIB_SX126X_IRQ_RX_DONE; loop(); auto frames=drain();
        if(mode=="rx_stale_header_then_valid") assert(frames.size()==1 && frames[0].type==RECEIVE && droppedRx==0);
        else assert(frames.empty() && droppedRx==1 && radio.receive_starts==2);
    } else if(mode=="rx_crc_done_refused") {
        command(armRequest()); drain(); radio.flags=RADIOLIB_SX126X_IRQ_CRC_ERR|RADIOLIB_SX126X_IRQ_RX_DONE;
        loop(); assert(drain().empty() && droppedRx==1 && radio.receive_starts==2 && !radio.starts);
    } else if(mode=="warm_config_failure") {
        auto f=armRequest(); put32(f.body,921375000u); put32(f.body+4,500000u); f.body[8]=5; put16(f.body+11,12);
        radio.warm_error=-11; command(f); assert(!armed && fenced && !radio.starts);
    } else if(mode=="warm_lease_expiry" || mode=="warm_tx_fence") {
        auto f=armRequest(); put32(f.body,921375000u); put32(f.body+4,500000u); f.body[8]=5; put16(f.body+11,12);
        command(f); assert(armed && radio.warm);
        if(mode=="warm_lease_expiry") fake_time=deadlineUs+1;
        else { command(txRequest()); radio.flags=RADIOLIB_SX126X_IRQ_TIMEOUT; }
        loop(); assert(!armed && !radio.warm);
        if(mode=="warm_tx_fence") assert(fenced && previousResult.body[0]==UNKNOWN && attempted==1 && reservedUs==100000);
    } else assert(false);
    return 0;
}
