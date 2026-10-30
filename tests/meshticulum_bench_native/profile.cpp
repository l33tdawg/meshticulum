// Actual firmware loop with deterministic fake-driver delays; no devices.
#define main existing_fault_main
#include "harness.cpp"
#undef main

using P=mtbench::profiling::Phase;
static void aggregateControls() {
    mtbench::profiling::Aggregate row;
    row.record(100,110,true);row.record(200,230,false);row.record(300,300,true);
    assert(row.calls==3 && row.failures==1 && row.total_us==40 && row.min_us==0 && row.max_us==30);
    row.record(20,10,false);
    assert(row.calls==4 && row.failures==2 && row.invalid==1 && row.flags==2 && row.total_us==40);
    row.calls=row.failures=row.invalid=UINT32_MAX;row.total_us=UINT64_MAX-1;
    row.record(0,5,false);row.record(1,0,false);
    assert(row.calls==UINT32_MAX && row.failures==UINT32_MAX && row.invalid==UINT32_MAX);
    assert(row.total_us==UINT64_MAX && row.flags==3);
    mtbench::profiling::Aggregate extreme;extreme.record(0,UINT64_MAX,true);
    assert(extreme.min_us==UINT64_MAX && extreme.max_us==UINT64_MAX && !extreme.flags);
    extreme.record(0,1,true);assert(extreme.flags==1 && extreme.total_us==UINT64_MAX);
    assert(sizeof(mtbench::profiling::Profile)<=1024);
}
#if MTBENCH_NATIVE_PROFILE
static const mtbench::profiling::Aggregate& row(P phase) { return nativeProfile.rows[uint8_t(phase)]; }
static void exact(P phase,uint64_t duration,uint32_t calls=1,uint32_t failures=0) {
    const auto& value=row(phase);
    assert(value.calls==calls && value.failures==failures && !value.invalid && !value.flags);
    assert(value.total_us==duration);
}
static Frame profileQuery(uint64_t id=100,int phase=-1) {
    auto f=request(NATIVE_PROFILE,id);if(phase>=0) { f.size=1;f.body[0]=uint8_t(phase); }return f;
}
static void queryRefused(uint8_t reason,int phase=-1) {
    const auto mutation=lastMutation,paid=reservedUs;const auto count=attempted,starts=radio.starts;
    command(profileQuery(100,phase));auto rows=drain();
    assert(rows.size()==1 && rows[0].type==REFUSED && rows[0].body[0]==reason);
    assert(lastMutation==mutation && reservedUs==paid && attempted==count && radio.starts==starts);
}
static void issueWithDelays() {
    burstPrepare();command(burstDataRequest());drain();
    radio.standby_advance_us=11;radio.irq_advance_us=13;radio.clear_advance_us=17;
    board.before_advance_us=19;radio.start_advance_us=23;
    burstService(true);
    exact(P::IssueStandby,11);exact(P::IssueIrqReread,13);exact(P::IssueIrqClear,17);
    exact(P::IssueCopyPrepare,19);exact(P::IssueTxStartCall,23);
    exact(P::IssuedToStarted,83);exact(P::IssuedPublish,0);exact(P::StartedPublish,0);
    assert(transmitting && attempted==1 && reservedUs==100000);
}
static void finishWithDelays() {
    radio.finish_advance_us=29;board.after_advance_us=31;radio.receive_advance_us=37;
    const auto before=fake_time;radio.flags=RADIOLIB_SX126X_IRQ_TX_DONE;serviceRadio();
    exact(P::TxFinishCleanup,60);exact(P::RxStartCall,37,2);exact(P::TxDoneToRxReturn,97);
    assert(fake_time==before+13+97 && burstNextStartUs==fake_time+BURST_REVERSE_US);
    assert(!transmitting && armed && !fenced && burstEvidence().state==burst_native::State::RfDone);
}
#endif

