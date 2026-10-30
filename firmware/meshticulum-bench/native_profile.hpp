#pragma once
// Diagnostic-only, fixed-memory timing. No authority, reset, allocation or I/O.
#include "protocol.hpp"
#ifndef MTBENCH_NATIVE_PROFILE
#define MTBENCH_NATIVE_PROFILE 0
#endif
#if MTBENCH_NATIVE_PROFILE != 0 && MTBENCH_NATIVE_PROFILE != 1
#error "MTBENCH_NATIVE_PROFILE must be 0 or 1"
#endif

namespace mtbench { namespace profiling {
enum class Phase : uint8_t {
    TxFinishCleanup, RxStartCall, TxDoneToRxReturn, IssuedToStarted,
    IssuedPublish, IssueStandby, IssueIrqReread, IssueRxDrain,
    IssueIrqClear, IssueCopyPrepare, IssueTxStartCall, StartedPublish, Count
};
constexpr uint8_t PHASES=uint8_t(Phase::Count), RECORD_BYTES=40;
// The first fourteen counters partition the existing droppedRx increments.
// These are observed software reasons, not identities or inferred RF causes.
enum class RxReason : uint8_t {
    ReadPayloadCrc, ReadHeader, ReadBoth, ReadIntegrityUnattributed,
    ReadTimeout, ReadOtherError, InvalidReportedLength, OutputReserveLow,
    OutputOfferFailed, SyncTxHeaderPreempted, IrqRestartCrcOnly,
    IrqRestartHeaderOnly, IrqRestartBoth, QueuedHeaderStall,
    RxExported, TimeoutOnlyRestart, BackpressureDeferVisits, Count
};
constexpr uint8_t RX_REASONS=uint8_t(RxReason::Count), RX_DROP_REASONS=14;
struct RxReasons {
    uint32_t counts[RX_REASONS]={}, saturated_mask=0;
    void record(RxReason reason) {
        const uint8_t index=uint8_t(reason);
        if(index>=RX_REASONS) return;
        if(counts[index]==UINT32_MAX) saturated_mask|=uint32_t(1)<<index;
        else ++counts[index];
    }
    void readFailure(bool integrity,bool timeout,bool crc,bool header) {
        record(integrity ? (crc ? (header ? RxReason::ReadBoth : RxReason::ReadPayloadCrc)
                               : (header ? RxReason::ReadHeader : RxReason::ReadIntegrityUnattributed))
                        : (timeout ? RxReason::ReadTimeout : RxReason::ReadOtherError));
    }
    void restart(bool crc,bool header) {
        record(crc ? (header ? RxReason::IrqRestartBoth : RxReason::IrqRestartCrcOnly)
                   : (header ? RxReason::IrqRestartHeaderOnly : RxReason::TimeoutOnlyRestart));
    }
    uint16_t encode(uint8_t* body) const {
        memcpy(body,"MNR1",4);body[4]=1;body[5]=RX_REASONS;body[6]=4;body[7]=1;
        put32(body+8,saturated_mask);
        for(uint8_t n=0;n<RX_REASONS;n++) put32(body+12+4*n,counts[n]);
        return 12+4*RX_REASONS;
    }
};
static_assert(sizeof(RxReasons)==72,"RX reason storage must remain fixed");
// Calls include invalid-clock samples. Duration aggregates exclude them.
// Flags: any counter/total saturation=1; clock regression=2.
struct Aggregate {
    uint32_t calls=0, failures=0, invalid=0, flags=0;
    uint64_t total_us=0, min_us=0, max_us=0;
    bool valid=false;
    void increment(uint32_t& value) {
        if(value==UINT32_MAX) flags|=1; else ++value;
    }
    void record(uint64_t before,uint64_t after,bool success) {
        increment(calls);if(!success) increment(failures);
        if(after<before) { increment(invalid);flags|=2;return; }
        const uint64_t elapsed=after-before;
        if(!valid || elapsed<min_us) min_us=elapsed;
        if(elapsed>max_us) max_us=elapsed;
        valid=true;
        if(elapsed>UINT64_MAX-total_us) { total_us=UINT64_MAX;flags|=1; }
        else total_us+=elapsed;
    }
    void encode(uint8_t* body) const {
        put32(body,calls);put32(body+4,failures);put32(body+8,invalid);put32(body+12,flags);
        put64(body+16,total_us);put64(body+24,min_us);put64(body+32,max_us);
    }
};
struct Profile {
    Aggregate rows[PHASES];
    RxReasons rx;
    void record(Phase phase,uint64_t before,uint64_t after,bool success) {
        const uint8_t index=uint8_t(phase);
        if(index<PHASES) rows[index].record(before,after,success);
    }
    // Empty query: schema/capability. One-byte query: one fixed aggregate.
    // No clocks are read and no state is changed while serializing.
    uint16_t encode(uint8_t* body,int phase=-1) const {
        memcpy(body,"MNP1",4);body[4]=PHASES;body[5]=RECORD_BYTES;
        body[6]=phase<0 ? 255 : uint8_t(phase);body[7]=1; // boot-cumulative, microseconds
        if(phase<0) return 8;
        if(phase>=PHASES) return 0;
        rows[phase].encode(body+8);return 8+RECORD_BYTES;
    }
};
static_assert(sizeof(Profile)<=1024,"native profile storage must remain bounded");
// A partial/early-return parent sample stays failed. This cannot change the
// original operation's result, cleanup, fencing or exception behavior.
class Scope {
    Profile& profile_;Phase phase_;uint64_t (*clock_)();uint64_t before_;bool live_=true;
public:
    Scope(Profile& profile,Phase phase,uint64_t (*clock)(),uint64_t before)
        :profile_(profile),phase_(phase),clock_(clock),before_(before) {}
    ~Scope() { if(live_) finish(false); }
    void finish(bool success) {
        if(live_) { const uint64_t after=clock_();live_=false;profile_.record(phase_,before_,after,success); }
    }
};
}} // namespace mtbench::profiling

