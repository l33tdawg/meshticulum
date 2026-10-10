// Experimental raw LoRa coprocessor. It sends nothing until explicitly armed.
// No MeshCore/RNS framing, routing, auto ACK, announcements or key-store access.
#include <Arduino.h>
#include <Preferences.h>
#include <esp_timer.h>
#include <mbedtls/sha256.h>
#include <target.h>
#include "protocol.hpp"

extern RADIO_CLASS radio;
using namespace mtbench;

static Decoder decoder;
static OutputQueue output;
static uint8_t boot[16]={}, profile[30]={}, previousPacket[255]={}, previousDigest[32]={};
static uint64_t previousRequest=0, lastMutation=0, deadlineUs=0, startedUs=0, reservedUs=0, capUs=0;
static uint32_t attempted=0, capCount=0, droppedRx=0;
static uint16_t previousSize=0;
static uint64_t previousEstimate=0;
static bool ready=false, armed=false, transmitting=false, fenced=false, resultPending=false;
static Frame previousResult;

static Frame response(uint8_t type,uint64_t request) {
    Frame frame; frame.type=type; frame.request=request; memcpy(frame.boot,boot,16); return frame;
}
static uint64_t nowUs() { return uint64_t(esp_timer_get_time()); }
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
    // Standby is an attempted containment action, not proof of RF completion.
    radio.standby(); board.onAfterTransmit(); transmitting=false; armed=false; fenced=true;
    reportOutcome(UNKNOWN,nowUs()-startedUs,flags,error);
}
static void startReceive() {
    if(!armed || fenced) return;
    if(radio.startReceive()!=RADIOLIB_ERR_NONE) { armed=false; fenced=true; }
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
        || sf<7 || sf>12 || cr<5 || cr>8 || power<0 || power>MESHTICULUM_MAX_CHIP_DBM
        || preamble<8 || preamble>64 || sync==0 || duration<1000 || duration>300000
        || count<=attempted || count>100000 || budget<=reservedUs || budget>300000000ull) return false;
    if(radio.standby()!=RADIOLIB_ERR_NONE
        || radio.setFrequency(hz/1000000.0f)!=RADIOLIB_ERR_NONE
        || radio.setBandwidth(bw/1000.0f)!=RADIOLIB_ERR_NONE
        || radio.setSpreadingFactor(sf)!=RADIOLIB_ERR_NONE
        || radio.setCodingRate(cr)!=RADIOLIB_ERR_NONE
        || radio.setOutputPower(power)!=RADIOLIB_ERR_NONE
        || radio.setPreambleLength(preamble)!=RADIOLIB_ERR_NONE
        || radio.setSyncWord(sync)!=RADIOLIB_ERR_NONE
        || radio.setCRC(1)!=RADIOLIB_ERR_NONE) return false;
    deadlineUs=nowUs()+uint64_t(duration)*1000; capCount=count; capUs=budget;
    memcpy(profile,body,30); return true;
}

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
    if(frame.type==mtbench::STATUS && frame.size==0) {
        Frame reply=response(STATUS_REPLY,frame.request); reply.size=49;
        memcpy(reply.body,profile,30); put32(reply.body+30,attempted); put64(reply.body+34,reservedUs);
        put32(reply.body+42,droppedRx); reply.body[46]=armed; reply.body[47]=transmitting;
        reply.body[48]=fenced; output.offer(reply); return;
    }
    if(frame.type==TRANSMIT && previousRequest && frame.request==previousRequest) {
        if(frame.size==previousSize && memcmp(frame.body,previousPacket,previousSize)==0)
            output.offer(previousResult); // Exact replay returns evidence; it never retransmits.
        else refuse(frame,ATTEMPT_CONFLICT);
        return;
    }
    if(frame.type==ARM || frame.type==DISARM || frame.type==TRANSMIT) {
        if(frame.request<=lastMutation) { refuse(frame,OLD_ATTEMPT); return; }
        lastMutation=frame.request; // A delayed ARM must not silently extend its lease.
    }
    if(transmitting || resultPending) { refuse(frame,mtbench::BUSY); return; }
    if(frame.type==DISARM && frame.size==0) {
        armed=false;
        if(radio.standby()!=RADIOLIB_ERR_NONE) fenced=true;
        Frame reply=response(DISARM_REPLY,frame.request); reply.size=1; reply.body[0]=fenced;
        output.offer(reply); return;
    }
    if(fenced || !ready) { refuse(frame,FENCED); return; }
    if(frame.type==ARM && frame.size==30) {
        if(armed) { refuse(frame,mtbench::BUSY); return; }
        if(!configure(frame.body)) { armed=false; refuse(frame,BAD_PROFILE); return; }
        armed=true; startReceive();
        if(!armed) { refuse(frame,FENCED); return; }
        Frame reply=response(ARM_REPLY,frame.request); reply.size=30; memcpy(reply.body,profile,30);
        output.offer(reply); return;
    }
    if(frame.type!=TRANSMIT || frame.size<1 || frame.size>255) { refuse(frame,BAD_COMMAND); return; }
    if(frame.request<=previousRequest) { refuse(frame,OLD_ATTEMPT); return; }
    if(!armed || nowUs()>=deadlineUs) { armed=false; refuse(frame,NOT_ARMED); return; }
    const uint64_t estimate=radio.getTimeOnAir(frame.size);
    if(!estimate || attempted>=capCount || estimate>capUs-reservedUs
        || nowUs()+estimate+100000>=deadlineUs) { refuse(frame,BUDGET); return; }
    // Reserve once before touching TX. No refunds; host maintains debt across reboot.
    if(mbedtls_sha256_ret(frame.body,frame.size,previousDigest,0)!=0) {
        armed=false; fenced=true; radio.standby(); refuse(frame,FENCED); return;
    }
    previousRequest=frame.request; previousSize=frame.size; memcpy(previousPacket,frame.body,frame.size);
    previousEstimate=estimate; reservedUs+=estimate; attempted++; startedUs=nowUs();
    previousResult=response(TX_RESULT,previousRequest); previousResult.size=55;
    previousResult.body[0]=UNKNOWN; memcpy(previousResult.body+1,previousDigest,32);
    put64(previousResult.body+33,estimate);
    int16_t error=radio.standby();
    if(error==RADIOLIB_ERR_NONE) error=radio.clearIrqFlags(RADIOLIB_SX126X_IRQ_ALL);
    if(error!=RADIOLIB_ERR_NONE) { contain(0,error); return; }
    board.onBeforeTransmit();
    error=radio.startTransmit(previousPacket,previousSize);
    if(error!=RADIOLIB_ERR_NONE) { contain(0,error); return; }
    transmitting=true; reportOutcome(STARTED,0,0,0);
}