int main(int argc,char** argv) {
    assert(argc==2);const std::string mode=argv[1];
    if(mode=="profile_aggregate") { aggregateControls();return 0; }
    if(mode.rfind("profile_",0)!=0) return existing_fault_main(argc,argv);
    setup();
#if !MTBENCH_NATIVE_PROFILE
    assert(mode=="profile_disabled");
    const auto clocks=fake_clock_reads;unsigned evaluated=0;
    MT_PROFILE_SCOPE(probe,RxStartCall);MT_PROFILE_END(probe,++evaluated);
    MT_PROFILE_SCOPE_AT(other,IssuedToStarted,++evaluated);MT_PROFILE_END(other,++evaluated);
    assert(fake_clock_reads==clocks && !evaluated);
    command(request(NATIVE_PROFILE,100));auto rows=drain();
    assert(rows.size()==1 && rows[0].type==REFUSED && rows[0].body[0]==BAD_COMMAND);
    assert(!attempted && !reservedUs && !lastMutation && !radio.starts);return 0;
#else
    if(mode=="profile_memory" || mode=="profile_memory_wire") {
        const auto clocks=fake_clock_reads;
        radioServiceTask=nullptr;queryRefused(BUSY,0xfe);radioServiceTask=fake_loop_task;
        armed=true;queryRefused(BUSY,0xfe);armed=false;
        transmitting=true;queryRefused(BUSY,0xfe);transmitting=false;
        resultPending=true;queryRefused(BUSY,0xfe);resultPending=false;
        serialContainmentPending=true;queryRefused(BUSY,0xfe);serialContainmentPending=false;
        burstBoot.containment_required=true;queryRefused(BUSY,0xfe);burstBoot.containment_required=false;
        auto wrong=profileQuery(100,0xfe);wrong.boot[0]^=1;command(wrong);
        auto rows=drain();assert(rows[0].type==REFUSED && rows[0].body[0]==WRONG_BOOT);
        assert(!fake_heap_queries && !fake_stack_queries && fake_clock_reads==clocks);
        command(profileQuery(100,0xfe));rows=drain();assert(rows.size()==1);
        const auto& f=rows[0];assert(f.type==NATIVE_PROFILE_REPLY && f.size==24);
        assert(!memcmp(f.body,"MNS1\x01\x01\x00\x00",8));
        assert(u32(f.body+8)==123456 && u32(f.body+12)==100000
               && u32(f.body+16)==65536 && u32(f.body+20)==4096);
        assert(fake_heap_queries==1 && fake_stack_queries==1 && fake_clock_reads==clocks);
        assert(!attempted && !reservedUs && !lastMutation && !radio.starts);
        if(mode=="profile_memory_wire") {
            uint8_t bytes[MAX_ENCODED];const auto n=encode(f,bytes,sizeof(bytes));
            std::cout.write(reinterpret_cast<char*>(bytes),n);
        }
        fake_heap_info.total_free_bytes=uint64_t(UINT32_MAX)+1;
        queryRefused(BAD_COMMAND,0xfe);return 0;
    }
    if(mode=="profile_query") {
        const auto clocks=fake_clock_reads;command(profileQuery());auto rows=drain();
        assert(rows.size()==1 && rows[0].type==NATIVE_PROFILE_REPLY && rows[0].size==8);
        assert(!memcmp(rows[0].body,"MNP1",4) && rows[0].body[4]==12 && rows[0].body[5]==40);
        assert(fake_clock_reads==clocks && !lastMutation && !attempted && !reservedUs);
        const auto capability=rows[0];command(profileQuery());rows=drain();
        assert(rows[0].size==capability.size && !memcmp(rows[0].body,capability.body,8));
        auto f=profileQuery();f.boot[0]^=1;command(f);rows=drain();assert(rows[0].body[0]==WRONG_BOOT);
        f=profileQuery(101,12);command(f);rows=drain();assert(rows[0].body[0]==BAD_COMMAND);
        f.size=2;command(f);rows=drain();assert(rows[0].body[0]==BAD_COMMAND);
        assert(!lastMutation);command(armRequest());assert(armed && lastMutation==1);drain();
        queryRefused(BUSY);return 0;
    }
    if(mode=="profile_busy") {
        resultPending=true;queryRefused(BUSY);resultPending=false;
        serialContainmentPending=true;queryRefused(BUSY);serialContainmentPending=false;
        burstBoot.containment_required=true;queryRefused(BUSY);burstBoot.containment_required=false;
        burstPrepare();command(burstDataRequest());drain();
        armed=false;queryRefused(BUSY);queryRefused(BUSY,0xfe);armed=true;burstService(true);drain();queryRefused(BUSY);
        burstDone();drain();armed=false;queryRefused(BUSY);queryRefused(BUSY,0xfe);
        assert(!fake_heap_queries && !fake_stack_queries);return 0;
    }
    if(mode=="profile_failed_start") {
        burstPrepare();command(burstDataRequest());drain();
        radio.start_advance_us=47;radio.start_error=-9;burstService(true);
        exact(P::IssueTxStartCall,47,1,1);exact(P::IssuedToStarted,47,1,1);
        assert(fenced && !armed && !transmitting && attempted==1 && reservedUs==100000);
        assert(burstEvidence().state==burst_native::State::Unknown && !row(P::StartedPublish).calls);return 0;
    }
    if(mode=="profile_failed_rx" || mode=="profile_failed_finish") {
        issueWithDelays();drain();radio.flags=RADIOLIB_SX126X_IRQ_TX_DONE;
        if(mode=="profile_failed_rx") { radio.receive_advance_us=37;radio.receive_error=-9; }
        else { radio.finish_advance_us=29;radio.finish_error=-9; }
        serviceRadio();assert(fenced && !armed && !transmitting && attempted==1 && reservedUs==100000);
        assert(row(P::TxDoneToRxReturn).failures==1);
        if(mode=="profile_failed_rx") {
            exact(P::RxStartCall,37,2,1);assert(burstEvidence().state==burst_native::State::RfDone);
        } else {
            exact(P::TxFinishCleanup,29,1,1);assert(burstEvidence().state==burst_native::State::Unknown);
            assert(row(P::RxStartCall).calls==1);
        }
        return 0;
    }
    if(mode=="profile_irq_race") {
        burstPrepare();command(burstDataRequest());drain();
        radio.irq_on_standby=RADIOLIB_SX126X_IRQ_RX_DONE;radio.shared_fifo=true;
        radio.overwrite_during_read=true;radio.read_advance_us=53;burstService(true);
        exact(P::IssueRxDrain,53);exact(P::IssuedToStarted,53);
        auto rows=drain();bool rx=false;for(const auto& f:rows) if(f.type==RECEIVE) {
            const uint8_t expected[]={0xc0,0xdb,0,255};assert(f.size==8 && !memcmp(f.body+4,expected,4));rx=true;
        }
        assert(rx && transmitting && !droppedRx && !fenced);return 0;
    }
    assert(mode=="profile_timings" || mode=="profile_wire");
    issueWithDelays();finishWithDelays();drain();
    assert(!fake_heap_queries && !fake_stack_queries); // Never scan heap/stack on RF paths.
    command(burstAckRequest(4,3));drain();command(request(DISARM,5));drain();
    assert(!armed && !burstQueue && !fenced && attempted==1 && reservedUs==100000);
    const auto mutation=lastMutation,paid=reservedUs;
    for(int phase=-1;phase<int(mtbench::profiling::PHASES);phase++) {
        command(profileQuery(100+phase+1,phase));auto rows=drain();assert(rows.size()==1);
        const auto& f=rows[0];assert(f.type==NATIVE_PROFILE_REPLY && f.size==(phase<0 ? 8 : 48));
        if(mode=="profile_wire") { uint8_t bytes[MAX_ENCODED];const auto n=encode(f,bytes,sizeof(bytes));std::cout.write(reinterpret_cast<char*>(bytes),n); }
    }
    assert(lastMutation==mutation && reservedUs==paid && attempted==1);return 0;
#endif
}