#if MTBENCH_NATIVE_PROFILE
#define MT_PROFILE_SCOPE_AT(name,phase,at) mtbench::profiling::Scope name(nativeProfile,mtbench::profiling::Phase::phase,nowUs,at)
#define MT_PROFILE_SCOPE(name,phase) MT_PROFILE_SCOPE_AT(name,phase,nowUs())
#define MT_PROFILE_END(name,success) name.finish(success)
#define MT_RX_COUNT(reason) nativeProfile.rx.record(mtbench::profiling::RxReason::reason)
#define MT_RX_READ_FAILURE(error,flags) nativeProfile.rx.readFailure( \
    (error)==RADIOLIB_ERR_CRC_MISMATCH,(error)==RADIOLIB_ERR_RX_TIMEOUT, \
    ((flags)&RADIOLIB_SX126X_IRQ_CRC_ERR)!=0, \
    ((flags)&RADIOLIB_SX126X_IRQ_HEADER_ERR)!=0 && \
    !((flags)&RADIOLIB_SX126X_IRQ_HEADER_VALID))
#define MT_RX_LENGTH_OR_RESERVE(length) nativeProfile.rx.record( \
    (length)<1 || (length)>255 ? mtbench::profiling::RxReason::InvalidReportedLength \
                             : mtbench::profiling::RxReason::OutputReserveLow)
#define MT_RX_RESTART(flags) nativeProfile.rx.restart( \
    ((flags)&RADIOLIB_SX126X_IRQ_CRC_ERR)!=0,((flags)&RADIOLIB_SX126X_IRQ_HEADER_ERR)!=0)
#else
// In a normal build, even the clock and success expressions are unevaluated.
#define MT_PROFILE_SCOPE_AT(name,phase,at) ((void)0)
#define MT_PROFILE_SCOPE(name,phase) ((void)0)
#define MT_PROFILE_END(name,success) ((void)0)
#define MT_RX_COUNT(reason) ((void)0)
#define MT_RX_READ_FAILURE(error,flags) ((void)0)
#define MT_RX_LENGTH_OR_RESERVE(length) ((void)0)
#define MT_RX_RESTART(flags) ((void)0)
#endif
