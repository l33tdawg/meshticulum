// Independent edge controls reuse only the source fixture and real SHA helper.
// Renaming the unused fixture entry point removes main's implicit return rule.
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wreturn-type"
#define main original_batch_fixture_main
#include "tx_batch.cpp"
#undef main
#pragma clang diagnostic pop

int main(int argc,char** argv) {
    assert(argc==2);
    Fixture f;
    if(!std::strcmp(argv[1],"sizes")) {
        std::printf("%zu %zu %zu\n",sizeof(TxBatchJob),sizeof(TxBurstQueue),sizeof(BootState));
        return 0;
    }
    if(!std::strcmp(argv[1],"exact_budget")) {
        f.b.counters.attempts=1020;
        f.b.counters.reserved_airtime_us=f.a.max_airtime_us-400000;
    }
    TxBatchJob q(f.a,1,f.b,f.pacing,hash);
    const auto before=allocations;
    if(!std::strcmp(argv[1],"overtaken_staging")) {
        f.stage(q);
        f.b.request_highwater=17; // Another serialized mutation consumed these IDs.
        assert(q.commit(f.d,18,NOW)==Code::Stale);
        assert(f.b.request_highwater==17 && f.b.counters.attempts==0);
        assert(f.b.counters.reserved_airtime_us==0 && q.retained()==0);
        assert(q.issue(NOW,false,true).code==Code::Missing);
    } else if(!std::strcmp(argv[1],"exact_budget")) {
        f.commit(q);
        assert(f.b.counters.attempts==1024);
        assert(f.b.counters.reserved_airtime_us==f.a.max_airtime_us);
        assert(q.issue(NOW,false,true).code==Code::Accepted);
        q.cancel();q.containmentComplete(true);
        BatchRelease entries[4];releaseEntries(q,entries);
        assert(q.release(f.d,16,entries,4,NOW)==Code::Accepted);
        assert(f.b.counters.attempts==1024 && f.b.counters.reserved_airtime_us==f.a.max_airtime_us);
        assert(q.fenced());
    } else if(!std::strcmp(argv[1],"release_clock_regression")) {
        f.commit(q);finish(q,f);
        BatchRelease entries[4];releaseEntries(q,entries);
        assert(q.release(f.d,16,entries,4,NOW+429999)==Code::ClockRegressed);
        assert(q.retained()==4 && f.b.request_highwater==15 && q.fenced());
        assert(q.release(f.d,16,entries,4,NOW+430000)==Code::Accepted);
        assert(q.retained()==0 && f.b.counters.attempts==4 && q.fenced());
    } else if(!std::strcmp(argv[1],"explicit_unknown_late_done")) {
        f.commit(q);assert(q.issue(NOW,false,true).code==Code::Accepted);
        assert(q.settle(evidence(f.packets[0],State::Unknown),NOW+100)==Code::Accepted);
        BatchRelease entries[4];releaseEntries(q,entries);
        assert(entries[0].state==State::Unknown && q.containmentRequired());
        assert(q.settle(evidence(f.packets[0]),NOW+200)==Code::Conflict);
        assert(q.release(f.d,16,entries,4,NOW+200)==Code::Busy && q.retained()==4);
        Evidence retained;assert(q.evidence(11,retained));
        assert(retained.state==State::Unknown && retained.event_us==NOW+100);
        q.containmentComplete(true);
        assert(q.release(f.d,16,entries,4,NOW+200)==Code::Accepted);
        assert(q.fenced() && f.b.fenced && f.b.counters.attempts==4);
        assert(q.issue(NOW+200000,false,true).code==Code::Fenced);
    } else if(!std::strcmp(argv[1],"wrong_open_token")) {
        auto other=f.d;other.token=2;
        assert(q.begin(other,NOW)==Code::WrongBinding);
        f.stage(q);
        assert(q.stage(other,0,f.packets[0],100000,NOW)==Code::WrongBinding);
        assert(q.commit(other,15,NOW)==Code::WrongBinding);
        assert(f.b.counters.attempts==0 && q.retained()==0);
        assert(q.commit(f.d,15,NOW)==Code::Accepted);
    } else if(!std::strcmp(argv[1],"late_known_completion")) {
        f.commit(q);assert(q.issue(NOW,false,true).code==Code::Accepted);
        assert(q.settle(evidence(f.packets[0]),END+1)==Code::Accepted);
        BatchRelease entries[4];releaseEntries(q,entries);
        assert(entries[0].state==State::RfDone);
        for(unsigned n=1;n<4;n++) assert(entries[n].state==State::NotSent);
        assert(q.issue(END+1,false,true).code==Code::Busy); // Retained tail floor.
        assert(q.release(f.d,16,entries,4,END+1)==Code::Accepted);
        assert(q.issue(END+100001,false,true).code==Code::Missing);
        assert(f.b.counters.attempts==4 && f.b.counters.reserved_airtime_us==400000);
    } else assert(false);
    assert(allocations==before);
}
