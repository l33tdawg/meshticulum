#pragma once
// Included once by main.cpp after its checked radio helpers. Single loop owns
// commands, state, SPI and output; no worker/task/ISR may dispatch this queue.
// OPEN is a local opt-in, not grant authentication. The host must establish the
// immutable bounded-handover policy before issuing it (MCU_BURST_PROTOCOL.md).
#include <new>

namespace burst_native = mtbench::experimental;
static constexpr uint64_t BURST_RX_US=5000, BURST_REVERSE_US=100000;
alignas(burst_native::TxBurstQueue) static uint8_t burstStorage[sizeof(burst_native::TxBurstQueue)];
static burst_native::TxBurstQueue* burstQueue=nullptr;
static burst_native::Binding burstBinding;
static uint64_t burstToken=0, burstOpenDeadline=0, burstArmRequest=0, burstNextStartUs=0;
static uint8_t burstOpenBody[48]={}, burstTrainCount=0;
static bool burstBoardPrepared=false;
static uint64_t burstHeaderSeenUs=0;
static uint64_t burstRxDelayUs=0;
static uint8_t burstDriverPacket[255]={}; // Never overwrite legacy replay bytes.
struct BurstOffered { uint64_t request=0, at=0; burst_native::State state=burst_native::State::Empty; };
static BurstOffered burstOffered[4];
struct BurstAckReceipt { uint64_t command=0; uint8_t body[57]={}; };
static BurstAckReceipt burstAckReceipts[4];
static size_t burstAckNext=0;
static Frame burstCancelReceipt;
struct BurstMutationFloor {
    uint64_t request;bool mutation;
    ~BurstMutationFloor() { if(mutation && request>lastMutation) lastMutation=request; }
};

