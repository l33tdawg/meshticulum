// Synthetic driver and clock, real SHA-256. No device or RF operations.
#include "experimental/tx_batch.hpp"
#include "mbedtls/sha256.h"
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>
#include <utility>

static unsigned allocations=0;
void* operator new(std::size_t n) { allocations++;if(auto* p=std::malloc(n)) return p;std::abort(); }
void* operator new[](std::size_t n) { return ::operator new(n); }
void operator delete(void* p) noexcept { std::free(p); }
void operator delete[](void* p) noexcept { std::free(p); }
using namespace mtbench::experimental;
constexpr std::uint64_t NOW=1000000, END=2500000;
static_assert(sizeof(TxBatchJob)<=8192,"fixed bounded batch memory");
bool hash(const std::uint8_t* p,std::size_t n,std::uint8_t* out) { return mbedtls_sha256_ret(p,n,out,0)==0; }
Admission admission() {
    Admission a;a.binding.boot[0]=1;a.binding.context_sha256[0]=2;
    a.lease_deadline_us=END;a.max_attempts=1024;a.max_airtime_us=30000000;return a;
}
BootState boot() { BootState b;b.boot[0]=1;return b; }
Packet packet(unsigned n) {
    Packet p;p.request=11+n;p.deadline_us=END;p.estimated_airtime_us=100000;p.size=255;
    for(unsigned i=0;i<255;i++) p.ciphertext[i]=std::uint8_t(i+n);
    assert(hash(p.ciphertext,p.size,p.digest));return p;
}
struct Fixture {
    Admission a=admission();BootState b=boot();BatchPacing pacing;
    BatchDescriptor d;Packet packets[4];
    Fixture() {
        d.binding=a.binding;d.token=1;d.job=10;d.deadline_us=END;d.count=4;
        for(unsigned n=0;n<4;n++) packets[n]=packet(n);
        assert(batchManifest(d,packets,hash,d.manifest));
    }
    void stage(TxBatchJob& q) {
        assert(q.begin(d,NOW)==Code::Accepted);
        for(unsigned n=0;n<4;n++) assert(q.stage(d,n,packets[n],100000,NOW)==Code::Accepted);
    }
    void commit(TxBatchJob& q) { stage(q);assert(q.commit(d,15,NOW)==Code::Accepted); }
};
Evidence evidence(const Packet& p,State state=State::RfDone) {
    Evidence e;e.binding=admission().binding;e.request=p.request;e.size=p.size;
    e.estimated_airtime_us=p.estimated_airtime_us;std::memcpy(e.digest,p.digest,32);e.state=state;
    if(state==State::RfDone) { e.irq_flags=1;e.driver_elapsed_us=100500; }return e;
}
void releaseEntries(TxBatchJob& q,BatchRelease (&entries)[4]) {
    for(unsigned n=0;n<4;n++) {
        Evidence e;assert(q.evidence(11+n,e));entries[n].request=e.request;entries[n].state=e.state;
        entries[n].event_us=e.event_us;std::memcpy(entries[n].digest,e.digest,32);
    }
}
void finish(TxBatchJob& q,const Fixture& f) {
    for(unsigned n=0;n<4;n++) {
        auto now=NOW+n*110000;
        assert(q.issue(now,false,true).code==Code::Accepted);
        assert(q.settle(evidence(f.packets[n]),now+100000)==Code::Accepted);
        assert(q.receiveStarted(f.packets[n].request,now+100000,true)==Code::Accepted);
    }
}
void run(const char* name) {
    Fixture f;TxBatchJob q(f.a,1,f.b,f.pacing,hash);
    const auto before=allocations;
    if(!std::strcmp(name,"manifest")) {
        for(auto v:f.d.manifest) std::printf("%02x",v);std::puts("");return;
    }
    if(!std::strcmp(name,"prepaid")) {
        f.stage(q);assert(f.b.counters.attempts==0 && q.retained()==0);
        assert(q.issue(NOW,false,true).code==Code::Missing);
        assert(q.commit(f.d,15,NOW)==Code::Accepted);
        assert(f.b.counters.attempts==4 && f.b.counters.reserved_airtime_us==400000 && q.retained()==4);
        assert(q.issue(NOW,false,true).code==Code::Accepted);
    } else if(!std::strcmp(name,"partial")) {
        assert(q.begin(f.d,NOW)==Code::Accepted);
        assert(q.stage(f.d,0,f.packets[0],100000,NOW)==Code::Accepted);
        assert(q.commit(f.d,15,NOW)==Code::Missing);
        assert(f.b.counters.attempts==0 && q.retained()==0 && q.issue(NOW,false,true).code==Code::Missing);
    } else if(!std::strcmp(name,"bad_stage")) {
        assert(q.begin(f.d,NOW)==Code::Accepted);
        auto p=f.packets[0];p.ciphertext[0]^=1;
        assert(q.stage(f.d,0,p,100000,NOW)==Code::Mismatch);
        p=f.packets[0];p.size=256;assert(q.stage(f.d,0,p,100000,NOW)==Code::Invalid);
        p=f.packets[0];p.kind=PacketKind::Ack;assert(q.stage(f.d,0,p,100000,NOW)==Code::Invalid);
        assert(q.stage(f.d,0,f.packets[0],99999,NOW)==Code::Invalid);
        assert(q.stage(f.d,4,f.packets[0],100000,NOW)==Code::Invalid);
        assert(f.b.counters.attempts==0 && q.retained()==0);
    } else if(!std::strcmp(name,"wrong_binding")) {
        auto wrong=f.d;wrong.binding.boot[0]++;
        assert(q.begin(wrong,NOW)==Code::WrongBinding);
        f.stage(q);assert(q.commit(wrong,15,NOW)==Code::WrongBinding);
        wrong=f.d;wrong.token++;
        assert(q.commit(wrong,15,NOW)==Code::WrongBinding);
        wrong=f.d;wrong.binding.context_sha256[0]++;
        assert(q.stage(wrong,0,f.packets[0],100000,NOW)==Code::WrongBinding);
        assert(f.b.counters.attempts==0);
    } else if(!std::strcmp(name,"manifest_mismatch")) {
        f.d.manifest[0]^=1;f.stage(q);
        assert(q.commit(f.d,15,NOW)==Code::Mismatch && f.b.counters.attempts==0 && q.retained()==0);
    } else if(!std::strcmp(name,"order")) {
        std::swap(f.packets[0],f.packets[1]);assert(batchManifest(f.d,f.packets,hash,f.d.manifest));f.stage(q);
        assert(q.commit(f.d,15,NOW)==Code::Invalid && f.b.counters.attempts==0);
    } else if(!std::strcmp(name,"budget")) {
        f.b.counters.attempts=1022;f.stage(q);
        assert(q.commit(f.d,15,NOW)==Code::Budget && f.b.counters.attempts==1022 && q.retained()==0);
    } else if(!std::strcmp(name,"airtime_budget")) {
        f.b.counters.reserved_airtime_us=f.a.max_airtime_us-399999;f.stage(q);
        assert(q.commit(f.d,15,NOW)==Code::Budget && f.b.counters.reserved_airtime_us==f.a.max_airtime_us-399999);
    } else if(!std::strcmp(name,"overflow")) {
        for(auto& p:f.packets) p.estimated_airtime_us=UINT64_MAX;
        assert(batchManifest(f.d,f.packets,hash,f.d.manifest));assert(q.begin(f.d,NOW)==Code::Accepted);
        for(unsigned n=0;n<4;n++) assert(q.stage(f.d,n,f.packets[n],UINT64_MAX,NOW)==Code::Accepted);
        assert(q.commit(f.d,15,NOW)==Code::Invalid && f.b.counters.attempts==0);
    } else if(!std::strcmp(name,"replay")) {
        f.commit(q);assert(q.commit(f.d,15,NOW)==Code::Replayed);
        assert(q.commit(f.d,16,NOW)==Code::Conflict);
        assert(q.stage(f.d,0,f.packets[0],100000,NOW)==Code::Replayed);
        auto changed=f.d;changed.manifest[0]^=1;assert(q.commit(changed,15,NOW)==Code::Mismatch);
        assert(f.b.counters.attempts==4 && f.b.request_highwater==15);
        assert(q.issue(NOW,false,true).code==Code::Accepted);
        assert(q.commit(f.d,15,NOW)==Code::Replayed && q.issue(NOW,false,true).code==Code::Busy);
    } else if(!std::strcmp(name,"stale")) {
        f.b.request_highwater=10;assert(q.begin(f.d,NOW)==Code::Stale);
    } else if(!std::strcmp(name,"deadline")) {
        f.stage(q);assert(q.commit(f.d,15,END-799999)==Code::Expired);
        assert(f.b.counters.attempts==0 && q.retained()==0);
    } else if(!std::strcmp(name,"hold")) {
        auto d=f.d;d.deadline_us=NOW+2000001;assert(q.begin(d,NOW)==Code::Expired);
    } else if(!std::strcmp(name,"revoke_staged")) {
        f.stage(q);q.cancel();assert(q.commit(f.d,15,NOW)==Code::Fenced && f.b.counters.attempts==0);
    } else if(!std::strcmp(name,"revoke_committed")) {
        f.commit(q);q.cancel();assert(q.issue(NOW,false,true).code==Code::Fenced);
        BatchRelease entries[4];releaseEntries(q,entries);
        for(const auto& e:entries) assert(e.state==State::NotSent);
        assert(q.release(f.d,16,entries,4,NOW)==Code::Accepted && f.b.counters.attempts==4);
    } else if(!std::strcmp(name,"unknown")) {
        f.commit(q);assert(q.issue(NOW,false,true).code==Code::Accepted);q.cancel();
        BatchRelease entries[4];releaseEntries(q,entries);assert(entries[0].state==State::Unknown);
        assert(q.release(f.d,16,entries,4,NOW)==Code::Busy && q.retained()==4);
        q.containmentComplete(false);assert(q.containmentRequired());q.containmentComplete(true);
        assert(q.release(f.d,16,entries,4,NOW)==Code::Accepted);
        assert(q.fenced() && f.b.counters.attempts==4 && f.b.counters.reserved_airtime_us==400000);
    } else if(!std::strcmp(name,"expiry")) {
        f.commit(q);assert(q.issue(END,false,true).code==Code::Missing);
        BatchRelease entries[4];releaseEntries(q,entries);
        for(const auto& e:entries) assert(e.state==State::NotSent);
        assert(f.b.counters.attempts==4);
    } else if(!std::strcmp(name,"pacing")) {
        f.commit(q);assert(q.issue(NOW,true,true).code==Code::RxPending);
        assert(q.issue(NOW,false,false).code==Code::OutputBlocked);
        assert(q.issue(NOW,false,true).code==Code::Accepted);
        assert(q.settle(evidence(f.packets[0]),NOW+100000)==Code::Accepted);
        assert(q.receiveStarted(11,NOW+100000,true)==Code::Accepted);
        assert(q.issue(NOW+104999,false,true).code==Code::Busy);
        assert(q.issue(NOW+105000,false,true).code==Code::Accepted);
        assert(q.settle(evidence(f.packets[1]),NOW+205000)==Code::Accepted);
        assert(q.receiveStarted(12,NOW+205000,true)==Code::Accepted);
        assert(q.issue(NOW+210000,false,true).code==Code::Accepted);
        assert(q.settle(evidence(f.packets[2]),NOW+310000)==Code::Accepted);
        assert(q.receiveStarted(13,NOW+310000,true)==Code::Accepted);
        assert(q.issue(NOW+315000,false,true).code==Code::Accepted);
        assert(q.settle(evidence(f.packets[3]),NOW+415000)==Code::Accepted);
        assert(q.receiveStarted(14,NOW+415000,true)==Code::Accepted);
        assert(f.pacing.next_issue_us==NOW+515000);
    } else if(!std::strcmp(name,"release_atomic")) {
        f.commit(q);finish(q,f);BatchRelease entries[4];releaseEntries(q,entries);
        auto wrong=f.d;wrong.binding.boot[0]++;
        assert(q.release(wrong,16,entries,4,NOW+430000)==Code::WrongBinding && q.retained()==4);
        assert(q.release(f.d,15,entries,4,NOW+430000)==Code::Stale && q.retained()==4);
        assert(q.release(f.d,16,entries,3,NOW+430000)==Code::Mismatch && q.retained()==4);
        entries[3].digest[0]^=1;
        assert(q.release(f.d,16,entries,4,NOW+430000)==Code::Mismatch && q.retained()==4);
        entries[3].digest[0]^=1;entries[2].event_us++;
        assert(q.release(f.d,16,entries,4,NOW+430000)==Code::Mismatch && q.retained()==4);
        entries[2].event_us--;std::swap(entries[0],entries[1]);
        assert(q.release(f.d,16,entries,4,NOW+430000)==Code::Mismatch && q.retained()==4);
    } else if(!std::strcmp(name,"release_replay")) {
        f.commit(q);finish(q,f);BatchRelease entries[4];releaseEntries(q,entries);
        assert(q.release(f.d,16,entries,4,NOW+430000)==Code::Accepted && q.retained()==0);
        assert(q.release(f.d,16,entries,4,END+1)==Code::Replayed);
        assert(q.commit(f.d,15,END+1)==Code::Replayed);
        assert(q.release(f.d,17,entries,4,END+1)==Code::Conflict);
        entries[3].event_us++;assert(q.release(f.d,16,entries,4,END+1)==Code::Mismatch);
        assert(f.b.counters.attempts==4 && q.issue(END+1,false,true).code==Code::Missing);
    } else if(!std::strcmp(name,"release_pending")) {
        f.commit(q);BatchRelease entries[4];releaseEntries(q,entries);
        assert(q.release(f.d,16,entries,4,NOW)==Code::NotTerminal && q.retained()==4);
    } else if(!std::strcmp(name,"clock")) {
        f.commit(q);assert(q.issue(NOW-1,false,true).code==Code::ClockRegressed && q.fenced());
        assert(f.b.counters.attempts==4);
    } else if(!std::strcmp(name,"debt")) {
        f.commit(q);f.b.counters.attempts--;
        assert(q.issue(NOW,false,true).code==Code::ClockRegressed && q.fenced());
    } else if(!std::strcmp(name,"stage_expiry")) {
        assert(q.begin(f.d,NOW)==Code::Accepted);
        assert(q.stage(f.d,0,f.packets[0],100000,END)==Code::Expired);
        assert(q.commit(f.d,15,END)==Code::Expired && f.b.counters.attempts==0);
    } else if(!std::strcmp(name,"active_mutation")) {
        f.commit(q);assert(q.issue(NOW,false,true).code==Code::Accepted);
        auto changed=f.d;changed.job=20;
        assert(q.begin(changed,NOW)==Code::Conflict);
        assert(q.stage(changed,0,f.packets[0],100000,NOW)==Code::Mismatch);
        auto p=f.packets[0];p.request=25;
        assert(q.stage(f.d,0,p,100000,NOW)==Code::Conflict);
        assert(q.commit(f.d,15,NOW)==Code::Replayed && f.b.counters.attempts==4);
    } else if(!std::strcmp(name,"count")) {
        auto d=f.d;d.count=0;assert(q.begin(d,NOW)==Code::Invalid);
        d.count=5;assert(q.begin(d,NOW)==Code::Invalid && f.b.counters.attempts==0);
    } else if(!std::strcmp(name,"one_tail")) {
        f.d.count=1;assert(batchManifest(f.d,f.packets,hash,f.d.manifest));
        assert(q.begin(f.d,NOW)==Code::Accepted);
        assert(q.stage(f.d,0,f.packets[0],100000,NOW)==Code::Accepted);
        assert(q.commit(f.d,12,NOW)==Code::Accepted);
        assert(q.issue(NOW,false,true).code==Code::Accepted);
        assert(q.settle(evidence(f.packets[0]),NOW+100000)==Code::Accepted);
        assert(q.receiveStarted(11,NOW+100000,true)==Code::Accepted);
        assert(f.pacing.next_issue_us==NOW+200000 && f.b.counters.attempts==1);
    } else if(!std::strcmp(name,"poll_unknown")) {
        f.commit(q);assert(q.issue(NOW,false,true).code==Code::Accepted);
        assert(q.issue(END,false,true).code==Code::Fenced && q.containmentRequired());
        q.containmentComplete(true);assert(!q.containmentRequired() && q.fenced());
        BatchRelease entries[4];releaseEntries(q,entries);
        assert(q.release(f.d,16,entries,4,END)==Code::Accepted);
        assert(q.issue(END+100000,false,true).code==Code::Fenced);
        assert(f.b.fenced && f.b.counters.attempts==4);
    } else if(!std::strcmp(name,"receive_delayed")) {
        f.commit(q);assert(q.issue(NOW,false,true).code==Code::Accepted);
        assert(q.settle(evidence(f.packets[0]),NOW+100000)==Code::Accepted);
        assert(f.pacing.receive_request==11 && f.pacing.receive_delay_us==5000);
        // Both the old 5 ms and 100 ms RF_DONE-based floors have elapsed.
        assert(q.issue(NOW+250000,false,true).code==Code::Busy);
        assert(q.receiveStarted(11,NOW+250000,true)==Code::Accepted);
        assert(q.issue(NOW+254999,false,true).code==Code::Busy);
        assert(q.issue(NOW+255000,false,true).code==Code::Accepted);
        assert(f.b.counters.attempts==4 && f.b.counters.reserved_airtime_us==400000);
    } else if(!std::strcmp(name,"receive_stale")) {
        f.commit(q);assert(q.issue(NOW,false,true).code==Code::Accepted);
        assert(q.settle(evidence(f.packets[0]),NOW+100000)==Code::Accepted);
        assert(q.receiveStarted(12,NOW+100000,true)==Code::Mismatch);
        assert(q.issue(NOW+110000,false,true).code==Code::Busy);
        assert(q.receiveStarted(11,NOW+110000,true)==Code::Accepted);
        const auto floor=f.pacing.next_issue_us;
        assert(q.receiveStarted(11,NOW+111000,true)==Code::Missing);
        assert(f.pacing.next_issue_us==floor);
        assert(q.issue(NOW+115000,false,true).code==Code::Accepted);
        assert(q.settle(evidence(f.packets[1]),NOW+215000)==Code::Accepted);
        assert(q.receiveStarted(11,NOW+215000,true)==Code::Mismatch);
        assert(q.issue(NOW+225000,false,true).code==Code::Busy);
        assert(q.receiveStarted(12,NOW+225000,true)==Code::Accepted);
        assert(q.issue(NOW+229999,false,true).code==Code::Busy);
    } else if(!std::strcmp(name,"receive_failed")) {
        f.commit(q);assert(q.issue(NOW,false,true).code==Code::Accepted);
        assert(q.settle(evidence(f.packets[0]),NOW+100000)==Code::Accepted);
        assert(q.receiveStarted(11,NOW+110000,false)==Code::Fenced);
        assert(q.receiveStarted(11,NOW+120000,true)==Code::Fenced);
        assert(q.issue(NOW+500000,false,true).code==Code::Fenced);
        BatchRelease entries[4];releaseEntries(q,entries);
        assert(entries[0].state==State::RfDone);
        for(unsigned n=1;n<4;n++) assert(entries[n].state==State::NotSent);
        assert(q.release(f.d,16,entries,4,NOW+500000)==Code::Accepted);
        assert(f.pacing.receive_request==11 && f.b.counters.attempts==4);
    } else if(!std::strcmp(name,"receive_clock")) {
        f.commit(q);assert(q.issue(NOW,false,true).code==Code::Accepted);
        assert(q.settle(evidence(f.packets[0]),NOW+100000)==Code::Accepted);
        assert(q.receiveStarted(11,NOW+99999,true)==Code::ClockRegressed);
        assert(q.receiveStarted(11,NOW+110000,true)==Code::Fenced);
        assert(q.issue(NOW+500000,false,true).code==Code::Fenced);
        assert(f.pacing.receive_request==11 && f.b.counters.attempts==4);
    } else if(!std::strcmp(name,"receive_expiry")) {
        f.commit(q);assert(q.issue(NOW,false,true).code==Code::Accepted);
        assert(q.settle(evidence(f.packets[0]),NOW+100000)==Code::Accepted);
        assert(q.issue(END,false,true).code==Code::Busy);
        assert(q.receiveStarted(11,END,true)==Code::Accepted);
        assert(q.issue(END+99999,false,true).code==Code::Busy);
        assert(q.issue(END+100000,false,true).code==Code::Missing);
        BatchRelease entries[4];releaseEntries(q,entries);
        assert(entries[0].state==State::RfDone);
        for(unsigned n=1;n<4;n++) assert(entries[n].state==State::NotSent);
        assert(f.b.counters.attempts==4 && f.b.counters.reserved_airtime_us==400000);
    } else if(!std::strcmp(name,"receive_expiry_after_rearm")) {
        f.commit(q);assert(q.issue(END-200000,false,true).code==Code::Accepted);
        auto done=evidence(f.packets[0]);done.driver_elapsed_us=110000;
        assert(q.settle(done,END-90000)==Code::Accepted);
        assert(q.receiveStarted(11,END-80000,true)==Code::Accepted);
        assert(f.pacing.next_issue_us==END-75000);
        assert(q.issue(END,false,true).code==Code::Busy);
        assert(f.pacing.next_issue_us==END+20000 && f.pacing.receive_delay_us==100000);
        assert(q.issue(END+19999,false,true).code==Code::Busy);
        assert(q.issue(END+20000,false,true).code==Code::Missing);
        assert(f.b.counters.attempts==4 && f.b.counters.reserved_airtime_us==400000);
    } else { assert(false); }
    assert(allocations==before);
}
int main(int argc,char** argv) {
    assert(argc==2);
    if(!std::strcmp(argv[1],"new_owner")) {
        Fixture f;
        {
            TxBatchJob q(f.a,1,f.b,f.pacing,hash);f.commit(q);finish(q,f);
            BatchRelease entries[4];releaseEntries(q,entries);
            assert(q.release(f.d,16,entries,4,NOW+430000)==Code::Accepted);
        }
        const auto old=f.d;
        TxBatchJob next(f.a,1,f.b,f.pacing,hash);
        assert(next.begin(old,NOW+430000)==Code::Stale);
        f.d.job=20;for(unsigned n=0;n<4;n++) f.packets[n].request=21+n;
        assert(batchManifest(f.d,f.packets,hash,f.d.manifest));
        assert(next.begin(f.d,NOW+430000)==Code::Accepted);
        for(unsigned n=0;n<4;n++) assert(next.stage(f.d,n,f.packets[n],100000,NOW+430000)==Code::Accepted);
        assert(next.commit(old,15,NOW+430000)==Code::Mismatch && f.b.counters.attempts==4);
        assert(next.commit(f.d,25,NOW+430000)==Code::Accepted && f.b.counters.attempts==8);
        assert(next.issue(NOW+529999,false,true).code==Code::Busy);
        assert(next.issue(NOW+530000,false,true).code==Code::Accepted);
    } else if(!std::strcmp(argv[1],"poll_new_owner")) {
        Fixture f;
        {
            TxBatchJob q(f.a,1,f.b,f.pacing,hash);f.commit(q);
            assert(q.issue(NOW,false,true).code==Code::Accepted);
            assert(q.issue(END,false,true).code==Code::Fenced);q.containmentComplete(true);
            BatchRelease entries[4];releaseEntries(q,entries);
            assert(q.release(f.d,16,entries,4,END)==Code::Accepted);
        }
        auto a=f.a;a.lease_deadline_us=END+2000000;
        TxBatchJob next(a,1,f.b,f.pacing,hash);
        f.d.job=20;f.d.deadline_us=END+1500000;
        assert(next.begin(f.d,END)==Code::Fenced);
        assert(next.issue(END+100000,false,true).code==Code::Fenced);
        assert(f.b.counters.attempts==4);
    } else if(!std::strcmp(argv[1],"receive_tail_new_owner")) {
        Fixture f;f.d.count=1;assert(batchManifest(f.d,f.packets,hash,f.d.manifest));
        {
            TxBatchJob q(f.a,1,f.b,f.pacing,hash);
            assert(q.begin(f.d,NOW)==Code::Accepted);
            assert(q.stage(f.d,0,f.packets[0],100000,NOW)==Code::Accepted);
            assert(q.commit(f.d,12,NOW)==Code::Accepted);
            assert(q.issue(NOW,false,true).code==Code::Accepted);
            assert(q.settle(evidence(f.packets[0]),NOW+100000)==Code::Accepted);
            Evidence held;assert(q.evidence(11,held));BatchRelease entry;
            entry.request=11;entry.event_us=held.event_us;entry.state=held.state;
            std::memcpy(entry.digest,held.digest,32);
            assert(q.release(f.d,13,&entry,1,NOW+100000)==Code::Accepted);
            assert(q.commit(f.d,12,NOW+100001)==Code::Replayed);
            assert(q.release(f.d,13,&entry,1,NOW+100001)==Code::Replayed);
            assert(f.pacing.receive_request==11 && f.pacing.receive_delay_us==100000);
        }
        TxBatchJob next(f.a,1,f.b,f.pacing,hash);
        f.d.job=20;f.packets[0].request=21;assert(batchManifest(f.d,f.packets,hash,f.d.manifest));
        assert(next.begin(f.d,NOW+200000)==Code::Accepted);
        assert(next.stage(f.d,0,f.packets[0],100000,NOW+200000)==Code::Accepted);
        assert(next.commit(f.d,22,NOW+200000)==Code::Accepted);
        assert(next.issue(NOW+300000,false,true).code==Code::Busy);
        assert(next.receiveStarted(21,NOW+300000,true)==Code::Mismatch);
        assert(next.receiveStarted(11,NOW+300000,true)==Code::Accepted);
        assert(next.issue(NOW+399999,false,true).code==Code::Busy);
        assert(next.issue(NOW+400000,false,true).code==Code::Accepted);
        assert(f.b.counters.attempts==2 && f.b.counters.reserved_airtime_us==200000);
    } else if(!std::strcmp(argv[1],"receive_failed_new_owner")) {
        Fixture f;
        {
            TxBatchJob q(f.a,1,f.b,f.pacing,hash);f.commit(q);
            assert(q.issue(NOW,false,true).code==Code::Accepted);
            assert(q.settle(evidence(f.packets[0]),NOW+100000)==Code::Accepted);
            assert(q.receiveStarted(11,NOW+100000,false)==Code::Fenced);
            BatchRelease entries[4];releaseEntries(q,entries);
            assert(q.release(f.d,16,entries,4,NOW+100000)==Code::Accepted);
        }
        TxBatchJob next(f.a,1,f.b,f.pacing,hash);
        assert(next.receiveStarted(11,NOW+300000,true)==Code::Fenced);
        assert(next.issue(NOW+500000,false,true).code==Code::Fenced);
        assert(f.b.counters.attempts==4 && f.pacing.receive_request==11);
    } else if(!std::strcmp(argv[1],"native_cap")) {
        Fixture f;f.a.max_attempts=1025;
        TxBatchJob q(f.a,1,f.b,f.pacing,hash);
        assert(q.begin(f.d,NOW)==Code::Invalid && f.b.counters.attempts==0);
        assert(q.issue(NOW,false,true).code==Code::Missing);
    } else if(!std::strcmp(argv[1],"boot_changed")) {
        Fixture f;f.b.boot[0]++;
        TxBatchJob q(f.a,1,f.b,f.pacing,hash);
        assert(q.begin(f.d,NOW)==Code::Fenced && f.b.counters.attempts==0);
    } else if(!std::strcmp(argv[1],"pacing_overflow")) {
        Fixture f;const auto now=UINT64_MAX-500000;
        f.a.lease_deadline_us=UINT64_MAX;f.d.deadline_us=UINT64_MAX;f.d.count=1;
        f.packets[0].deadline_us=UINT64_MAX;assert(batchManifest(f.d,f.packets,hash,f.d.manifest));
        TxBatchJob q(f.a,1,f.b,f.pacing,hash);
        assert(q.begin(f.d,now)==Code::Accepted);
        assert(q.stage(f.d,0,f.packets[0],100000,now)==Code::Accepted);
        assert(q.commit(f.d,12,now)==Code::Accepted && q.issue(now,false,true).code==Code::Accepted);
        assert(q.settle(evidence(f.packets[0]),UINT64_MAX-50000)==Code::Accepted);
        assert(!q.fenced() && f.pacing.receive_request==11);
        assert(q.receiveStarted(11,UINT64_MAX-50000,true)==Code::Fenced);
        assert(q.fenced() && f.pacing.next_issue_us==UINT64_MAX && f.b.counters.attempts==1);
    } else run(argv[1]);
}
