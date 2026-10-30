// Experimental raw LoRa coprocessor. It sends nothing until explicitly armed.
// No MeshCore/RNS framing, routing, auto ACK, announcements or key-store access.
#include <Arduino.h>
#include <Preferences.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <mbedtls/sha256.h>
#include <target.h>
#include "protocol.hpp"
#include "native_profile.hpp"
#if MTBENCH_NATIVE_PROFILE
#include <esp_heap_caps.h>
// Pinned ESP32-S3 FreeRTOS reports byte high-water, unlike vanilla word units.
static_assert(sizeof(StackType_t)==1,"native memory query requires byte stack units");
#endif
#include "gfsk_profile.hpp"
#include "gfsk250_profile.hpp"
#include "experimental/tx_burst.hpp"

extern RADIO_CLASS radio;
using namespace mtbench;

static Decoder decoder;
static OutputQueue output;
#if MTBENCH_NATIVE_PROFILE
static mtbench::profiling::Profile nativeProfile;
#endif
static constexpr size_t USB_RX_BYTES=2048, USB_RX_DRAIN_BYTES=128;
static_assert(USB_RX_BYTES>=2*MAX_ENCODED,"USB RX must hold two maximum encoded frames");
static bool serialReady=false;
static bool serialContainmentPending=false;
static TaskHandle_t radioServiceTask=nullptr;
static_assert(configUSE_TASK_NOTIFICATIONS==1,"radio wake requires task notifications");
static_assert(configTICK_RATE_HZ==1000,"retain the pinned one-millisecond periodic service");
static void IRAM_ATTR radioWake() {
    // An edge is only a wake hint. SPI, IRQ interpretation and every radio
    // action stay in the original loop task and its checked periodic path.
    if(radioServiceTask==nullptr) return;
    BaseType_t woke=pdFALSE;
    vTaskNotifyGiveFromISR(radioServiceTask,&woke);
    if(woke==pdTRUE) portYIELD_FROM_ISR();
}
static void waitForRadioOrTick() {
    if(radioServiceTask!=nullptr) ulTaskNotifyTake(pdTRUE,1);
    else delay(1);
}
static mtbench::experimental::BootState burstBoot;
static uint8_t boot[16]={}, profile[47]={}, previousPacket[255]={}, previousDigest[32]={};
static uint64_t previousRequest=0, deadlineUs=0, startedUs=0, capUs=0;
static uint64_t& lastMutation=burstBoot.request_highwater;
static uint64_t& reservedUs=burstBoot.counters.reserved_airtime_us;
static uint32_t& attempted=burstBoot.counters.attempts;
static uint32_t capCount=0, droppedRx=0;
static uint16_t previousSize=0;
static uint64_t previousEstimate=0;
static bool gfskMode=false;
static bool gfsk250Mode=false;
static bool ready=false, armed=false, transmitting=false, resultPending=false;
static bool& fenced=burstBoot.fenced;
static Frame previousResult;
static uint64_t armMutationRequest=0, burstActiveRequest=0;
static bool burstOutcome(uint8_t outcome,uint64_t elapsed,uint32_t flags,int16_t error);
static void burstStop();
static void burstReceiveStarted();

