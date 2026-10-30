// Real firmware branches with deterministic fake radio/output; no devices.
#define main existing_fault_main
#include "harness.cpp"
#undef main
using R=mtbench::profiling::RxReason;
static uint32_t expected[mtbench::profiling::RX_REASONS]={};
static void observed(R reason,uint32_t count=1) { expected[uint8_t(reason)]+=count; }
static void fillOutput() { while(output.freeSlots()) assert(output.offer(response(STATUS_REPLY,90))); }
static void printFrames(const std::vector<Frame>& frames) {
    for(const auto& frame:frames) {
        uint8_t bytes[MAX_ENCODED];const auto n=encode(frame,bytes,sizeof(bytes));assert(n);
        std::cout.write(reinterpret_cast<char*>(bytes),n);
    }
}
static Frame reasonQuery(uint64_t id=100) {
    auto f=request(NATIVE_PROFILE,id);f.size=1;f.body[0]=0xfd;return f;
}
#if MTBENCH_NATIVE_PROFILE
static void refused(uint8_t reason) {
    const auto clocks=fake_clock_reads;const auto mutation=lastMutation,paid=reservedUs;
    const auto attempts=attempted,starts=radio.starts,rx=radio.receive_starts;
    command(reasonQuery());const auto frames=drain();
    assert(frames.size()==1 && frames[0].type==REFUSED && frames[0].body[0]==reason);
    assert(clocks==fake_clock_reads && mutation==lastMutation && paid==reservedUs
           && attempts==attempted && starts==radio.starts && rx==radio.receive_starts);
    assert(!fake_heap_queries && !fake_stack_queries);
}
#endif
int main(int argc,char** argv) {
    assert(argc==2);const std::string mode=argv[1];setup();
    if(mode=="disabled") {
#if !MTBENCH_NATIVE_PROFILE
        unsigned evaluations=0;const auto clocks=fake_clock_reads;
        MT_RX_COUNT(NotEvenAnEnum);MT_RX_READ_FAILURE(++evaluations,++evaluations);
        MT_RX_LENGTH_OR_RESERVE(++evaluations);MT_RX_RESTART(++evaluations);
        assert(!evaluations && clocks==fake_clock_reads);
        command(reasonQuery());const auto frames=drain();assert(frames.size()==1);
        assert(frames[0].type==REFUSED && frames[0].body[0]==BAD_COMMAND);
#else
        assert(false);
#endif
        return 0;
    }
    if(mode=="query" || mode=="wire" || mode=="saturation") {
#if MTBENCH_NATIVE_PROFILE
        armed=true;refused(BUSY);armed=false;
        transmitting=true;refused(BUSY);transmitting=false;
        resultPending=true;refused(BUSY);resultPending=false;
        serialContainmentPending=true;refused(BUSY);serialContainmentPending=false;
        burstBoot.containment_required=true;refused(BUSY);burstBoot.containment_required=false;
        auto wrong=reasonQuery();wrong.boot[0]^=1;command(wrong);
        auto frames=drain();assert(frames.size()==1 && frames[0].body[0]==WRONG_BOOT);
        auto malformed=reasonQuery();malformed.size=2;command(malformed);
        frames=drain();assert(frames.size()==1 && frames[0].body[0]==BAD_COMMAND);
        if(mode=="saturation") {
            auto& reasons=nativeProfile.rx;
            for(uint8_t n=0;n<mtbench::profiling::RX_REASONS;n++) {
                reasons.counts[n]=UINT32_MAX-1;reasons.record(R(n));
                assert(reasons.counts[n]==UINT32_MAX && !(reasons.saturated_mask&(uint32_t(1)<<n)));
                reasons.record(R(n));reasons.record(R(n));
                assert(reasons.counts[n]==UINT32_MAX && (reasons.saturated_mask&(uint32_t(1)<<n)));
            }
            reasons.record(R(255));assert(reasons.saturated_mask==0x1ffff);
        } else {
            for(uint8_t n=0;n<mtbench::profiling::RX_REASONS;n++)
                for(uint8_t count=0;count<n;count++) nativeProfile.rx.record(R(n));
        }
        const auto clocks=fake_clock_reads;const auto mutation=lastMutation,paid=reservedUs;
        command(reasonQuery());frames=drain();assert(frames.size()==1);
        const auto first=frames[0];assert(first.type==NATIVE_PROFILE_REPLY && first.size==80);
        assert(!memcmp(first.body,"MNR1\x01\x11\x04\x01",8));
        assert(clocks==fake_clock_reads && mutation==lastMutation && paid==reservedUs
               && !attempted && !radio.starts && !fake_heap_queries && !fake_stack_queries);
        command(reasonQuery());frames=drain();assert(frames.size()==1);
        assert(frames[0].size==first.size && !memcmp(first.body,frames[0].body,first.size));
        if(mode!="query") printFrames(frames);
        // Both queued and terminal retained records keep the same read-only gate.
        burstPrepare();command(burstDataRequest());drain();armed=false;refused(BUSY);armed=true;
        burstService(true);drain();burstDone();drain();armed=false;refused(BUSY);
#else
        assert(false);
#endif
        return 0;
    }
    const bool queued=mode=="header_stall" || mode=="backpressure";
    if(queued) { burstPrepare();command(burstDataRequest());drain(); }
    else { command(mode=="gfsk_export" || mode=="gfsk_preempt" || mode=="gfsk_stale_header"
                   ? gfskArmRequest() : armRequest());drain();assert(armed); }
    if(mode=="header_stall") {
        radio.flags=RADIOLIB_SX126X_IRQ_HEADER_VALID;burstService(true);
        assert(burstHeaderSeenUs && !droppedRx && !radio.starts);
        fake_time+=200001;burstService(true);observed(R::QueuedHeaderStall);
        assert(droppedRx==1 && !burstHeaderSeenUs && !radio.starts && armed && !fenced);
    } else if(mode=="backpressure") {
        while(output.freeSlots()>2) assert(output.offer(response(STATUS_REPLY,90)));
        radio.flags=RADIOLIB_SX126X_IRQ_RX_DONE;serviceRadio();serviceRadio();
        observed(R::BackpressureDeferVisits,2);
        assert(!droppedRx && !radio.receiving && (radio.flags&RADIOLIB_SX126X_IRQ_RX_DONE));
        drain();serviceRadio();observed(R::RxExported);assert(!droppedRx && radio.receiving);
    } else if(mode=="sync_preempt" || mode=="gfsk_preempt" || mode=="sync_done") {
        radio.flags=gfskMode ? RADIOLIB_SX126X_IRQ_SYNC_WORD_VALID : RADIOLIB_SX126X_IRQ_HEADER_VALID;
        if(mode=="sync_done") radio.flags|=RADIOLIB_SX126X_IRQ_RX_DONE;
        command(txRequest());observed(mode=="sync_done" ? R::RxExported : R::SyncTxHeaderPreempted);
        assert(transmitting && attempted==1 && !fenced);
    } else if(mode.rfind("irq_",0)==0) {
        if(mode=="irq_crc") { radio.flags=RADIOLIB_SX126X_IRQ_CRC_ERR;observed(R::IrqRestartCrcOnly); }
        else if(mode=="irq_header") { radio.flags=RADIOLIB_SX126X_IRQ_HEADER_ERR;observed(R::IrqRestartHeaderOnly); }
        else if(mode=="irq_both") { radio.flags=RADIOLIB_SX126X_IRQ_CRC_ERR|RADIOLIB_SX126X_IRQ_HEADER_ERR;observed(R::IrqRestartBoth); }
        else if(mode=="irq_timeout") { radio.flags=RADIOLIB_SX126X_IRQ_TIMEOUT;observed(R::TimeoutOnlyRestart); }
        else if(mode=="irq_stale") radio.flags=RADIOLIB_SX126X_IRQ_HEADER_VALID|RADIOLIB_SX126X_IRQ_HEADER_ERR;
        else if(mode=="irq_timeout_stale") {
            radio.flags=RADIOLIB_SX126X_IRQ_HEADER_VALID|RADIOLIB_SX126X_IRQ_HEADER_ERR|RADIOLIB_SX126X_IRQ_TIMEOUT;
            observed(R::IrqRestartHeaderOnly);
        } else assert(false);
        serviceRadio();assert(armed && !fenced && !attempted);
    } else {
        radio.flags=RADIOLIB_SX126X_IRQ_RX_DONE;
        if(mode=="read_crc") { radio.flags|=RADIOLIB_SX126X_IRQ_CRC_ERR;observed(R::ReadPayloadCrc); }
        else if(mode=="read_header") { radio.flags|=RADIOLIB_SX126X_IRQ_HEADER_ERR;observed(R::ReadHeader); }
        else if(mode=="read_both") { radio.flags|=RADIOLIB_SX126X_IRQ_CRC_ERR|RADIOLIB_SX126X_IRQ_HEADER_ERR;observed(R::ReadBoth); }
        else if(mode=="read_stale_both") {
            radio.flags|=RADIOLIB_SX126X_IRQ_CRC_ERR|RADIOLIB_SX126X_IRQ_HEADER_ERR|RADIOLIB_SX126X_IRQ_HEADER_VALID;
            observed(R::ReadPayloadCrc);
        } else if(mode=="read_unattributed") { radio.read_error=-7;observed(R::ReadIntegrityUnattributed); }
        else if(mode=="read_timeout") { radio.read_error=-6;observed(R::ReadTimeout); }
        else if(mode=="read_other") { radio.read_error=-99;observed(R::ReadOtherError); }
        else if(mode=="length_zero" || mode=="length_large") { radio.received.resize(mode=="length_zero" ? 0 : 256);observed(R::InvalidReportedLength); }
        else if(mode=="reserve_low" || mode=="invalid_length_and_low") {
            while(output.freeSlots()>2) assert(output.offer(response(STATUS_REPLY,90)));
            if(mode=="invalid_length_and_low") radio.received.clear();
            observed(mode=="reserve_low" ? R::OutputReserveLow : R::InvalidReportedLength);
        } else if(mode=="offer_failed") { radio.read_hook=fillOutput;observed(R::OutputOfferFailed); }
        else if(mode=="export" || mode=="gfsk_export") observed(R::RxExported);
        else if(mode=="stale_header" || mode=="gfsk_stale_header") {
            radio.flags|=RADIOLIB_SX126X_IRQ_HEADER_ERR|(gfskMode ? RADIOLIB_SX126X_IRQ_SYNC_WORD_VALID : RADIOLIB_SX126X_IRQ_HEADER_VALID);
            // Pinned RadioLib's readData integrity check uses HEADER_VALID,
            // even for FSK. The app's preemption/restart guards use sync-valid.
            observed(gfskMode ? R::ReadHeader : R::RxExported);
        } else assert(false);
        serviceRadio();assert(armed && !fenced && !attempted);
    }
    uint32_t drops=0;for(uint8_t n=0;n<mtbench::profiling::RX_DROP_REASONS;n++) drops+=expected[n];
    assert(droppedRx==drops);
#if MTBENCH_NATIVE_PROFILE
    assert(!nativeProfile.rx.saturated_mask);
    for(uint8_t n=0;n<mtbench::profiling::RX_REASONS;n++) assert(nativeProfile.rx.counts[n]==expected[n]);
    assert(!fake_heap_queries && !fake_stack_queries);
#endif
    // Off/on runs must preserve every serial byte, native charge and driver count.
    printFrames(drain());
    std::cout << droppedRx << ',' << attempted << ',' << reservedUs << ',' << radio.starts
              << ',' << radio.receive_starts << ',' << armed << ',' << fenced << ','
              << transmitting << ',' << lastMutation << ',' << fake_time << '\n';
    return 0;
}
