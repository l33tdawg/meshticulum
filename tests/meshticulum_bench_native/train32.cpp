// Real adapter, static three host-owned slots, unchanged native max8/guards.
#define main existing_fault_main
#include "harness.cpp"
#undef main
static Frame open32(uint64_t id=2) {
 auto f=burstOpenRequest(id);memmove(f.body+2,f.body,48);f.body[0]=8;f.body[1]=32;f.size=50;return f;
}
static Frame data32(uint64_t id,uint64_t end) {
 auto f=burstDataRequest(id);put64(f.body+8,end);put64(f.body+16,radio.airtime_us);return f;
}
int main(int argc,char**argv) {
 assert(argc==2);std::string mode=argv[1];setup();assert(ready);
 auto cap=request(BURST_CAPABILITIES,90);cap.size=2;cap.body[0]=8;cap.body[1]=32;
 command(cap);auto rows=drain();assert(rows.size()==1 && rows[0].size==25);
 assert(!memcmp(rows[0].body,"MBT3\x08\x20\x07",7));
 assert(u32(rows[0].body+9)==2000000&&u32(rows[0].body+13)==100000&&u32(rows[0].body+17)==5000&&u32(rows[0].body+21)==100000);
 if(mode=="capability"){assert(!attempted&&!radio.starts&&!lastMutation);return 0;}
 radio.airtime_us=35920;auto arm=armRequest();put32(arm.body+4,500000);arm.body[8]=5;put16(arm.body+11,32);
 put32(arm.body+14,10000);put32(arm.body+18,64);put64(arm.body+22,10000000);
 command(arm);drain();command(open32());drain();assert(burstQueue&&burstDepth==8&&burstTrainFrames==32);
 const uint64_t end=fake_time+1900000,openEnd=burstOpenDeadline;uint64_t id=3;
 for(unsigned i=0;i<3;i++){command(data32(id++,end));drain();}
 assert(attempted==3&&burstQueue->retained()==3);
 if(mode=="late_budget"){
  fake_time=end-100000;const auto debt=reservedUs;command(data32(id++,end));rows=drain();
  assert(rows.back().type==REFUSED&&rows.back().body[0]==BUDGET&&attempted==3&&reservedUs==debt&&burstOpenDeadline==openEnd);return 0;
 }
 fake_time=std::max(fake_time,burstNextStartUs);loop();assert(radio.starts==1);
 if(mode=="cancel"){
  auto f=request(BURST_CANCEL,id++);f.size=8;put64(f.body,burstToken);command(f);drain();
  assert(fenced&&!armed&&!transmitting&&attempted==3&&burstOpenDeadline==openEnd);return 0;
 }
 assert(mode=="thirtytwo");
 for(unsigned n=1;n<=32;n++){
  const auto active=burstActiveRequest;assert(active);
  // Every nonfinal completion has a genuinely queued successor, not a retained active record.
  if(n<32)assert(burstQueue->queued()>0);
  fake_time+=38920;radio.flags=RADIOLIB_SX126X_IRQ_TX_DONE;loop();assert(!transmitting);
  assert(burstNextStartUs==burstLastRxStartUs+(n==32?BURST_REVERSE_US:BURST_RX_US));
  command(burstAckRequest(id++,active));drain();
  if(n<=29){command(data32(id++,end));rows=drain();assert(rows.back().type==BURST_EVENT&&attempted==3+n);}
  assert(burstQueue->retained()<=3&&burstOpenDeadline==openEnd);
  if(n<32){fake_time=burstNextStartUs-1;loop();assert(radio.starts==n);fake_time++;loop();assert(radio.starts==n+1);}
 }
 assert(attempted==32&&!fenced&&!burstQueue->pending()&&!burstQueue->retained());
 const auto floor=burstNextStartUs,debt=reservedUs;command(open32(id++));drain();
 assert(burstNextStartUs==floor&&reservedUs==debt&&burstOpenDeadline==openEnd);
 auto fresh=data32(id++,fake_time+1900000);put64(fresh.body,burstToken);command(fresh);drain();
 fake_time=floor-1;loop();assert(radio.starts==32);fake_time=floor;loop();assert(radio.starts==33);
}
