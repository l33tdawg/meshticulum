#pragma once
#include "Arduino.h"
#define RADIO_CLASS FakeRadio
#define RADIOLIB_ERR_NONE 0
#define RADIOLIB_SX126X_IRQ_TX_DONE 1
#define RADIOLIB_SX126X_IRQ_RX_DONE 2
#define RADIOLIB_SX126X_IRQ_TIMEOUT 0x200
#define RADIOLIB_SX126X_IRQ_ALL 0x43ff
#define RADIOLIB_SX126X_CMD_GET_IRQ_STATUS 0x12
#define MESHTICULUM_BOARD_ID 1
#define MESHTICULUM_MAX_CHIP_DBM 10
#define MESHTICULUM_FIRMWARE "host-fault-test"
struct FakeBoard {
    unsigned before=0, after=0;
    void begin() {}
    void onBeforeTransmit() { before++; }
    void onAfterTransmit() { after++; }
};
inline FakeBoard board;
struct FakeRadio {
    int16_t standby_error=0, clear_error=0, start_error=0, finish_error=0, receive_error=0, config_error=0, read_error=0, irq_error=0;
    uint32_t flags=0, starts=0;
    std::vector<uint8_t> received={0xc0,0xdb,0,255};
    int16_t standby() { return standby_error; }
    int16_t clearIrqFlags(uint32_t) { if(!clear_error) flags=0; return clear_error; }
    int16_t startTransmit(uint8_t*,size_t) { starts++; return start_error; }
    int16_t finishTransmit() { flags=0; return finish_error; }
    int16_t startReceive() { flags=0; return receive_error; }
    uint32_t getIrqFlags() { return flags; }
    FakeRadio* getMod() { return this; }
    int16_t SPIreadStream(uint16_t,uint8_t* data,size_t) {
        data[0]=flags>>8; data[1]=flags; return irq_error;
    }
    uint64_t getTimeOnAir(size_t) { return 100000; }
    size_t getPacketLength() { return received.size(); }
    int16_t readData(uint8_t* p,size_t n) { memcpy(p,received.data(),n); return read_error; }
    float getRSSI() { return -92.5; }
    float getSNR() { return -2.3; }
    int16_t setFrequency(float) { return config_error; }
    int16_t setBandwidth(float) { return config_error; }
    int16_t setSpreadingFactor(uint8_t) { return config_error; }
    int16_t setCodingRate(uint8_t) { return config_error; }
    int16_t setOutputPower(int8_t) { return config_error; }
    int16_t setPreambleLength(uint16_t) { return config_error; }
    int16_t setSyncWord(uint8_t) { return config_error; }
    int16_t setCRC(uint8_t) { return config_error; }
};
inline FakeRadio radio;
inline bool radio_init() { return true; }
