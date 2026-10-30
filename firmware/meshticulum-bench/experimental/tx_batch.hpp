#pragma once
// Dormant MBQ2 source prototype. No serial opcode or driver is installed.
// Single-loop ownership and a trusted SHA-256 / actual-profile airtime caller
// are mandatory. The host must durably charge EVERY packet before COMMIT.
#include "tx_burst.hpp"

namespace mtbench { namespace experimental {
constexpr std::uint64_t BATCH_RX_US=5000, BATCH_REVERSE_US=100000;
using BatchHash=bool (*)(const std::uint8_t*,std::size_t,std::uint8_t*);
struct BatchDescriptor {
    Binding binding;
    std::uint64_t token=0, job=0, deadline_us=0;
    std::uint8_t count=0, manifest[32]={};
};
// Keep alongside BootState for the entire boot, including between job owners.
// No ARM/rebinding/owner reconstruction may clear this listening floor.
struct BatchPacing { std::uint64_t next_issue_us=0; };
struct BatchRelease {
    std::uint64_t request=0, event_us=0;
    State state=State::Empty;
    std::uint8_t digest[32]={};
};

inline void batchPut64(std::uint8_t*& out,std::uint64_t value) {
    for(int n=7;n>=0;n--) *out++=std::uint8_t(value>>(n*8));
}
// Canonical network-order manifest: MBQ2, boot, context, token, job, deadline,
// count, then ordered (request, estimate, size, digest). At most 277 bytes.
inline bool batchManifest(const BatchDescriptor& d,const Packet* packets,
                          BatchHash hash,std::uint8_t out[32]) {
    if(!hash || !packets || d.count<1 || d.count>TX_BURST_DEPTH) return false;
    std::uint8_t bytes[277]={}; auto* p=bytes;
    std::memcpy(p,"MBQ2",4);p+=4;
    std::memcpy(p,d.binding.boot,16);p+=16;
    std::memcpy(p,d.binding.context_sha256,32);p+=32;
    batchPut64(p,d.token);batchPut64(p,d.job);batchPut64(p,d.deadline_us);*p++=d.count;
    for(std::size_t n=0;n<d.count;n++) {
        const auto& packet=packets[n];batchPut64(p,packet.request);
        batchPut64(p,packet.estimated_airtime_us);
        *p++=std::uint8_t(packet.size>>8);*p++=std::uint8_t(packet.size);
        std::memcpy(p,packet.digest,32);p+=32;
    }
    return hash(bytes,std::size_t(p-bytes),out);
}

class TxBatchJob {
    const Admission admission_;
    const std::uint64_t token_;
    BootState& boot_;
    BatchPacing& pacing_;
    const BatchHash hash_;
    TxBurstQueue queue_;
    BatchDescriptor descriptor_;
    Packet staged_[TX_BURST_DEPTH];
    std::uint64_t estimates_[TX_BURST_DEPTH]={};
    bool present_[TX_BURST_DEPTH]={};
    BatchRelease released_[TX_BURST_DEPTH];
    std::uint64_t commit_request_=0, release_request_=0;
    bool begun_=false, committed_=false, released_all_=false, revoked_=false;

    bool bound(const BatchDescriptor& d) const {
        return d.token==token_ && std::memcmp(d.binding.boot,admission_.binding.boot,16)==0
            && std::memcmp(d.binding.context_sha256,admission_.binding.context_sha256,32)==0;
    }
    bool exact(const BatchDescriptor& d) const {
        return bound(d) && d.token==descriptor_.token && d.job==descriptor_.job
            && d.deadline_us==descriptor_.deadline_us && d.count==descriptor_.count
            && std::memcmp(d.manifest,descriptor_.manifest,32)==0;
    }
    static bool samePacket(const Packet& a,const Packet& b) {
        return a.request==b.request && a.deadline_us==b.deadline_us
            && a.estimated_airtime_us==b.estimated_airtime_us && a.size==b.size
            && a.kind==b.kind && std::memcmp(a.digest,b.digest,32)==0
            && std::memcmp(a.ciphertext,b.ciphertext,a.size)==0;
    }
    static bool sameRelease(const BatchRelease& a,const BatchRelease& b) {
        return a.request==b.request && a.event_us==b.event_us && a.state==b.state
            && std::memcmp(a.digest,b.digest,32)==0;
    }
    Code clock(std::uint64_t now) { return queue_.poll(now); }
    void listen(std::uint64_t now,std::uint64_t duration) {
        if(now>UINT64_MAX-duration) { queue_.cancel();pacing_.next_issue_us=UINT64_MAX; }
        else if(pacing_.next_issue_us<now+duration) pacing_.next_issue_us=now+duration;
    }
public:
    TxBatchJob(const Admission& admission,std::uint64_t original_open_token,
               BootState& boot,BatchPacing& pacing,BatchHash hash):
        admission_(admission),token_(original_open_token),boot_(boot),pacing_(pacing),
        hash_(hash),queue_(admission,boot) {}
    TxBatchJob(const TxBatchJob&)=delete;
    TxBatchJob& operator=(const TxBatchJob&)=delete;

