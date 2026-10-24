// Actual source state machine with synthetic time/driver evidence; never RF.
#include "experimental/tx_burst.hpp"
#include <cassert>
#include <cstdlib>
#include <cstdio>
#include <new>
#include <cstring>
#include <limits>

static std::size_t allocations=0;
void* operator new(std::size_t size) { allocations++; if(void* p=std::malloc(size)) return p; std::abort(); }
void* operator new[](std::size_t size) { return ::operator new(size); }
void operator delete(void* p) noexcept { std::free(p); }
void operator delete[](void* p) noexcept { std::free(p); }

using namespace mtbench::experimental;
constexpr std::uint64_t NOW=1000000, END=2500000;
static_assert(sizeof(TxBurstQueue)<=4096,"queue must retain fixed bounded memory");

Admission admission() {
    Admission value; value.binding.boot[0]=1; value.binding.context_sha256[0]=2;
    value.lease_deadline_us=NOW+10000000; value.max_attempts=1024; value.max_airtime_us=30000000;
    return value;
}
BootState boot() { BootState value; value.boot[0]=1; return value; }
Packet packet(std::uint64_t request=1) {
    Packet value; value.request=request; value.deadline_us=END; value.estimated_airtime_us=100000;
    // SHA-256(bytes(range(255))); hashing itself belongs to the trusted adapter.
    constexpr std::uint8_t digest[]={
        0x3f,0x85,0x91,0x11,0x2c,0x6b,0xbe,0x5c,0x96,0x39,0x65,0x95,0x4e,0x29,0x31,0x08,
        0xb7,0x20,0x8e,0xd2,0xaf,0x89,0x3e,0x50,0x0d,0x85,0x93,0x68,0xc6,0x54,0xea,0xbe};
    value.size=255; std::memcpy(value.digest,digest,32);
    for(std::size_t n=0;n<value.size;n++) value.ciphertext[n]=std::uint8_t(n);
    return value;
}
Evidence driver(const Packet& p,State outcome=State::RfDone) {
    Evidence value; value.binding=admission().binding; value.request=p.request;
    value.size=p.size; value.estimated_airtime_us=p.estimated_airtime_us;
    std::memcpy(value.digest,p.digest,32); value.state=outcome;
    if(outcome==State::RfDone) { value.irq_flags=1; value.driver_elapsed_us=100500; }
    return value;
}
State state(TxBurstQueue& q,std::uint64_t request=1) { Evidence value; assert(q.evidence(request,value)); return value.state; }
void enqueue(TxBurstQueue& q,const Packet& p) { assert(q.enqueue(admission().binding,p,p.estimated_airtime_us,NOW)==Code::Accepted); }
void issue(TxBurstQueue& q,const Packet& p,std::uint64_t now=NOW) {
    auto result=q.issue(now,false,true); assert(result.code==Code::Accepted && result.packet);
    assert(result.packet->request==p.request && result.packet->size==p.size);
    assert(std::memcmp(result.packet->ciphertext,p.ciphertext,p.size)==0);
}
void done(TxBurstQueue& q,const Packet& p,std::uint64_t now=NOW) { assert(q.settle(driver(p),now)==Code::Accepted); }
void ack(TxBurstQueue& q,const Packet& p,State outcome=State::RfDone) {
    assert(q.acknowledge(admission().binding,p.request,p.digest,outcome)==Code::Accepted);
}

