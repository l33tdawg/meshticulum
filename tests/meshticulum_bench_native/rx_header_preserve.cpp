// Source-only IRQ interleavings; no physical radio qualification.

#define main existing_harness_main
#include "harness.cpp"
#undef main
int main(int argc,char** argv) {
    assert(argc==2);const std::string mode=argv[1];setup();
    command(mode=="gfsk" ? gfskArmRequest() : armRequest());drain();
    assert(armed && radio.receiving && !fenced);
    const auto receives=radio.receive_starts,standbys=radio.standbys;
    const auto paid=reservedUs,mutation=lastMutation,limit=deadlineUs;
    const auto attempts=attempted;
    radio.flags=RADIOLIB_SX126X_IRQ_HEADER_ERR;
    if(mode=="valid_initial") radio.flags|=RADIOLIB_SX126X_IRQ_HEADER_VALID;
    else if(mode=="done_initial") radio.flags|=RADIOLIB_SX126X_IRQ_RX_DONE|RADIOLIB_SX126X_IRQ_HEADER_VALID;
    else if(mode=="crc") radio.flags|=RADIOLIB_SX126X_IRQ_CRC_ERR;
    else if(mode=="timeout") radio.flags|=RADIOLIB_SX126X_IRQ_TIMEOUT;
    else if(mode=="unknown") radio.flags|=0x8000;
    else if(mode=="preamble") radio.flags|=0x04;
    else if(mode=="late_valid") radio.irq_after_read=RADIOLIB_SX126X_IRQ_HEADER_VALID;
    else if(mode=="late_done") radio.irq_after_read=RADIOLIB_SX126X_IRQ_RX_DONE|RADIOLIB_SX126X_IRQ_HEADER_VALID;
    else if(mode=="late_crc") radio.irq_after_read=RADIOLIB_SX126X_IRQ_CRC_ERR;
    else if(mode=="late_unknown") radio.irq_after_read=0x8000;
    else if(mode=="clear_valid") radio.irq_on_clear=RADIOLIB_SX126X_IRQ_HEADER_VALID;
    else if(mode=="clear_done") radio.irq_on_clear=RADIOLIB_SX126X_IRQ_RX_DONE|RADIOLIB_SX126X_IRQ_HEADER_VALID;
    else if(mode=="clear_crc") radio.irq_on_clear=RADIOLIB_SX126X_IRQ_CRC_ERR;
    else if(mode=="late_done_invalid") radio.irq_after_read=RADIOLIB_SX126X_IRQ_RX_DONE|RADIOLIB_SX126X_IRQ_HEADER_ERR;
    else if(mode=="clear_done_invalid") radio.irq_on_clear=RADIOLIB_SX126X_IRQ_RX_DONE|RADIOLIB_SX126X_IRQ_HEADER_ERR;
    else if(mode=="clear_failure") radio.clear_error=-99;
    else if(mode=="expired") radio.irq_advance_us=deadlineUs-fake_time;
    else if(mode=="irq_failure") radio.irq_error=-98;
    else assert(mode=="header" || mode=="gfsk" || mode=="shadow_restart" || mode=="shadow_restart_failure");
    serviceRadio();
    assert(attempted==attempts && reservedUs==paid && lastMutation==mutation
           && deadlineUs==limit && !radio.starts && !transmitting);
    if(mode=="shadow_restart" || mode=="shadow_restart_failure") {
#if CANDIDATE
        assert(headerErrorShadow);
#endif
        if(mode=="shadow_restart_failure") radio.receive_error=-99;
        startReceive();
        if(mode=="shadow_restart_failure") {
            assert(!armed && fenced && !radio.receiving);
#if CANDIDATE
            assert(headerErrorShadow);
#endif
        } else {
#if CANDIDATE
            assert(!headerErrorShadow);
#endif
            // A successful full clear/RX launch ends the shadow's lifetime.
            // The original driver permits this flag pattern absent old error.
            radio.flags=RADIOLIB_SX126X_IRQ_RX_DONE;serviceRadio();
            const auto frames=drain();assert(frames.size()==1 && frames[0].type==RECEIVE);
            assert(droppedRx==1 && armed && !fenced);
        }
    } else if(mode=="valid_initial") {
        assert(radio.receive_starts==receives && radio.standbys==standbys
               && radio.flags==(RADIOLIB_SX126X_IRQ_HEADER_ERR|RADIOLIB_SX126X_IRQ_HEADER_VALID)
               && !droppedRx && armed && !fenced);
    } else if(mode=="done_initial") {
        const auto frames=drain();assert(frames.size()==1 && frames[0].type==RECEIVE);
        assert(!droppedRx && armed && !fenced && radio.receive_starts==receives+1);
    } else if(mode=="irq_failure" || mode=="clear_failure") {
        assert(!armed && fenced && !radio.receiving);
    } else if(mode=="expired") {
        assert(!armed && !radio.receiving && radio.receive_starts==receives);
    } else if(mode=="crc" || mode=="timeout" || mode=="unknown" || mode=="gfsk") {
        assert(radio.receive_starts==receives+1 && !radio.flags && droppedRx==1 && armed && !fenced);
    } else {
        assert(droppedRx==1 && armed && !fenced && radio.receiving);
#if CANDIDATE
        assert(radio.receive_starts==receives && radio.standbys==standbys);
        assert(radio.last_clear_mask==RADIOLIB_SX126X_IRQ_HEADER_ERR);
        uint32_t expected=0;
        if(mode=="late_valid" || mode=="clear_valid") expected=RADIOLIB_SX126X_IRQ_HEADER_VALID;
        if(mode=="late_done" || mode=="clear_done") expected=RADIOLIB_SX126X_IRQ_RX_DONE|RADIOLIB_SX126X_IRQ_HEADER_VALID;
        if(mode=="late_done_invalid" || mode=="clear_done_invalid") expected=RADIOLIB_SX126X_IRQ_RX_DONE;
        if(mode=="late_crc" || mode=="clear_crc") expected=RADIOLIB_SX126X_IRQ_CRC_ERR;
        if(mode=="late_unknown") expected=0x8000;
        if(mode=="preamble") expected=0x04;
        assert(radio.flags==expected);
        if(expected & RADIOLIB_SX126X_IRQ_RX_DONE) {
            serviceRadio();const auto frames=drain();
            if(expected & RADIOLIB_SX126X_IRQ_HEADER_VALID)
                assert(frames.size()==1 && frames[0].type==RECEIVE && droppedRx==1);
            else {
                assert(frames.empty() && droppedRx==2);
#if MTBENCH_NATIVE_PROFILE
                assert(nativeProfile.rx.counts[uint8_t(mtbench::profiling::RxReason::ReadHeader)]==1);
#endif
            }
            assert(!headerErrorShadow); // checked full rearm completed
        }
        if(expected & RADIOLIB_SX126X_IRQ_CRC_ERR) {
            // Selective clear never erases concurrently arriving payload CRC.
            radio.flags|=RADIOLIB_SX126X_IRQ_RX_DONE|RADIOLIB_SX126X_IRQ_HEADER_VALID;
            serviceRadio();assert(drain().empty() && droppedRx==2);
        }
#else
        assert(radio.receive_starts==receives+1 && radio.standbys==standbys+1);
        assert(!radio.flags); // The old full restart clears the new arrival too.
#endif
    }
#if MTBENCH_NATIVE_PROFILE
    if(mode!="valid_initial" && mode!="done_initial" && mode!="irq_failure")
        assert(nativeProfile.rx.counts[uint8_t(mtbench::profiling::RxReason::IrqRestartHeaderOnly)]
               ==unsigned(mode!="crc"));
#endif
    std::cout << attempted << ',' << reservedUs << ',' << radio.receive_starts-receives
              << ',' << radio.standbys-standbys << ',' << droppedRx << ',' << armed << ',' << fenced << '\n';
}
