#pragma once
// Dormant source prototype; not included by main.cpp or the firmware recipe.
// This is a single-loop-owned state machine, not RF authority or a driver.
// The adapter must verify the admitted DATA lane, actual profile/airtime,
// original boot/context/deadline and durable host reservation before enqueue.
// It must check RX and evidence-output capacity before issue(), bind every
// driver result to the returned exact packet, and physically contain the radio
// whenever containmentRequired() is true, retrying failed containment. Drain
// RX_DONE/USB receive evidence before each claim and retain the admitted reverse
// listen window after the finite DATA burst. RF_DONE never calls issue() itself.
// No peer ACK, crypto or retry runs here.
// Queue acceptance is bounded delegation for at most two seconds. Binding and
// deadlines cannot prove instantaneous host revocation; cancel must reach the
// MCU, and uncertainty after issue still requires checked physical containment.
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace mtbench { namespace experimental {
constexpr std::size_t TX_BURST_DEPTH=4, TX_PACKET_BYTES=255;
constexpr std::uint64_t MAX_AUTONOMOUS_HOLD_US=2000000, START_GUARD_US=100000;

struct Binding { std::uint8_t boot[16]={}, context_sha256[32]={}; };
struct Counters { std::uint32_t attempts=0; std::uint64_t reserved_airtime_us=0; };
// One instance lives for the whole physical MCU boot, alongside legacy debt.
// ARM/rebinding/cancellation must not recreate or zero this state. The adapter
// reconciles every other radio path with these same counters/highwater/fence
// and the same non-regressing, extended MCU clock.
struct BootState {
    std::uint8_t boot[16]={};
    Counters counters, paid_floor;
    std::uint64_t request_highwater=0, last_now_us=0;
    bool fenced=false, containment_required=false, queue_owned=false, initialized=false;
};
struct Admission {
    Binding binding;
    std::uint64_t lease_deadline_us=0;
    std::uint32_t max_attempts=0;
    std::uint64_t max_airtime_us=0;
};
enum class PacketKind : std::uint8_t { Data=1, Ack=2, Control=3 };
struct Packet {
    std::uint64_t request=0, deadline_us=0, estimated_airtime_us=0;
    std::uint16_t size=0;
    PacketKind kind=PacketKind::Data;
    std::uint8_t digest[32]={}, ciphertext[TX_PACKET_BYTES]={};
};
enum class State : std::uint8_t { Empty, Queued, Issued, Started, RfDone, NotSent, Unknown };
enum class Code : std::uint8_t {
    Accepted, Replayed, WrongBinding, Invalid, Conflict, Stale, Full, Budget,
    Expired, Busy, RxPending, OutputBlocked, Fenced, ClockRegressed, Missing,
    NotTerminal, Mismatch
};
// Driver proof only. RF_DONE is not an authenticated peer/application receipt.
struct Evidence {
    Binding binding;
    std::uint64_t request=0, estimated_airtime_us=0, driver_elapsed_us=0;
    std::uint16_t size=0;
    std::uint8_t digest[32]={};
    State state=State::Empty;
    std::uint32_t irq_flags=0;
    std::int16_t driver_error=0;
};
struct Dispatch { Code code; const Packet* packet; };

// Keep this object alive until every retained outcome has been acknowledged.
// Destruction is a fail-closed fallback, not durable evidence storage: it keeps
// boot debt/highwater/fence, but host reconciliation must treat any lost driver
// outcome as uncertain. A physical reset likewise needs explicit reconciliation.
class TxBurstQueue {
    struct Slot { Packet packet; State state=State::Empty; Evidence evidence; };
    const Admission admission_;
    BootState& boot_;
    Counters& counters_;
    Counters& paid_floor_;
    std::uint64_t& high_request_;
    std::uint64_t& last_now_;
    bool& fenced_;
    bool& containment_required_;
    Slot slots_[TX_BURST_DEPTH];
    std::uint64_t burst_deadline_=0;
    std::size_t admitted_=0, active_=TX_BURST_DEPTH;
    bool valid_=false, owns_=false;

    static bool nonzero(const std::uint8_t* bytes,std::size_t size) {
        for(std::size_t n=0;n<size;n++) if(bytes[n]) return true;
        return false;
    }
    bool bound(const Binding& value) const {
        return std::memcmp(value.boot,admission_.binding.boot,16)==0
            && std::memcmp(value.context_sha256,admission_.binding.context_sha256,32)==0;
    }
    static bool terminal(State state) {
        return state==State::RfDone || state==State::NotSent || state==State::Unknown;
    }
    static bool same(const Packet& a,const Packet& b) {
        return a.request==b.request && a.deadline_us==b.deadline_us
            && a.estimated_airtime_us==b.estimated_airtime_us && a.size==b.size && a.kind==b.kind
            && std::memcmp(a.digest,b.digest,32)==0 && std::memcmp(a.ciphertext,b.ciphertext,a.size)==0;
    }
    bool proof(const Slot& slot,const Evidence& value) const {
        return bound(value.binding) && value.request==slot.packet.request
            && value.size==slot.packet.size && value.estimated_airtime_us==slot.packet.estimated_airtime_us
            && std::memcmp(value.digest,slot.packet.digest,32)==0;
    }
    void state(Slot& slot,State value) {
        slot.state=value;
        slot.evidence.binding=admission_.binding;
        slot.evidence.request=slot.packet.request;
        slot.evidence.estimated_airtime_us=slot.packet.estimated_airtime_us;
        slot.evidence.size=slot.packet.size;
        std::memcpy(slot.evidence.digest,slot.packet.digest,32);
        slot.evidence.state=value;
    }
    void stop(bool fence) {
        for(auto& slot:slots_) {
            if(slot.state==State::Issued || slot.state==State::Started) {
                state(slot,State::Unknown); containment_required_=true;
            } else if(slot.state==State::Queued) state(slot,State::NotSent);
        }
        active_=TX_BURST_DEPTH;
        fenced_=fenced_ || fence || containment_required_;
    }
    bool observe(std::uint64_t now,bool expire=true) {
        if(now<last_now_ || counters_.attempts<paid_floor_.attempts
            || counters_.reserved_airtime_us<paid_floor_.reserved_airtime_us
            || counters_.attempts>admission_.max_attempts
            || counters_.reserved_airtime_us>admission_.max_airtime_us) {
            stop(true); return false;
        }
        last_now_=now; paid_floor_=counters_;
        if(fenced_) stop(true);
        if(expire && burst_deadline_ && now>=burst_deadline_) stop(false);
        return true;
    }
public:
    TxBurstQueue(const Admission& admission,BootState& boot):
        admission_(admission),boot_(boot),counters_(boot.counters),paid_floor_(boot.paid_floor),
        high_request_(boot.request_highwater),last_now_(boot.last_now_us),fenced_(boot.fenced),containment_required_(boot.containment_required) {
        if(!boot.initialized) { boot.paid_floor=boot.counters; boot.initialized=true; }
        valid_=nonzero(admission.binding.boot,16) && nonzero(admission.binding.context_sha256,32)
            && std::memcmp(admission.binding.boot,boot.boot,16)==0
            && admission.lease_deadline_us>0 && admission.max_attempts>0 && admission.max_attempts<=100000
            && admission.max_airtime_us>0 && admission.max_airtime_us<=300000000
            && counters_.attempts>=paid_floor_.attempts && counters_.reserved_airtime_us>=paid_floor_.reserved_airtime_us
            && counters_.attempts<=admission.max_attempts && counters_.reserved_airtime_us<=admission.max_airtime_us;
        if(boot.queue_owned) { valid_=false; containment_required_=true; }
        if(!valid_) fenced_=true;
        else { boot.queue_owned=true; owns_=true; }
    }
    ~TxBurstQueue() {
        if(!owns_) return;
        for(const auto& slot:slots_) if(slot.state!=State::Empty) { stop(true); break; }
        boot_.queue_owned=false;
    }
    TxBurstQueue(const TxBurstQueue&)=delete;
    TxBurstQueue& operator=(const TxBurstQueue&)=delete;

    // independent_estimate_us is computed by the trusted adapter from the
    // actual admitted radio profile, not copied from the host descriptor.
    Code enqueue(const Binding& binding,const Packet& packet,std::uint64_t independent_estimate_us,
                 std::uint64_t now) {
        if(!bound(binding)) return Code::WrongBinding;
        if(!observe(now)) return Code::ClockRegressed;
        if(packet.size<1 || packet.size>TX_PACKET_BYTES || !packet.request
            || packet.kind!=PacketKind::Data || !nonzero(packet.digest,32)
            || !packet.estimated_airtime_us || packet.estimated_airtime_us!=independent_estimate_us)
            return Code::Invalid;
        for(const auto& slot:slots_) if(slot.state!=State::Empty && slot.packet.request==packet.request)
            return same(slot.packet,packet) ? Code::Replayed : Code::Conflict;
        if(fenced_) return Code::Fenced;
        if(packet.request<=high_request_) return Code::Stale;
        if(packet.deadline_us<=now || packet.deadline_us>admission_.lease_deadline_us
            || packet.deadline_us-now>MAX_AUTONOMOUS_HOLD_US) return Code::Expired;
        if(burst_deadline_ && packet.deadline_us!=burst_deadline_) return Code::Conflict;
        if(admitted_==TX_BURST_DEPTH) return Code::Full;
        std::uint64_t needed=packet.estimated_airtime_us+START_GUARD_US;
        if(needed<packet.estimated_airtime_us) return Code::Invalid;
        for(const auto& slot:slots_) if(slot.state==State::Queued || slot.state==State::Issued || slot.state==State::Started) {
            if(slot.packet.estimated_airtime_us>UINT64_MAX-START_GUARD_US) return Code::Invalid;
            const auto extra=slot.packet.estimated_airtime_us+START_GUARD_US;
            if(needed>UINT64_MAX-extra) return Code::Invalid;
            needed+=extra;
        }
        if(needed>packet.deadline_us-now) return Code::Expired;
        if(counters_.attempts>=admission_.max_attempts
            || packet.estimated_airtime_us>admission_.max_airtime_us-counters_.reserved_airtime_us)
            return Code::Budget;
        for(auto& slot:slots_) if(slot.state==State::Empty) {
            // All checks precede charging. No cancellation, expiry, evidence
            // acknowledgment or queue reset ever decrements these counters.
            counters_.attempts++; counters_.reserved_airtime_us+=packet.estimated_airtime_us;
            paid_floor_=counters_; slot.packet=packet; slot.evidence=Evidence{}; state(slot,State::Queued);
            high_request_=packet.request; burst_deadline_=packet.deadline_us; admitted_++;
            return Code::Accepted;
        }
        return Code::Full;
    }

    Dispatch issue(std::uint64_t now,bool rx_pending,bool evidence_output_available) {
        if(!observe(now)) return {Code::ClockRegressed,nullptr};
        if(fenced_) return {Code::Fenced,nullptr};
        if(active_!=TX_BURST_DEPTH) return {Code::Busy,nullptr};
        if(rx_pending) return {Code::RxPending,nullptr};
        if(!evidence_output_available) return {Code::OutputBlocked,nullptr};
        std::size_t next=TX_BURST_DEPTH;
        for(std::size_t n=0;n<TX_BURST_DEPTH;n++) if(slots_[n].state==State::Queued
            && (next==TX_BURST_DEPTH || slots_[n].packet.request<slots_[next].packet.request)) next=n;
        if(next==TX_BURST_DEPTH) return {Code::Missing,nullptr};
        auto& slot=slots_[next];
        if(now>=slot.packet.deadline_us || slot.packet.estimated_airtime_us+START_GUARD_US>slot.packet.deadline_us-now) {
            stop(false); return {Code::Expired,nullptr};
        }
        // ISSUED is uncertain before the driver call. Absence of STARTED or
        // RF_DONE is never sufficient evidence for NOT_SENT.
        active_=next; state(slot,State::Issued);
        return {Code::Accepted,&slot.packet};
    }

    Code settle(const Evidence& value,std::uint64_t now) {
        // A late, exact physical result still accounts the earlier issued TX.
        // Expiry fences future work; it does not erase its completion evidence.
        if(!observe(now,false)) return Code::ClockRegressed;
        if(!bound(value.binding)) return Code::WrongBinding;
        for(std::size_t n=0;n<TX_BURST_DEPTH;n++) {
            auto& slot=slots_[n];
            if(slot.state==State::Empty || slot.packet.request!=value.request) continue;
            if(!proof(slot,value)) { stop(true); return Code::Mismatch; }
            if(terminal(slot.state)) {
                const auto& held=slot.evidence;
                if(held.state==value.state && held.driver_elapsed_us==value.driver_elapsed_us
                    && held.irq_flags==value.irq_flags && held.driver_error==value.driver_error) return Code::Replayed;
                stop(true); return Code::Conflict;
            }
            if(active_!=n || (slot.state!=State::Issued && slot.state!=State::Started)) {
                stop(true); return Code::Mismatch;
            }
            const bool started=value.state==State::Started && !value.driver_elapsed_us
                && !value.irq_flags && !value.driver_error;
            const bool done=value.state==State::RfDone && value.driver_elapsed_us>0
                && (value.irq_flags&1u) && !(value.irq_flags&0x200u) && !value.driver_error;
            if(started || done) {
                slot.evidence=value; slot.state=value.state;
                if(done) active_=TX_BURST_DEPTH;
                if(burst_deadline_ && now>=burst_deadline_) stop(false);
                return Code::Accepted;
            }
            // Explicit UNKNOWN or invalid driver evidence retains uncertainty.
            if(value.state==State::Unknown) slot.evidence=value;
            stop(true); return value.state==State::Unknown ? Code::Accepted : Code::Mismatch;
        }
        return Code::Missing;
    }

    Code poll(std::uint64_t now) { return observe(now) ? (fenced_ ? Code::Fenced : Code::Accepted) : Code::ClockRegressed; }
    void cancel() { stop(true); }
    bool containmentRequired() const { return containment_required_; }
    bool fenced() const { return fenced_; }
    // Report checked physical containment; this never unfences or refunds debt.
    void containmentComplete(bool success) { if(success && owns_) containment_required_=false; }
    bool evidence(std::uint64_t request,Evidence& value) const {
        for(const auto& slot:slots_) if(slot.state!=State::Empty && slot.packet.request==request) {
            value=slot.evidence; return true;
        }
        return false;
    }
    Code acknowledge(const Binding& binding,std::uint64_t request,const std::uint8_t digest[32],State outcome) {
        if(!bound(binding)) return Code::WrongBinding;
        for(auto& slot:slots_) if(slot.state!=State::Empty && slot.packet.request==request) {
            if(!terminal(slot.state)) return Code::NotTerminal;
            if(containment_required_) return Code::Busy;
            if(slot.state!=outcome || std::memcmp(slot.packet.digest,digest,32)!=0) return Code::Mismatch;
            slot=Slot{};
            bool any=false; for(const auto& held:slots_) if(held.state!=State::Empty) any=true;
            if(!any) { admitted_=0; burst_deadline_=0; }
            return Code::Accepted;
        }
        return request<=high_request_ ? Code::Stale : Code::Missing;
    }
};
}} // namespace mtbench::experimental