    Code begin(const BatchDescriptor& d,std::uint64_t now) {
        if(!bound(d)) return Code::WrongBinding;
        const auto observed=clock(now);if(observed!=Code::Accepted) return observed;
        if(begun_) return exact(d) ? Code::Replayed : Code::Conflict;
        if(revoked_) return Code::Fenced;
        bool nonzero=false;for(auto b:d.manifest) nonzero=nonzero || b;
        if(!hash_ || admission_.max_attempts>1024 || !d.token || d.job<=d.token
            || !nonzero || d.count<1 || d.count>TX_BURST_DEPTH)
            return Code::Invalid;
        if(d.job<=boot_.request_highwater) return Code::Stale;
        if(d.deadline_us<=now || d.deadline_us>admission_.lease_deadline_us
            || d.deadline_us-now>MAX_AUTONOMOUS_HOLD_US) return Code::Expired;
        descriptor_=d;begun_=true;boot_.request_highwater=d.job;
        return Code::Accepted;
    }

    Code stage(const BatchDescriptor& d,std::size_t index,const Packet& packet,
               std::uint64_t independent_estimate,std::uint64_t now) {
        if(!bound(d)) return Code::WrongBinding;
        const auto observed=clock(now);if(observed!=Code::Accepted) return observed;
        if(!begun_ || !exact(d)) return Code::Mismatch;
        if(index>=d.count || packet.size<1 || packet.size>TX_PACKET_BYTES
            || packet.kind!=PacketKind::Data || packet.deadline_us!=d.deadline_us
            || packet.request<=d.job || !independent_estimate
            || packet.estimated_airtime_us!=independent_estimate) return Code::Invalid;
        std::uint8_t digest[32];
        if(!hash_(packet.ciphertext,packet.size,digest) || std::memcmp(digest,packet.digest,32))
            return Code::Mismatch;
        if(present_[index]) return samePacket(staged_[index],packet) ? Code::Replayed : Code::Conflict;
        if(committed_ || released_all_ || revoked_) return Code::Fenced;
        if(now>=d.deadline_us) return Code::Expired;
        staged_[index]=packet;estimates_[index]=independent_estimate;present_[index]=true;
        return Code::Accepted;
    }