static Frame response(uint8_t type,uint64_t request) {
    Frame frame; frame.type=type; frame.request=request; memcpy(frame.boot,boot,16); return frame;
}
static uint64_t nowUs() { return uint64_t(esp_timer_get_time()); }
static int16_t idleRadio() {
    // A disarmed/expired/fenced carrier does not retain the fast oscillator.
    // RadioLib updates both standby() and the RX/TX fallback mode within this
    // checked call; an error remains a containment failure.
    const int16_t error=radio.setStandbyXOSC(false);
    if(error!=RADIOLIB_ERR_NONE && burstBoot.queue_owned) {
        burstBoot.containment_required=true;serialContainmentPending=true;fenced=true;
    }
    return error;
}
static int16_t readIrqFlags(uint32_t& flags) {
    // RadioLib getIrqFlags() discards the SPI return code. A failed read must
    // never turn partially returned bytes into physical completion evidence.
    uint8_t bytes[2]={};
    const int16_t error=radio.getMod()->SPIreadStream(RADIOLIB_SX126X_CMD_GET_IRQ_STATUS,bytes,2);
    flags=error==RADIOLIB_ERR_NONE ? u16(bytes) : 0;
    return error;
}
static void refuse(const Frame& request,uint8_t reason) {
    Frame frame=response(REFUSED,request.request); frame.size=1; frame.body[0]=reason; output.offer(frame);
}
static bool isCurrentBoot(const Frame& frame) { return memcmp(frame.boot,boot,16)==0; }
static void reportOutcome(uint8_t outcome,uint64_t elapsed,uint32_t flags,int16_t error) {
    previousResult=response(TX_RESULT,previousRequest); previousResult.size=55;
    previousResult.body[0]=outcome; memcpy(previousResult.body+1,previousDigest,32);
    put64(previousResult.body+33,previousEstimate); put64(previousResult.body+41,elapsed);
    put32(previousResult.body+49,flags); put16(previousResult.body+53,uint16_t(error));
    resultPending=!output.offer(previousResult);
}
static void contain(uint32_t flags,int16_t error) {
    if(burstOutcome(UNKNOWN,nowUs()-startedUs,flags,error)) return;
    // Standby is an attempted containment action, not proof of RF completion.
    idleRadio(); board.onAfterTransmit(); transmitting=false; armed=false; fenced=true;
    reportOutcome(UNKNOWN,nowUs()-startedUs,flags,error);
}
static void startReceive() {
    if(!armed || fenced) return;
    if(nowUs()>=deadlineUs) { armed=false; if(idleRadio()!=RADIOLIB_ERR_NONE) fenced=true; return; }
    MT_PROFILE_SCOPE(rxStart,RxStartCall);
    const int16_t receiveError=radio.startReceive();
    MT_PROFILE_END(rxStart,receiveError==RADIOLIB_ERR_NONE);
    if(receiveError!=RADIOLIB_ERR_NONE) { armed=false; fenced=true; idleRadio(); }
    else burstReceiveStarted();
}
static bool drainCompletedReceive(bool rearm,bool alreadyStandby=false) {
    // RX continuous shares the FIFO with TX. Stop writes before reading the
    // completed packet; SetStandby preserves the latched IRQ and FIFO contents.
    uint32_t flags=0;
    if((!alreadyStandby && radio.standby()!=RADIOLIB_ERR_NONE)
        || readIrqFlags(flags)!=RADIOLIB_ERR_NONE || !(flags & RADIOLIB_SX126X_IRQ_RX_DONE)) {
        armed=false; fenced=true; idleRadio(); return false;
    }
    const size_t length=radio.getPacketLength();
    Frame received=response(gfsk250Mode ? GFSK250_RECEIVE : (gfskMode ? GFSK_RECEIVE : RECEIVE),0);
    if(length>=1 && length<=255 && output.freeSlots()>2) {
        const int16_t error=radio.readData(received.body+(gfskMode ? 2 : 4),length);
        if(error==RADIOLIB_ERR_NONE) {
            put16(received.body,uint16_t(int16_t((gfskMode ? radio.getRSSI(true) : radio.getRSSI())*10)));
            if(!gfskMode) put16(received.body+2,uint16_t(int16_t(radio.getSNR()*10)));
            received.size=length+(gfskMode ? 2 : 4);
            if(!output.offer(received)) droppedRx++;
        } else droppedRx++;
    } else droppedRx++;
    if(rearm) startReceive();
    return armed && !fenced;
}
static bool configure(const uint8_t* body) {
    const uint32_t hz=u32(body), bw=u32(body+4);
    const uint8_t sf=body[8], cr=body[9], sync=body[13];
    const int8_t power=int8_t(body[10]);
    const uint16_t preamble=u16(body+11);
    const uint32_t duration=u32(body+14), count=u32(body+18);
    const uint64_t budget=u64(body+22);
    // These are bench bounds, not a regional compliance determination.
    if((bw!=62500 && bw!=125000 && bw!=250000 && bw!=500000)
        || hz<902000000u+bw/2 || hz>928000000u-bw/2
        || sf<5 || sf>12 || cr<5 || cr>8 || power<0 || power>MESHTICULUM_MAX_CHIP_DBM
        || preamble<(sf<=6 ? 12 : 8) || preamble>64 || sync==0 || duration<1000 || duration>300000
        || count<=attempted || count>100000 || budget<=reservedUs || budget>300000000ull) return false;
    if(gfskMode && !radio_init()) return false;
    if(radio.standby()!=RADIOLIB_ERR_NONE
        || radio.setFrequency(hz/1000000.0f)!=RADIOLIB_ERR_NONE
        || radio.setBandwidth(bw/1000.0f)!=RADIOLIB_ERR_NONE
        || radio.setSpreadingFactor(sf)!=RADIOLIB_ERR_NONE
        || radio.setCodingRate(cr)!=RADIOLIB_ERR_NONE
        || radio.setOutputPower(power)!=RADIOLIB_ERR_NONE
        || radio.setPreambleLength(preamble)!=RADIOLIB_ERR_NONE
        || radio.setSyncWord(sync)!=RADIOLIB_ERR_NONE
        || radio.setCRC(1)!=RADIOLIB_ERR_NONE
        // SF5/6 require short packet/ACK turnarounds. Keep the TCXO/XOSC
        // running within the original ARM lease instead of restarting it
        // on every standby->TX/RX transition. SF7+ retain their old mode.
        || radio.setStandbyXOSC(sf<=6)!=RADIOLIB_ERR_NONE) return false;
    deadlineUs=nowUs()+uint64_t(duration)*1000; capCount=count; capUs=budget;
    memcpy(profile,body,30); gfskMode=false; gfsk250Mode=false; return true;
}

