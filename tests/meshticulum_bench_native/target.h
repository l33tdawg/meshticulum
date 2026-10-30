#pragma once
#include "Arduino.h"
#define RADIO_CLASS FakeRadio
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_SX126X_IRQ_TX_DONE 1
#define RADIOLIB_SX126X_IRQ_RX_DONE 2
#define RADIOLIB_SX126X_IRQ_HEADER_VALID 0x10
#define RADIOLIB_SX126X_IRQ_SYNC_WORD_VALID 0x08
#define RADIOLIB_SX126X_IRQ_HEADER_ERR 0x20
#define RADIOLIB_SX126X_IRQ_CRC_ERR 0x40
#define RADIOLIB_SX126X_IRQ_TIMEOUT 0x200
#define RADIOLIB_SX126X_IRQ_ALL 0x43ff
#define RADIOLIB_SX126X_CMD_GET_IRQ_STATUS 0x12
#define MESHTICULUM_BOARD_ID 1
#define SX126X_DIO3_TCXO_VOLTAGE 1.8f
#define RADIOLIB_SHAPING_0_5 2
#define MESHTICULUM_MAX_CHIP_DBM 10
#define MESHTICULUM_FIRMWARE "host-fault-test"
struct FakeBoard {
    unsigned before=0, after=0;
    uint64_t before_advance_us=0, after_advance_us=0;
    void begin() {}
    void onBeforeTransmit() { before++;fake_time+=before_advance_us; }
    void onAfterTransmit() { after++;fake_time+=after_advance_us; }
};
inline FakeBoard board;
struct FakeRadio {
    void (*dio1_action)()=nullptr;
    void setDio1Action(void (*action)()) { dio1_action=action; }
    int16_t standby_error=0, clear_error=0, start_error=0, finish_error=0, receive_error=0, config_error=0, read_error=0, irq_error=0, warm_error=0;
    uint32_t flags=0, starts=0, receive_starts=0;
    bool warm=false;
    bool gfsk=false;
    uint32_t gfsk_rate=0;
    bool receiving=false, shared_fifo=false, overwrite_during_read=false;
    uint32_t irq_on_standby=0, irq_on_receive=0;
    uint64_t read_advance_us=0, receive_advance_us=0;
    uint64_t standby_advance_us=0, clear_advance_us=0, start_advance_us=0,
        finish_advance_us=0, irq_advance_us=0;
    std::vector<uint8_t> received={0xc0,0xdb,0,255};
    int16_t standby() { receiving=false; flags|=irq_on_standby; irq_on_standby=0;fake_time+=standby_advance_us; return standby_error; }
    int16_t setStandbyXOSC(bool enabled) { if(warm_error) return warm_error; warm=enabled; return standby(); }
    int16_t clearIrqFlags(uint32_t) { if(!clear_error) flags=0;fake_time+=clear_advance_us; return clear_error; }
    int16_t startTransmit(uint8_t* p,size_t n) { starts++; if(shared_fifo) received.assign(p,p+n);fake_time+=start_advance_us; return start_error; }
    int16_t finishTransmit() { flags=0;fake_time+=finish_advance_us; return finish_error; }
    int16_t startReceive() { receive_starts++; flags=irq_on_receive;irq_on_receive=0;receiving=true;fake_time+=receive_advance_us;return receive_error; }
    uint32_t getIrqFlags() { return flags; }
    FakeRadio* getMod() { return this; }
    int16_t SPIreadStream(uint16_t,uint8_t* data,size_t) {
        data[0]=flags>>8; data[1]=flags;fake_time+=irq_advance_us; return irq_error;
    }
    uint64_t getTimeOnAir(size_t) { return 100000; }
    size_t getPacketLength() { return received.size(); }
    int16_t readData(uint8_t* p,size_t n) {
        const auto irq=flags;
        if(overwrite_during_read && receiving) received.assign(received.size(),0x55);
        if(read_error) return read_error;
        memcpy(p,received.data(),n); flags=0; fake_time+=read_advance_us;
        if((irq & RADIOLIB_SX126X_IRQ_CRC_ERR)
            || ((irq & RADIOLIB_SX126X_IRQ_HEADER_ERR) && !(irq & (gfsk ? RADIOLIB_SX126X_IRQ_SYNC_WORD_VALID : RADIOLIB_SX126X_IRQ_HEADER_VALID)))) return -7;
        return 0;
    }
    float getRSSI() { return -92.5; }
    float getRSSI(bool packet) { assert(packet);return -92.5; }
    float getSNR() { return -2.3; }
    int16_t setFrequency(float) { return config_error; }
    int16_t setBandwidth(float) { return config_error; }
    int16_t setSpreadingFactor(uint8_t) { return config_error; }
    int16_t setCodingRate(uint8_t) { return config_error; }
    int16_t setOutputPower(int8_t) { return config_error; }
    int16_t setPreambleLength(uint16_t) { return config_error; }
    int16_t setSyncWord(uint8_t) { return config_error; }
    int16_t setCRC(uint8_t) { return config_error; }
    int16_t beginFSK(float,float br,float dev,float bw,int8_t,uint16_t pre,float tcxo,bool ldo) {
        assert(((br==125 && dev==31.25 && bw==312) || (br==250 && dev==62.5 && bw==467))
            && pre==32 && tcxo==1.8f && !ldo);
        if(!config_error) { gfsk=true;gfsk_rate=uint32_t(br*1000); } return config_error;
    }
    int16_t setDataShaping(uint8_t n) { assert(n==2); return config_error; }
    int16_t setSyncWord(uint8_t* data,size_t n) { assert(n==8 && !memcmp(data,"MTGFSK01",8)); return config_error; }
    int16_t variablePacketLengthMode(uint8_t n) { assert(n==255); return config_error; }
    int16_t setCRC(uint8_t n,uint16_t seed,uint16_t poly,bool inverted) { assert(n==2 && seed==65535 && poly==0x1021 && !inverted);return config_error; }
    int16_t setWhitening(bool enabled,uint16_t seed) { assert(enabled && seed==511); return config_error; }
};
inline FakeRadio radio;
inline bool radio_init() { radio.gfsk=false;return true; }