    Code commit(const BatchDescriptor& d,std::uint64_t request,std::uint64_t now) {
        if(!bound(d)) return Code::WrongBinding;
        // Replays report the original admission only: they never issue again.
        if(!begun_ || !exact(d)) return Code::Mismatch;
        if(committed_) return request==commit_request_ ? Code::Replayed : Code::Conflict;
        const auto observed=clock(now);if(observed!=Code::Accepted) return observed;
        if(revoked_) return Code::Fenced;
        if(now>=d.deadline_us) return Code::Expired;
        std::uint64_t previous=d.job;
        for(std::size_t n=0;n<d.count;n++) {
            if(!present_[n]) return Code::Missing;
            if(staged_[n].request<=previous) return Code::Invalid;
            previous=staged_[n].request;
        }
        if(request<=previous || request<=boot_.request_highwater) return Code::Stale;
        std::uint8_t digest[32];
        if(!batchManifest(d,staged_,hash_,digest) || std::memcmp(digest,d.manifest,32)) return Code::Mismatch;
        // Run the SAME queue validation on a private fixed-memory copy. The
        // original owner/boot is untouched. Single-loop ownership forbids any
        // intervening mutation between preflight and the live commit below.
        BootState trial=boot_;trial.queue_owned=false;
        {
            TxBurstQueue preflight(admission_,trial);
            for(std::size_t n=0;n<d.count;n++) {
                const auto result=preflight.enqueue(d.binding,staged_[n],estimates_[n],now);
                if(result!=Code::Accepted) return result;
            }
        }
        // All entries are validated; all native charges precede issue().
        for(std::size_t n=0;n<d.count;n++) {
            if(queue_.enqueue(d.binding,staged_[n],estimates_[n],now)!=Code::Accepted) {
                // Unreachable under the ownership contract. Preserve the full
                // validated reservation and fence rather than refund a prefix.
                boot_.counters=trial.counters;boot_.paid_floor=trial.paid_floor;
                boot_.request_highwater=request;queue_.cancel();revoked_=true;
                return Code::Fenced;
            }
        }
        boot_.request_highwater=request;commit_request_=request;committed_=true;
        return Code::Accepted;
    }

    Dispatch issue(std::uint64_t now,bool rx_pending,bool output_available) {
        const auto observed=clock(now);if(observed!=Code::Accepted) return {observed,nullptr};
        if(!committed_ || released_all_ || revoked_) return {Code::Missing,nullptr};
        if(now<pacing_.next_issue_us) return {Code::Busy,nullptr};
        return queue_.issue(now,rx_pending,output_available);
    }
    Code settle(const Evidence& evidence,std::uint64_t now) {
        const auto result=queue_.settle(evidence,now);
        if(result==Code::Accepted && evidence.state==State::RfDone)
            listen(now,queue_.queued() ? BATCH_RX_US : BATCH_REVERSE_US);
        if(queue_.fenced()) listen(now,BATCH_REVERSE_US);
        return result;
    }
    Code release(const BatchDescriptor& d,std::uint64_t request,const BatchRelease* entries,
                 std::size_t count,std::uint64_t now) {
        if(!bound(d)) return Code::WrongBinding;
        if(!committed_ || !exact(d) || !entries || count!=d.count) return Code::Mismatch;
        if(released_all_) {
            if(request!=release_request_) return Code::Conflict;
            for(std::size_t n=0;n<count;n++) if(!sameRelease(entries[n],released_[n])) return Code::Mismatch;
            return Code::Replayed;
        }
        // Even fenced outcomes may be retained/acknowledged after checked
        // containment. Clock regression, however, must not free evidence.
        const auto observed=clock(now);if(observed==Code::ClockRegressed) return observed;
        if(request<=boot_.request_highwater) return Code::Stale;
        if(queue_.containmentRequired()) return Code::Busy;
        for(std::size_t n=0;n<count;n++) {
            Evidence held;
            if(entries[n].request!=staged_[n].request || !queue_.evidence(entries[n].request,held)) return Code::Mismatch;
            if(held.state!=State::RfDone && held.state!=State::NotSent && held.state!=State::Unknown) return Code::NotTerminal;
            if(entries[n].state!=held.state || entries[n].event_us!=held.event_us
                || std::memcmp(entries[n].digest,held.digest,32)) return Code::Mismatch;
        }
        // Entire vector verified before freeing its first entry.
        for(std::size_t n=0;n<count;n++) {
            if(queue_.acknowledge(d.binding,entries[n].request,entries[n].digest,entries[n].state)!=Code::Accepted) {
                queue_.cancel();return Code::Fenced;
            }
            released_[n]=entries[n];
        }
        boot_.request_highwater=request;release_request_=request;released_all_=true;
        return Code::Accepted;
    }
    void cancel() { revoked_=true;queue_.cancel();listen(boot_.last_now_us,BATCH_REVERSE_US); }
    bool evidence(std::uint64_t request,Evidence& out) const { return queue_.evidence(request,out); }
    std::size_t retained() const { return queue_.retained(); }
    bool fenced() const { return queue_.fenced(); }
    bool containmentRequired() const { return queue_.containmentRequired(); }
    void containmentComplete(bool checked) { queue_.containmentComplete(checked); }
};
}} // namespace mtbench::experimental