void setup() {
    Serial.begin(115200); Serial.setTxTimeoutMs(0);
    board.begin();
    put64(boot,ESP.getEfuseMac());
    // A separate NVS namespace preserves existing MeshCore identity/settings.
    Preferences state;
    if(!state.begin("mtbench",false)) return;
    uint64_t generation=state.getULong64("boot",0);
    if(generation==UINT64_MAX || state.putULong64("boot",generation+1)!=8) { state.end(); return; }
    state.end(); put64(boot+8,generation+1);
    ready=radio_init();
    if(ready && radio.standby()!=RADIOLIB_ERR_NONE) ready=false;
    // No Mesh instance, background advertising, automatic forwarding or RF ACK.
}

void loop() {
    output.flush(Serial);
    if(resultPending && output.offer(previousResult)) resultPending=false;
    Frame frame;
    for(unsigned n=0;n<128 && Serial.available();n++) if(decoder.feed(uint8_t(Serial.read()),frame)) command(frame);
    if(!ready) { delay(1); return; }
    const uint64_t now=nowUs();
    if(transmitting) {
        uint32_t flags=0;
        const int16_t irqError=readIrqFlags(flags);
        if(irqError!=RADIOLIB_ERR_NONE) { contain(flags,irqError); return; }
        if((flags & RADIOLIB_SX126X_IRQ_TX_DONE) && !(flags & RADIOLIB_SX126X_IRQ_TIMEOUT)) {
            const int16_t error=radio.finishTransmit(); board.onAfterTransmit();
            if(error!=RADIOLIB_ERR_NONE) { contain(flags,error); return; }
            transmitting=false; reportOutcome(RF_DONE,now-startedUs,flags,0); startReceive();
        } else if((flags & RADIOLIB_SX126X_IRQ_TIMEOUT) || now-startedUs>previousEstimate+1000000) {
            contain(flags,-1);
        }
    } else if(armed) {
        if(now>=deadlineUs) {
            armed=false;
            if(radio.standby()!=RADIOLIB_ERR_NONE) fenced=true;
            return;
        }
        uint32_t flags=0;
        if(readIrqFlags(flags)!=RADIOLIB_ERR_NONE) {
            armed=false; fenced=true; radio.standby(); return;
        }
        if(flags & RADIOLIB_SX126X_IRQ_RX_DONE) {
            const size_t length=radio.getPacketLength();
            Frame received=response(RECEIVE,0);
            if(length>=1 && length<=255 && output.freeSlots()>2) {
                const int16_t error=radio.readData(received.body+4,length);
                if(error==RADIOLIB_ERR_NONE) {
                    put16(received.body,uint16_t(int16_t(radio.getRSSI()*10)));
                    put16(received.body+2,uint16_t(int16_t(radio.getSNR()*10)));
                    received.size=length+4;
                    if(!output.offer(received)) droppedRx++;
                } else droppedRx++;
            } else { droppedRx++; radio.standby(); }
            startReceive();
        }
    }
    output.flush(Serial);
    delay(1);
}