static bool burstTerminal(burst_native::State state) {
    return state==burst_native::State::RfDone || state==burst_native::State::NotSent || state==burst_native::State::Unknown;
}
static uint8_t burstReason(burst_native::Code code) {
    using C=burst_native::Code;
    switch(code) {
    case C::Stale: case C::Missing:return OLD_ATTEMPT;
    case C::Conflict: case C::Mismatch:return ATTEMPT_CONFLICT;
    case C::Full: case C::Busy: case C::NotTerminal:return mtbench::BUSY;
    case C::Budget:return BUDGET;
    case C::Expired:return NOT_ARMED;
    case C::Fenced: case C::ClockRegressed:return FENCED;
    default:return BAD_COMMAND;
    }
}
static Frame burstEvent(const burst_native::Evidence& e) {
    Frame frame=response(BURST_EVENT,e.request);frame.size=81;
    put64(frame.body,burstToken);frame.body[8]=uint8_t(e.state);put64(frame.body+9,e.event_us);
    memcpy(frame.body+17,e.digest,32);put64(frame.body+49,e.estimated_airtime_us);put16(frame.body+57,e.size);
    put64(frame.body+59,e.deadline_us);put64(frame.body+67,e.driver_elapsed_us);
    put32(frame.body+75,e.irq_flags);put16(frame.body+79,uint16_t(e.driver_error));return frame;
}
static void burstFlushEvents() {
    if(!burstQueue) return;
    for(size_t n=0;n<4 && output.freeSlots()>2;n++) {
        burst_native::Evidence e;if(!burstQueue->evidenceAt(n,e)) { burstOffered[n]=BurstOffered{};continue; }
        auto& sent=burstOffered[n];
        if(sent.request==e.request && sent.at==e.event_us && sent.state==e.state) continue;
        if(output.offer(burstEvent(e))) { sent.request=e.request;sent.at=e.event_us;sent.state=e.state; }
    }
}
static void burstCloseEmpty() {
    if(!burstQueue || burstQueue->retained()) return;
    burstQueue->~TxBurstQueue();burstQueue=nullptr;burstToken=burstOpenDeadline=burstArmRequest=0;
    burstBinding=burst_native::Binding{};memset(burstOpenBody,0,sizeof(burstOpenBody));
    for(auto& sent:burstOffered) sent=BurstOffered{};
}
static bool burstMutation(const Frame& frame) {
    if(frame.request<=lastMutation) { refuse(frame,OLD_ATTEMPT);return false; }
    lastMutation=frame.request;return true;
}
static void burstTryContain() {
    if(!burstQueue || !burstQueue->containmentRequired()) return;
    const int16_t error=idleRadio();
    if(burstBoardPrepared) { board.onAfterTransmit();burstBoardPrepared=false; }
    transmitting=false;burstActiveRequest=0;armed=false;fenced=true;
    serialContainmentPending=error!=RADIOLIB_ERR_NONE;
    burstQueue->containmentComplete(error==RADIOLIB_ERR_NONE);
}
static void burstStop() {
    if(!burstQueue) return;
    if(transmitting && !burstActiveRequest) {
        // Legacy ACK/control may run after the DATA drain. A later containment
        // command still has to preserve that legacy physical uncertainty.
        reportOutcome(UNKNOWN,nowUs()-startedUs,0,-1);board.onAfterTransmit();
    }
    burstQueue->poll(nowUs());burstQueue->cancel();
    if(transmitting && !burstQueue->containmentRequired()) burstBoot.containment_required=true;
    // Even queued-only cancellation must physically close continuous receive.
    if(!burstQueue->containmentRequired()) {
        const int16_t error=idleRadio();armed=false;
        if(error!=RADIOLIB_ERR_NONE) { burstBoot.containment_required=true;serialContainmentPending=true; }
    }
    burstTryContain();burstFlushEvents();
}
static void burstReceiveStarted() {
    burstHeaderSeenUs=0;
    const uint64_t delay=burstRxDelayUs ? burstRxDelayUs : (burstQueue ? BURST_RX_US : 0);
    if(delay) {
        const uint64_t next=nowUs()+delay;
        if(next>burstNextStartUs) burstNextStartUs=next;
        burstRxDelayUs=0;
    }
}
static bool burstOutcome(uint8_t outcome,uint64_t elapsed,uint32_t flags,int16_t error) {
    if(!burstQueue || !burstActiveRequest) return false;
    burst_native::Evidence e;
    if(!burstQueue->evidence(burstActiveRequest,e)) { burstStop();return true; }
    e.state=outcome==RF_DONE ? burst_native::State::RfDone : (outcome==STARTED ? burst_native::State::Started : burst_native::State::Unknown);
    e.driver_elapsed_us=elapsed;e.irq_flags=flags;e.driver_error=error;
    const auto code=burstQueue->settle(e,nowUs());
    if(outcome==UNKNOWN || (code!=burst_native::Code::Accepted && code!=burst_native::Code::Replayed)) {
        burstStop();return true;
    }
    if(outcome==RF_DONE) {
        burstActiveRequest=0;burstTrainCount++;
        const bool tail=burstTrainCount>=4 || !burstQueue->queued();
        burstRxDelayUs=tail ? BURST_REVERSE_US : BURST_RX_US;
        if(tail) burstTrainCount=0;
    }
    burstFlushEvents();return true;
}
static bool burstCommand(const Frame& frame) {
    if(frame.type<BURST_CAPABILITIES || frame.type>BURST_STATUS) return false;
    // Even a refused malformed mutation consumes its global ordering mark.
    // DATA's queue check runs before this exit guard so accepted IDs are not
    // mistakenly stale; exact retained replays still precede stale refusal.
    BurstMutationFloor floor{frame.request,frame.type==BURST_OPEN || frame.type==BURST_DATA || frame.type==BURST_ACK || frame.type==BURST_CANCEL};
    if(frame.type==BURST_CAPABILITIES && frame.size==0) {
        Frame reply=response(BURST_CAPABILITIES_REPLY,frame.request);reply.size=24;
        memcpy(reply.body,"MBQ1",4);reply.body[4]=4;reply.body[5]=7;put16(reply.body+6,255);
        put32(reply.body+8,2000000);put32(reply.body+12,100000);put32(reply.body+16,BURST_RX_US);put32(reply.body+20,BURST_REVERSE_US);
        output.offer(reply);return true;
    }
    if(frame.type==BURST_CLOCK && frame.size==0) {
        Frame reply=response(BURST_CLOCK_REPLY,frame.request);reply.size=24;put64(reply.body,nowUs());
        const bool live=armed && nowUs()<deadlineUs;
        put64(reply.body+8,live ? armMutationRequest : 0);put64(reply.body+16,live ? deadlineUs : 0);
        output.offer(reply);return true;
    }
    if(frame.type==BURST_STATUS && frame.size==8) {
        if(u64(frame.body) && u64(frame.body)!=burstToken) { refuse(frame,ATTEMPT_CONFLICT);return true; }
        Frame reply=response(BURST_STATUS_REPLY,frame.request);reply.size=89;
        put64(reply.body,burstToken);memcpy(reply.body+8,burstBinding.context_sha256,32);
        put64(reply.body+40,burstArmRequest);put64(reply.body+48,burstOpenDeadline);
        reply.body[56]=burstQueue ? burstQueue->pending() : 0;reply.body[57]=burstQueue ? burstQueue->retained() : 0;
        reply.body[58]=transmitting;reply.body[59]=fenced;reply.body[60]=burstBoot.containment_required;
        put64(reply.body+61,nowUs());put64(reply.body+69,burstNextStartUs);put32(reply.body+77,attempted);put64(reply.body+81,reservedUs);
        output.offer(reply);return true;
    }
    if(frame.type==BURST_OPEN && frame.size==48) {
        if(burstToken && frame.request==burstToken) {
            if(memcmp(frame.body,burstOpenBody,48)) refuse(frame,ATTEMPT_CONFLICT);
            else { auto reply=response(BURST_OPEN_REPLY,frame.request);reply.size=48;memcpy(reply.body,burstOpenBody,48);output.offer(reply); }
            return true;
        }
        if(!burstMutation(frame)) return true;
        if(transmitting || resultPending || (burstQueue && burstQueue->retained())) { refuse(frame,mtbench::BUSY);return true; }
        if(!ready || fenced || gfskMode || !armed || nowUs()>=deadlineUs) { refuse(frame,NOT_ARMED);return true; }
        bool context=false;for(size_t n=0;n<32;n++) context=context || frame.body[n];
        if(!context || u64(frame.body+32)!=armMutationRequest || u64(frame.body+40)<=nowUs() || u64(frame.body+40)>deadlineUs) {
            refuse(frame,BAD_PROFILE);return true;
        }
        if(burstQueue && !memcmp(frame.body,burstBinding.context_sha256,32) && burstArmRequest==armMutationRequest
            && u64(frame.body+40)>burstOpenDeadline) { refuse(frame,BAD_PROFILE);return true; }
        burstCloseEmpty();memcpy(burstBinding.boot,boot,16);memcpy(burstBinding.context_sha256,frame.body,32);
        burst_native::Admission admission;admission.binding=burstBinding;admission.lease_deadline_us=u64(frame.body+40);
        admission.max_attempts=capCount;admission.max_airtime_us=capUs;
        burstQueue=new(burstStorage) burst_native::TxBurstQueue(admission,burstBoot);
        burstToken=frame.request;burstArmRequest=armMutationRequest;burstOpenDeadline=admission.lease_deadline_us;
        memcpy(burstOpenBody,frame.body,48);
        auto reply=response(BURST_OPEN_REPLY,frame.request);reply.size=48;memcpy(reply.body,frame.body,48);output.offer(reply);return true;
    }
    if(frame.type==BURST_ACK && frame.size==57) {
        // Check retained releases before the global stale gate, including a
        // fresh command repeating an already committed exact release identity.
        for(const auto& receipt:burstAckReceipts) if(receipt.command==frame.request) {
            if(memcmp(receipt.body,frame.body,57)) refuse(frame,ATTEMPT_CONFLICT);
            else {
                auto reply=response(BURST_ACK_REPLY,frame.request);reply.size=57;memcpy(reply.body,frame.body,57);output.offer(reply);
            }
            return true;
        }
        bool released=false;
        for(const auto& receipt:burstAckReceipts) if(receipt.command && u64(receipt.body+8)==u64(frame.body+8)) {
            if(memcmp(receipt.body,frame.body,57)) { refuse(frame,ATTEMPT_CONFLICT);return true; }
            released=true;
        }
        if(!burstMutation(frame)) return true;
        if(released) {
            auto& receipt=burstAckReceipts[burstAckNext++%4];receipt.command=frame.request;memcpy(receipt.body,frame.body,57);
            auto reply=response(BURST_ACK_REPLY,frame.request);reply.size=57;memcpy(reply.body,frame.body,57);output.offer(reply);return true;
        }
        if(!burstQueue || u64(frame.body)!=burstToken) { refuse(frame,ATTEMPT_CONFLICT);return true; }
        burst_native::Evidence e;
        if(!burstQueue->evidence(u64(frame.body+8),e)) { refuse(frame,OLD_ATTEMPT);return true; }
        if(!burstTerminal(e.state) || uint8_t(e.state)!=frame.body[16] || e.event_us!=u64(frame.body+17)
            || memcmp(e.digest,frame.body+25,32)) { refuse(frame,ATTEMPT_CONFLICT);return true; }
        const auto code=burstQueue->acknowledge(burstBinding,e.request,e.digest,e.state);
        if(code!=burst_native::Code::Accepted) { refuse(frame,burstReason(code));return true; }
        auto& receipt=burstAckReceipts[burstAckNext++%4];receipt.command=frame.request;memcpy(receipt.body,frame.body,57);
        auto reply=response(BURST_ACK_REPLY,frame.request);reply.size=57;memcpy(reply.body,frame.body,57);output.offer(reply);return true;
    }
    if(frame.type==BURST_CANCEL && frame.size==8) {
        if(burstCancelReceipt.request==frame.request) {
            if(u64(frame.body)!=u64(burstCancelReceipt.body)) refuse(frame,ATTEMPT_CONFLICT);else output.offer(burstCancelReceipt);
            return true;
        }
        if(!burstMutation(frame)) return true;
        if(!burstQueue || u64(frame.body)!=burstToken) { refuse(frame,ATTEMPT_CONFLICT);return true; }
        burstStop();burstCancelReceipt=response(BURST_CANCEL_REPLY,frame.request);burstCancelReceipt.size=10;
        put64(burstCancelReceipt.body,burstToken);burstCancelReceipt.body[8]=fenced;
        burstCancelReceipt.body[9]=burstQueue->containmentRequired();output.offer(burstCancelReceipt);return true;
    }
    if(!burstQueue || frame.size<8 || u64(frame.body)!=burstToken) { refuse(frame,ATTEMPT_CONFLICT);return true; }
    if(frame.type==BURST_QUERY && frame.size==8) {
        burst_native::Evidence e;
        if(burstQueue->evidence(frame.request,e)) output.offer(burstEvent(e));else refuse(frame,OLD_ATTEMPT);
        return true;
    }
    if(frame.type==BURST_DATA && frame.size>=60 && frame.size<=314) {
        const uint16_t size=u16(frame.body+25);
        if(size<1 || size>255 || frame.size!=59+size || frame.body[24]!=1) { refuse(frame,BAD_COMMAND);return true; }
        burst_native::Packet packet;packet.request=frame.request;packet.deadline_us=u64(frame.body+8);
        packet.estimated_airtime_us=u64(frame.body+16);packet.size=size;memcpy(packet.digest,frame.body+27,32);memcpy(packet.ciphertext,frame.body+59,size);
        burst_native::Evidence retained;
        const bool replay=burstQueue->evidence(frame.request,retained);
        const uint64_t estimate=radio.getTimeOnAir(size);
        uint8_t digest[32]={};
        if(mbedtls_sha256_ret(packet.ciphertext,size,digest,0)!=0 || memcmp(digest,packet.digest,32) || estimate!=packet.estimated_airtime_us) {
            if(frame.request>lastMutation) lastMutation=frame.request;
            refuse(frame,replay ? ATTEMPT_CONFLICT : BAD_COMMAND);return true;
        }
        if(!replay) {
            if(!armed || gfskMode || nowUs()>=deadlineUs) { refuse(frame,NOT_ARMED);return true; }
            uint64_t needed=estimate+100000+BURST_RX_US;
            for(size_t n=0;n<4;n++) {
                burst_native::Evidence e;if(burstQueue->evidenceAt(n,e) && !burstTerminal(e.state)) needed+=e.estimated_airtime_us+100000+BURST_RX_US;
            }
            const uint64_t now=nowUs();if(burstNextStartUs>now) needed+=burstNextStartUs-now;
            if(packet.deadline_us<=now || needed>packet.deadline_us-now) { refuse(frame,BUDGET);return true; }
        }
        const auto code=burstQueue->enqueue(burstBinding,packet,estimate,nowUs());
        if(code==burst_native::Code::Accepted || code==burst_native::Code::Replayed) {
            burstQueue->evidence(frame.request,retained);output.offer(burstEvent(retained));
            for(size_t n=0;n<4;n++) {
                burst_native::Evidence e;if(burstQueue->evidenceAt(n,e) && e.request==frame.request) {
                    burstOffered[n].request=e.request;burstOffered[n].at=e.event_us;burstOffered[n].state=e.state;
                }
            }
        } else { if(frame.request>lastMutation) lastMutation=frame.request;refuse(frame,burstReason(code)); }
        return true;
    }
    refuse(frame,BAD_COMMAND);return true;
}
static void burstService(bool dispatch) {
    if(!burstQueue) return;
    burstQueue->poll(nowUs());burstTryContain();burstFlushEvents();
    if(!dispatch || !ready || !armed || fenced || transmitting || resultPending || nowUs()<burstNextStartUs
        || output.freeSlots()<5 || !burstQueue->queued()) return;
    uint32_t flags=0;
    if(readIrqFlags(flags)!=RADIOLIB_ERR_NONE) { burstStop();return; }
    if(flags&RADIOLIB_SX126X_IRQ_RX_DONE) {
        burstHeaderSeenUs=0;
        if(!drainCompletedReceive(true)) burstStop();
        return;
    }
    if(flags&RADIOLIB_SX126X_IRQ_HEADER_VALID) {
        if(!burstHeaderSeenUs) burstHeaderSeenUs=nowUs();
        // Continuous RX can leave HEADER_VALID latched without RX_DONE or a
        // hardware timeout. Wait a maximum-size admitted frame plus guard,
        // then restart checked RX; an actual RX_DONE always takes priority.
        if(nowUs()-burstHeaderSeenUs>radio.getTimeOnAir(255)+100000) {
            droppedRx++;burstHeaderSeenUs=0;startReceive();
        }
        return;
    }
    burstHeaderSeenUs=0;
    // Claim only after receive/evidence priority. A second checked standby/IRQ
    // drain closes the decode-to-FIFO race; every failure after claim is UNKNOWN.
    const auto claim=burstQueue->issue(nowUs(),false,true);
    if(!claim.packet) { burstFlushEvents();return; }
    burstActiveRequest=claim.packet->request;startedUs=nowUs();previousEstimate=claim.packet->estimated_airtime_us;
    burstFlushEvents();
    int16_t error=radio.standby();
    if(error==RADIOLIB_ERR_NONE) error=readIrqFlags(flags);
    if(error==RADIOLIB_ERR_NONE && (flags&RADIOLIB_SX126X_IRQ_RX_DONE)) {
        if(!drainCompletedReceive(false,true)) error=-1;
    } else if(error==RADIOLIB_ERR_NONE && (flags&RADIOLIB_SX126X_IRQ_HEADER_VALID)) {
        // A valid in-progress header appeared during standby. Do not overwrite
        // it or label this issued operation NOT_SENT; fail closed and contain.
        burstStop();return;
    }
    if(error==RADIOLIB_ERR_NONE) error=radio.clearIrqFlags(RADIOLIB_SX126X_IRQ_ALL);
    if(error!=RADIOLIB_ERR_NONE || nowUs()>=claim.packet->deadline_us
        || claim.packet->estimated_airtime_us+100000>claim.packet->deadline_us-nowUs()) {
        burstOutcome(UNKNOWN,nowUs()-startedUs,flags,error ? error : -1);return;
    }
    memcpy(burstDriverPacket,claim.packet->ciphertext,claim.packet->size);
    board.onBeforeTransmit();burstBoardPrepared=true;error=radio.startTransmit(burstDriverPacket,claim.packet->size);
    if(error!=RADIOLIB_ERR_NONE) { burstOutcome(UNKNOWN,nowUs()-startedUs,flags,error);return; }
    transmitting=true;burstOutcome(STARTED,0,0,0);
}