static bool configureGfsk(const uint8_t* body,size_t size,bool fast=false) {
    mtbench::gfsk::Profile p;
    if(!(fast ? mtbench::gfsk250::decode(body,size,p,MESHTICULUM_BOARD_ID)
              : mtbench::gfsk::decode(body,size,p,MESHTICULUM_BOARD_ID))
        || p.maxAttempts<=attempted || p.maxAirtimeUs<=reservedUs) return false;
    if(radio.standby()!=RADIOLIB_ERR_NONE
        || (fast ? mtbench::gfsk250::program(radio,p,SX126X_DIO3_TCXO_VOLTAGE,false)
                 : mtbench::gfsk::program(radio,p,SX126X_DIO3_TCXO_VOLTAGE,false))!=RADIOLIB_ERR_NONE) return false;
    deadlineUs=nowUs()+uint64_t(p.leaseMs)*1000;capCount=p.maxAttempts;capUs=p.maxAirtimeUs;
    memcpy(profile,body,47);gfskMode=true;gfsk250Mode=fast;return true;
}

#include "burst_adapter.hpp"

static void command(const Frame& frame) {
    if(!frame.request || output.freeSlots()<2) return; // Host must treat missing reply as unknown.
    if(frame.type==HELLO && frame.size==0) {
        Frame reply=response(HELLO_REPLY,frame.request); reply.size=20;
        reply.body[0]=MESHTICULUM_BOARD_ID; reply.body[1]=ready; put16(reply.body+2,255);
        const char version[]=MESHTICULUM_FIRMWARE;
        static_assert(sizeof(version)<=16,"firmware identity too long");
        memcpy(reply.body+4,version,sizeof(version)); output.offer(reply); return;
    }
    if(!isCurrentBoot(frame)) { refuse(frame,WRONG_BOOT); return; }
#if MTBENCH_NATIVE_PROFILE
    if(frame.type==NATIVE_PROFILE) {
        const bool memory=frame.size==1 && frame.body[0]==0xfe;
        if(frame.size>1 || (frame.size==1 && !memory && frame.body[0]>=mtbench::profiling::PHASES)) {
            refuse(frame,BAD_COMMAND);return;
        }
        // Read-only boot snapshot: never poll, disarm, release, reset or pay.
        // Even terminal retained entries must be reconciled before retrieval.
        if(armed || transmitting || resultPending || serialContainmentPending
            || burstBoot.containment_required
            || (burstQueue && (burstQueue->pending() || burstQueue->retained()))) {
            refuse(frame,mtbench::BUSY);return;
        }
        auto reply=response(NATIVE_PROFILE_REPLY,frame.request);
        if(memory) {
            if(radioServiceTask==nullptr) { refuse(frame,mtbench::BUSY);return; }
            // Query-only heap walk/stack scan, after all RF work is closed.
            // The region minima need not have occurred simultaneously.
            multi_heap_info_t info={};
            heap_caps_get_info(&info,MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT);
            const uint64_t values[]={info.total_free_bytes,info.minimum_free_bytes,
                info.largest_free_block,uxTaskGetStackHighWaterMark(radioServiceTask)};
            for(const auto value:values) if(value>UINT32_MAX) { refuse(frame,BAD_COMMAND);return; }
            reply.size=24;memcpy(reply.body,"MNS1",4);
            reply.body[4]=1;reply.body[5]=1; // schema version, byte units; 6/7 reserved zero
            for(size_t n=0;n<4;n++) put32(reply.body+8+4*n,uint32_t(values[n]));
        } else reply.size=nativeProfile.encode(reply.body,frame.size ? frame.body[0] : -1);
        output.offer(reply);return;
    }
#endif
    if(burstCommand(frame)) return;
    if(frame.type==GFSK250_CAPABILITIES && frame.size==0) {
        // Read-only feature negotiation: no ARM, RF, replay counter or lease.
        Frame reply=response(GFSK250_CAPABILITIES_REPLY,frame.request);reply.size=16;
        mtbench::gfsk250::capability(reply.body);output.offer(reply);return;
    }
    if(frame.type==mtbench::STATUS && frame.size==0 && !gfskMode) {
        Frame reply=response(STATUS_REPLY,frame.request); reply.size=49;
        memcpy(reply.body,profile,30); put32(reply.body+30,attempted); put64(reply.body+34,reservedUs);
        put32(reply.body+42,droppedRx); reply.body[46]=armed; reply.body[47]=transmitting;
        reply.body[48]=fenced; output.offer(reply); return;
    }
    if(((frame.type==GFSK_STATUS && !gfsk250Mode)
        || (frame.type==GFSK250_STATUS && gfsk250Mode)) && frame.size==0 && gfskMode) {
        Frame reply=response(gfsk250Mode ? GFSK250_STATUS_REPLY : GFSK_STATUS_REPLY,frame.request);reply.size=66;
        memcpy(reply.body,profile,47);put32(reply.body+47,attempted);put64(reply.body+51,reservedUs);
        put32(reply.body+59,droppedRx);reply.body[63]=armed;reply.body[64]=transmitting;reply.body[65]=fenced;
        output.offer(reply);return;
    }
    if(frame.type==TRANSMIT && previousRequest && frame.request==previousRequest) {
        if(frame.size==previousSize && memcmp(frame.body,previousPacket,previousSize)==0)
            output.offer(previousResult); // Exact replay returns evidence; it never retransmits.
        else refuse(frame,ATTEMPT_CONFLICT);
        return;
    }
    if(frame.type==ARM || frame.type==GFSK_ARM || frame.type==GFSK250_ARM || frame.type==DISARM || frame.type==TRANSMIT) {
        if(frame.request<=lastMutation) { refuse(frame,OLD_ATTEMPT); return; }
        lastMutation=frame.request; // A delayed ARM must not silently extend its lease.
    }
    if(frame.type==DISARM && frame.size==0 && burstQueue) {
        if(burstQueue->retained() || transmitting || burstQueue->containmentRequired()) burstStop();
    }
    if(burstQueue && burstQueue->pending()) { refuse(frame,mtbench::BUSY); return; }
    if(burstQueue && frame.type==TRANSMIT && nowUs()<burstNextStartUs) { refuse(frame,mtbench::BUSY);return; }
    if((frame.type==ARM || frame.type==GFSK_ARM || frame.type==GFSK250_ARM) && burstQueue) {
        if(burstQueue->retained()) { refuse(frame,mtbench::BUSY);return; }
        burstCloseEmpty();
    }
    if(transmitting || resultPending) { refuse(frame,mtbench::BUSY); return; }
    if(frame.type==DISARM && frame.size==0) {
        armed=false;
        if(idleRadio()!=RADIOLIB_ERR_NONE) fenced=true;
        else burstCloseEmpty();
        Frame reply=response(DISARM_REPLY,frame.request); reply.size=1; reply.body[0]=fenced;
        output.offer(reply); return;
    }
    if(fenced || !ready) { refuse(frame,FENCED); return; }
    if(frame.type==ARM && frame.size==30) {
        if(armed) { refuse(frame,mtbench::BUSY); return; }
        if(!configure(frame.body)) { armed=false; if(idleRadio()!=RADIOLIB_ERR_NONE) fenced=true; refuse(frame,BAD_PROFILE); return; }
        armed=true; startReceive();
        if(!armed) { refuse(frame,FENCED); return; }
        armMutationRequest=frame.request;
        Frame reply=response(ARM_REPLY,frame.request); reply.size=30; memcpy(reply.body,profile,30);
        output.offer(reply); return;
    }
    if((frame.type==GFSK_ARM || frame.type==GFSK250_ARM) && frame.size==47) {
        if(armed) { refuse(frame,mtbench::BUSY);return; }
        if(!configureGfsk(frame.body,frame.size,frame.type==GFSK250_ARM)) { armed=false;fenced=true;idleRadio();refuse(frame,BAD_PROFILE);return; }
        armed=true;startReceive();if(!armed) { refuse(frame,FENCED);return; }
        armMutationRequest=frame.request;
        Frame reply=response(gfsk250Mode ? GFSK250_ARM_REPLY : GFSK_ARM_REPLY,frame.request);reply.size=47;memcpy(reply.body,profile,47);output.offer(reply);return;
    }
    if(frame.type!=TRANSMIT || frame.size<1 || frame.size>255) { refuse(frame,BAD_COMMAND); return; }
    if(frame.request<=previousRequest) { refuse(frame,OLD_ATTEMPT); return; }
    if(!armed || nowUs()>=deadlineUs) { armed=false; refuse(frame,NOT_ARMED); return; }
    const uint64_t estimate=gfsk250Mode ? mtbench::gfsk250::airtimeUs(frame.size)
        : (gfskMode ? mtbench::gfsk::airtimeUs(frame.size) : radio.getTimeOnAir(frame.size));
    if(!estimate || attempted>=capCount || estimate>capUs-reservedUs
        || nowUs()+estimate+100000>=deadlineUs) { refuse(frame,BUDGET); return; }
    // The RX may have completed while this USB frame was being decoded.
    // Preserve it before TX's IRQ clear and shared-FIFO write.
    uint32_t receiveFlags=0;
    if(readIrqFlags(receiveFlags)!=RADIOLIB_ERR_NONE) {
        armed=false; fenced=true; idleRadio(); refuse(frame,FENCED); return;
    }
    if((receiveFlags & RADIOLIB_SX126X_IRQ_RX_DONE) && !drainCompletedReceive(false)) {
        refuse(frame,FENCED); return;
    }
    if(nowUs()+estimate+100000>=deadlineUs) { armed=false; idleRadio(); refuse(frame,NOT_ARMED); return; }
    // Reserve once before touching TX. No refunds; host maintains debt across reboot.
    if(mbedtls_sha256_ret(frame.body,frame.size,previousDigest,0)!=0) {
        armed=false; fenced=true; radio.standby(); refuse(frame,FENCED); return;
    }
    previousRequest=frame.request; previousSize=frame.size; memcpy(previousPacket,frame.body,frame.size);
    previousEstimate=estimate; reservedUs+=estimate; attempted++; startedUs=nowUs();
    burstBoot.paid_floor=burstBoot.counters;
    previousResult=response(TX_RESULT,previousRequest); previousResult.size=55;
    previousResult.body[0]=UNKNOWN; memcpy(previousResult.body+1,previousDigest,32);
    put64(previousResult.body+33,estimate);
    int16_t error=radio.standby();
    if(error==RADIOLIB_ERR_NONE) {
        // Catch a packet that completed while the USB frame was being decoded
        // or while entering standby, before TX clears IRQs/writes the FIFO.
        uint32_t flags=0;
        error=readIrqFlags(flags);
        if(error==RADIOLIB_ERR_NONE && (flags & RADIOLIB_SX126X_IRQ_RX_DONE)
            && !drainCompletedReceive(false,true)) { contain(flags,-1); return; }
        if(error==RADIOLIB_ERR_NONE && (flags & (gfskMode ? RADIOLIB_SX126X_IRQ_SYNC_WORD_VALID : RADIOLIB_SX126X_IRQ_HEADER_VALID))
            && !(flags & RADIOLIB_SX126X_IRQ_RX_DONE)) droppedRx++;
    }
    if(error==RADIOLIB_ERR_NONE) error=radio.clearIrqFlags(RADIOLIB_SX126X_IRQ_ALL);
    if(error!=RADIOLIB_ERR_NONE) { contain(0,error); return; }
    if(!armed || nowUs()+estimate+100000>=deadlineUs) { contain(0,-1); return; }
    board.onBeforeTransmit();
    error=radio.startTransmit(previousPacket,previousSize);
    if(error!=RADIOLIB_ERR_NONE) { contain(0,error); return; }
    transmitting=true; reportOutcome(STARTED,0,0,0);
}