void prepaid() {
    auto b=boot(); auto a=admission(); TxBurstQueue q(a,b);
    const auto before=allocations;
    for(std::uint64_t n=1;n<=4;n++) enqueue(q,packet(n));
    assert(b.counters.attempts==4 && b.counters.reserved_airtime_us==400000);
    assert(q.enqueue(a.binding,packet(5),100000,NOW)==Code::Full);
    assert(allocations==before && sizeof(q)<=4096);
    for(std::uint64_t n=1;n<=4;n++) { issue(q,packet(n)); done(q,packet(n)); }
    assert(!q.fenced() && !q.containmentRequired());
    // Merely retaining an RF result cannot free capacity or admit another burst.
    assert(q.enqueue(a.binding,packet(5),100000,NOW)==Code::Full);
    ack(q,packet(1)); assert(q.enqueue(a.binding,packet(5),100000,NOW)==Code::Full);
    for(std::uint64_t n=2;n<=4;n++) ack(q,packet(n));
    enqueue(q,packet(5)); assert(b.counters.attempts==5);
}
void replay() {
    auto b=boot(); auto a=admission(); TxBurstQueue q(a,b); auto p=packet(); enqueue(q,p);
    assert(q.enqueue(a.binding,p,100000,NOW)==Code::Replayed);
    auto changed=p; changed.ciphertext[0]^=1;
    assert(q.enqueue(a.binding,changed,100000,NOW)==Code::Conflict);
    changed=p; changed.digest[2]=42; assert(q.enqueue(a.binding,changed,100000,NOW)==Code::Conflict);
    changed=p; changed.deadline_us++; assert(q.enqueue(a.binding,changed,100000,NOW)==Code::Conflict);
    assert(b.counters.attempts==1 && b.counters.reserved_airtime_us==100000);
    issue(q,p); assert(q.enqueue(a.binding,p,100000,NOW)==Code::Replayed);
    assert(q.issue(NOW,false,true).code==Code::Busy);
    assert(q.acknowledge(a.binding,p.request,p.digest,State::RfDone)==Code::NotTerminal);
    done(q,p); assert(q.settle(driver(p),NOW)==Code::Replayed);
    changed=p;changed.digest[0]^=1;
    assert(q.acknowledge(a.binding,p.request,changed.digest,State::RfDone)==Code::Mismatch);
    assert(q.acknowledge(a.binding,p.request,p.digest,State::Unknown)==Code::Mismatch);
    ack(q,p); assert(q.enqueue(a.binding,p,100000,NOW)==Code::Stale);
    assert(b.request_highwater==1 && b.counters.attempts==1);
}
void invalid() {
    auto b=boot(); auto a=admission(); TxBurstQueue q(a,b); auto p=packet();
    auto binding=a.binding;binding.boot[0]++;assert(q.enqueue(binding,p,100000,NOW)==Code::WrongBinding);
    binding=a.binding;binding.context_sha256[0]++;assert(q.enqueue(binding,p,100000,NOW)==Code::WrongBinding);
    auto changed=p;changed.kind=PacketKind::Ack;assert(q.enqueue(a.binding,changed,100000,NOW)==Code::Invalid);
    changed=p;changed.kind=PacketKind::Control;assert(q.enqueue(a.binding,changed,100000,NOW)==Code::Invalid);
    assert(q.enqueue(a.binding,p,99999,NOW)==Code::Invalid);
    changed=p;changed.size=0;assert(q.enqueue(a.binding,changed,100000,NOW)==Code::Invalid);
    changed=p;changed.size=256;assert(q.enqueue(a.binding,changed,100000,NOW)==Code::Invalid);
    changed=p;std::memset(changed.digest,0,32);assert(q.enqueue(a.binding,changed,100000,NOW)==Code::Invalid);
    changed=p;changed.request=0;assert(q.enqueue(a.binding,changed,100000,NOW)==Code::Invalid);
    changed=p;changed.deadline_us=NOW;assert(q.enqueue(a.binding,changed,100000,NOW)==Code::Expired);
    changed=p;changed.deadline_us=NOW+MAX_AUTONOMOUS_HOLD_US+1;assert(q.enqueue(a.binding,changed,100000,NOW)==Code::Expired);
    changed=p;changed.estimated_airtime_us=UINT64_MAX;assert(q.enqueue(a.binding,changed,UINT64_MAX,NOW)==Code::Invalid);
    assert(b.counters.attempts==0 && b.counters.reserved_airtime_us==0);
    enqueue(q,p);changed=packet(2);changed.deadline_us++;assert(q.enqueue(a.binding,changed,100000,NOW)==Code::Conflict);
}
void budget() {
    auto a=admission(); auto b=boot();b.counters.attempts=a.max_attempts-1;
    TxBurstQueue q(a,b);enqueue(q,packet());
    assert(q.enqueue(a.binding,packet(2),100000,NOW)==Code::Budget);
    assert(b.counters.attempts==a.max_attempts);
    auto c=boot();c.counters.reserved_airtime_us=a.max_airtime_us-100000;TxBurstQueue r(a,c);enqueue(r,packet());
    assert(r.enqueue(a.binding,packet(2),100000,NOW)==Code::Budget);
    assert(c.counters.reserved_airtime_us==a.max_airtime_us);
}
void backpressure() {
    auto b=boot();TxBurstQueue q(admission(),b);auto p=packet();enqueue(q,p);
    assert(q.issue(NOW,true,true).code==Code::RxPending && state(q)==State::Queued);
    assert(q.issue(NOW,false,false).code==Code::OutputBlocked && state(q)==State::Queued);
    issue(q,p);done(q,p);assert(state(q)==State::RfDone);
    // Backpressure and repeated polling retain the exact completed evidence.
    for(int n=0;n<100;n++) { q.poll(NOW+1);assert(state(q)==State::RfDone); }
    assert(b.counters.attempts==1);ack(q,p);
}
void cancel_queued() {
    auto b=boot();TxBurstQueue q(admission(),b);enqueue(q,packet());q.cancel();
    assert(q.fenced() && !q.containmentRequired() && state(q)==State::NotSent);
    ack(q,packet(),State::NotSent);assert(b.counters.attempts==1 && b.counters.reserved_airtime_us==100000);
    assert(q.enqueue(admission().binding,packet(2),100000,NOW)==Code::Fenced);
}
void cancel_issued(bool started) {
    auto b=boot();TxBurstQueue q(admission(),b);auto p=packet();enqueue(q,p);enqueue(q,packet(2));issue(q,p);
    if(started) assert(q.settle(driver(p,State::Started),NOW)==Code::Accepted);
    q.cancel();assert(q.fenced() && q.containmentRequired());
    assert(state(q)==State::Unknown && state(q,2)==State::NotSent);
    assert(q.acknowledge(admission().binding,1,p.digest,State::Unknown)==Code::Busy);
    q.containmentComplete(false);assert(q.containmentRequired());
    assert(q.issue(NOW,false,true).code==Code::Fenced);
    q.containmentComplete(true);assert(!q.containmentRequired() && q.fenced());
    assert(q.settle(driver(p),NOW)==Code::Conflict && state(q)==State::Unknown);
    ack(q,p,State::Unknown);ack(q,packet(2),State::NotSent);
    assert(q.issue(NOW,false,true).code==Code::Fenced && b.counters.attempts==2);
}
void expiry(bool issued) {
    auto b=boot();TxBurstQueue q(admission(),b);auto p=packet();enqueue(q,p);enqueue(q,packet(2));
    if(issued) issue(q,p);
    q.poll(END);assert(state(q)==(issued ? State::Unknown : State::NotSent));
    assert(state(q,2)==State::NotSent && q.containmentRequired()==issued && q.fenced()==issued);
    assert(b.counters.attempts==2 && b.counters.reserved_airtime_us==200000);
    if(issued) assert(q.settle(driver(p),END+1)==Code::Conflict && state(q)==State::Unknown);
}
void late_result() {
    auto b=boot();TxBurstQueue q(admission(),b);auto p=packet();enqueue(q,p);enqueue(q,packet(2));issue(q,p);
    done(q,p,END+1);assert(state(q)==State::RfDone && state(q,2)==State::NotSent);
    assert(!q.fenced() && !q.containmentRequired());
    assert(q.issue(END+1,false,true).packet==nullptr && b.counters.attempts==2);
}
void bad_result(const char* fault) {
    auto b=boot();TxBurstQueue q(admission(),b);auto p=packet();enqueue(q,p);enqueue(q,packet(2));issue(q,p);
    auto e=driver(p);
    if(!std::strcmp(fault,"bad_digest")) e.digest[0]++;
    else if(!std::strcmp(fault,"bad_flags")) e.irq_flags=0x201;
    else if(!std::strcmp(fault,"bad_error")) e.driver_error=-1;
    else if(!std::strcmp(fault,"bad_estimate")) e.estimated_airtime_us++;
    else if(!std::strcmp(fault,"bad_size")) e.size--;
    else if(!std::strcmp(fault,"unknown")) { e.state=State::Unknown;e.irq_flags=0x200;e.driver_error=-1; }
    else assert(false);
    const auto code=q.settle(e,NOW);assert(code==Code::Accepted || code==Code::Mismatch);
    assert(state(q)==State::Unknown && state(q,2)==State::NotSent && q.fenced() && q.containmentRequired());
    assert(b.counters.attempts==2 && b.counters.reserved_airtime_us==200000);
}
void regression(bool debt) {
    auto b=boot();TxBurstQueue q(admission(),b);enqueue(q,packet());issue(q,packet());
    if(debt) b.counters.reserved_airtime_us--;
    assert(q.poll(debt ? NOW : NOW-1)==Code::ClockRegressed);
    assert(q.fenced() && q.containmentRequired() && state(q)==State::Unknown);
}
void reconstruction(bool retained) {
    auto b=boot();auto a=admission();
    { TxBurstQueue q(a,b);enqueue(q,packet());issue(q,packet()); if(!retained) {done(q,packet());ack(q,packet());} }
    TxBurstQueue q(a,b);
    if(retained) { assert(q.fenced() && q.containmentRequired());assert(q.enqueue(a.binding,packet(),100000,NOW)==Code::Fenced); }
    else { assert(q.enqueue(a.binding,packet(),100000,NOW)==Code::Stale);enqueue(q,packet(2)); }
    assert(b.counters.attempts==(retained ? 1u : 2u));
}
void reconstruction_clock() {
    auto b=boot();auto a=admission();
    {
        TxBurstQueue q(a,b);enqueue(q,packet());issue(q,packet());
        done(q,packet(),NOW+5000);ack(q,packet());
    }
    assert(b.last_now_us==NOW+5000 && !b.fenced && !b.queue_owned);
    TxBurstQueue q(a,b);
    assert(q.enqueue(a.binding,packet(2),100000,NOW+4999)==Code::ClockRegressed);
    assert(b.last_now_us==NOW+5000 && b.counters.attempts==1);
    assert(q.fenced() && !q.containmentRequired());
    assert(q.issue(NOW+5000,false,true).code==Code::Fenced);
}
void duplicate_owner() {
    auto b=boot();auto a=admission();TxBurstQueue q(a,b);enqueue(q,packet());issue(q,packet());
    { TxBurstQueue other(a,b);assert(other.fenced() && other.containmentRequired()); }
    assert(q.poll(NOW)==Code::Fenced && state(q)==State::Unknown && q.containmentRequired());
    assert(b.queue_owned);q.containmentComplete(true);assert(!q.containmentRequired() && q.fenced());
}
void hex(const std::uint8_t* bytes,std::size_t size) {
    for(std::size_t n=0;n<size;n++) std::printf("%02x",unsigned(bytes[n]));
}
void trace(TxBurstQueue& q,const Packet& p,std::uint64_t at) {
    Evidence e;assert(q.evidence(p.request,e));
    std::printf("%llu,%llu,%u,%u,%llu,%llu,",
        static_cast<unsigned long long>(at),static_cast<unsigned long long>(e.request),
        unsigned(e.state),unsigned(e.size),static_cast<unsigned long long>(e.estimated_airtime_us),
        static_cast<unsigned long long>(p.deadline_us));
    hex(e.digest,32);std::printf(",");hex(e.binding.boot,16);std::printf(",");hex(e.binding.context_sha256,32);
    std::printf(",%llu,%u,%d\n",static_cast<unsigned long long>(e.driver_elapsed_us),e.irq_flags,int(e.driver_error));
}
void conformance() {
    auto b=boot();TxBurstQueue q(admission(),b);
    for(std::uint64_t n=1;n<=3;n++) { enqueue(q,packet(n));trace(q,packet(n),NOW); }
    issue(q,packet(1),NOW+1000);trace(q,packet(1),NOW+1000);
    assert(q.settle(driver(packet(1),State::Started),NOW+2000)==Code::Accepted);
    trace(q,packet(1),NOW+2000);
    done(q,packet(1),NOW+103000);trace(q,packet(1),NOW+103000);
    issue(q,packet(2),NOW+104000);trace(q,packet(2),NOW+104000);
    assert(q.poll(NOW+105000)==Code::Accepted);q.cancel();
    trace(q,packet(2),NOW+105000);trace(q,packet(3),NOW+105000);
    assert(q.fenced() && q.containmentRequired() && b.counters.attempts==3);
    assert(b.counters.reserved_airtime_us==300000);
}
int main(int argc,char** argv) {
    assert(argc==2);const char* scenario=argv[1];
    if(!std::strcmp(scenario,"prepaid")) prepaid();
    else if(!std::strcmp(scenario,"replay")) replay();
    else if(!std::strcmp(scenario,"invalid")) invalid();
    else if(!std::strcmp(scenario,"budget")) budget();
    else if(!std::strcmp(scenario,"backpressure")) backpressure();
    else if(!std::strcmp(scenario,"cancel_queued")) cancel_queued();
    else if(!std::strcmp(scenario,"cancel_issued")) cancel_issued(false);
    else if(!std::strcmp(scenario,"cancel_started")) cancel_issued(true);
    else if(!std::strcmp(scenario,"expiry_queued")) expiry(false);
    else if(!std::strcmp(scenario,"expiry_issued")) expiry(true);
    else if(!std::strcmp(scenario,"late_result")) late_result();
    else if(!std::strcmp(scenario,"clock_regression")) regression(false);
    else if(!std::strcmp(scenario,"debt_regression")) regression(true);
    else if(!std::strcmp(scenario,"reconstruction_clean")) reconstruction(false);
    else if(!std::strcmp(scenario,"reconstruction_uncertain")) reconstruction(true);
    else if(!std::strcmp(scenario,"reconstruction_clock")) reconstruction_clock();
    else if(!std::strcmp(scenario,"duplicate_owner")) duplicate_owner();
    else if(!std::strcmp(scenario,"conformance")) conformance();
    else bad_result(scenario);
}