void setup() {
    // Preset HWCDC's bounded queue before begin enables its USB interrupt.
    // Allocation failure leaves serial decoding and radio initialization off.
    if(Serial.setRxBufferSize(USB_RX_BYTES)!=USB_RX_BYTES) return;
    Serial.begin(115200); Serial.setTxTimeoutMs(0);
    serialReady=true;
    board.begin();
    put64(boot,ESP.getEfuseMac());
    // A separate NVS namespace preserves existing MeshCore identity/settings.
    Preferences state;
    if(!state.begin("mtbench",false)) return;
    uint64_t generation=state.getULong64("boot",0);
    if(generation==UINT64_MAX || state.putULong64("boot",generation+1)!=8) { state.end(); return; }
    state.end(); put64(boot+8,generation+1);
    memcpy(burstBoot.boot,boot,16);
    ready=radio_init();
    if(ready && idleRadio()!=RADIOLIB_ERR_NONE) ready=false;
    if(ready) {
        radioServiceTask=xTaskGetCurrentTaskHandle();
        if(radioServiceTask!=nullptr) radio.setDio1Action(radioWake);
    }
    // No Mesh instance, background advertising, automatic forwarding or RF ACK.
}

static void serviceRadio() {
    if(!ready) return;
    const uint64_t now=nowUs();
    if(transmitting) {
        uint32_t flags=0;
        const int16_t irqError=readIrqFlags(flags);
        if(irqError!=RADIOLIB_ERR_NONE) { contain(flags,irqError); return; }
        if((flags & RADIOLIB_SX126X_IRQ_TX_DONE) && !(flags & RADIOLIB_SX126X_IRQ_TIMEOUT)) {
            MT_PROFILE_SCOPE(txToRx,TxDoneToRxReturn);
            MT_PROFILE_SCOPE(txFinish,TxFinishCleanup);
            const int16_t error=radio.finishTransmit(); board.onAfterTransmit();
            MT_PROFILE_END(txFinish,error==RADIOLIB_ERR_NONE);
            if(burstActiveRequest) burstBoardPrepared=false;
            if(error!=RADIOLIB_ERR_NONE) { contain(flags,error); return; }
            transmitting=false;
            if(!burstOutcome(RF_DONE,now-startedUs,flags,0)) reportOutcome(RF_DONE,now-startedUs,flags,0);
            startReceive();
            MT_PROFILE_END(txToRx,armed && !fenced);
        } else if((flags & RADIOLIB_SX126X_IRQ_TIMEOUT) || now-startedUs>previousEstimate+1000000) {
            contain(flags,-1);
        }
    } else if(armed) {
        if(now>=deadlineUs) {
            armed=false;
            if(idleRadio()!=RADIOLIB_ERR_NONE) fenced=true;
            return;
        }
        uint32_t flags=0;
        if(readIrqFlags(flags)!=RADIOLIB_ERR_NONE) {
            armed=false; fenced=true; idleRadio(); return;
        }
        if(flags & RADIOLIB_SX126X_IRQ_RX_DONE) {
            if(burstQueue && output.freeSlots()<=2) {
                // Preserve the completed shared FIFO while USB is blocked;
                // continuous RX must not overwrite evidence awaiting space.
                if(radio.standby()!=RADIOLIB_ERR_NONE) { armed=false;fenced=true;idleRadio(); }
                return;
            }
            drainCompletedReceive(true);
        } else if((flags & RADIOLIB_SX126X_IRQ_TIMEOUT)
                  || ((flags & (RADIOLIB_SX126X_IRQ_HEADER_ERR|RADIOLIB_SX126X_IRQ_CRC_ERR))
                      && !(flags & (gfskMode ? RADIOLIB_SX126X_IRQ_SYNC_WORD_VALID : RADIOLIB_SX126X_IRQ_HEADER_VALID)))) {
            // A header/CRC failure need not include RX_DONE. Leaving that
            // sticky IRQ unhandled can reject a later good packet. Do not
            // interrupt a new valid header merely because an older error
            // remains set. RX_DONE still passes through RadioLib's CRC check.
            if(flags & (RADIOLIB_SX126X_IRQ_CRC_ERR|RADIOLIB_SX126X_IRQ_HEADER_ERR)) droppedRx++;
            startReceive(); // checked standby/IRQ-clear/RX launch, same lease
        }
    }
}

static void serialReadFailed() {
    serialReady=false; armed=false; fenced=true;
    const int16_t error=idleRadio();
    serialContainmentPending=error!=RADIOLIB_ERR_NONE;
    if(burstQueue) {
        burstStop();
        return;
    }
    if(transmitting) {
        board.onAfterTransmit(); transmitting=false;
        // A failed input channel cannot establish completion or erase debt.
        reportOutcome(UNKNOWN,nowUs()-startedUs,0,error==RADIOLIB_ERR_NONE ? -1 : error);
    }
}

void loop() {
    output.flush(Serial);
    if(resultPending && output.offer(previousResult)) resultPending=false;
    if(!serialReady) {
        // Preserve output evidence and retry checked containment even when RX
        // is unusable. A later standby success never upgrades UNKNOWN.
        if(serialContainmentPending) serialContainmentPending=idleRadio()!=RADIOLIB_ERR_NONE;
        burstService(false);
        output.flush(Serial); waitForRadioOrTick(); return;
    }
    serviceRadio(); // Drain RX_DONE before a queued TX can clear it or overwrite its FIFO.
    Frame frame;
    uint8_t bytes[USB_RX_DRAIN_BYTES];
    const size_t count=Serial.read(bytes,sizeof(bytes));
    if(count>sizeof(bytes)) {
        serialReadFailed(); output.flush(Serial); waitForRadioOrTick(); return;
    }
    for(size_t n=0;n<count;n++) if(decoder.feed(bytes[n],frame)) command(frame);
    burstService(true);
    output.flush(Serial);
    waitForRadioOrTick();
}
