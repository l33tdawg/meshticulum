"""Explicit generic grant/crypto/flow controls; synthetic RF worker, no device."""
import json,os
from contextlib import ExitStack
from dataclasses import replace
import pytest
from meshticulum.bench.gfsk_profile import GfskProfile,GfskProfileError
from meshticulum.bench.admission import GfskAdmission
from meshticulum.bench.authority import RunAuthority
from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.state import SessionStore
from meshticulum.bench.wire import SessionCipher,WireError
from meshticulum.bench.flow_runtime import BenchFlow
from meshticulum.scheduler.budget import BudgetPolicy,Ledger
from tests.test_meshticulum_bench_runtime import Clock,ModelWorker,BOOT,provision
from tests.radio_profiles import SOURCE_ADMISSION

P=GfskProfile(921375000,0,90000,256,60000000)
A=GfskAdmission('MY',(921375000,),(0,),1000,180000,256,60000000)


def gfsk_plan(path,unit,payload,clock,*,marked=True):
 plan=provision(path,unit,payload,clock);plan['profile']=P.to_dict()
 if marked:plan['radio_modem']='gfsk125'
 (path/'grant.json').write_text(json.dumps(plan));(path/'grant.json').chmod(0o600)
 return plan


def test_gfsk_json_record_needs_explicit_board_context_and_preserves_higher_power():
 high=replace(P,chip_power_dbm=22);assert GfskProfile.from_dict(high.to_dict(),board_id=2)==high
 with pytest.raises(GfskProfileError):GfskProfile.from_dict(high.to_dict(),board_id=1)


@pytest.mark.parametrize('fault',['lora_admission','missing_marker','missing_antenna','wrong_frequency'])
def test_gfsk_authority_refuses_wrong_admission_or_unmarked_plan(tmp_path,fault):
 clock=Clock();path=tmp_path/'a';p=gfsk_plan(path,'unit-a',b'public',clock,marked=fault!='missing_marker')
 if fault=='missing_antenna':p['antenna_confirmed']=False
 if fault=='wrong_frequency':p['profile']['frequency_hz']=925000000
 (path/'grant.json').write_text(json.dumps(p));(path/'grant.json').chmod(0o600)
 with pytest.raises((WireError,GfskProfileError,TypeError)):
  RunAuthority(path,radio_admission=SOURCE_ADMISSION if fault=='lora_admission' else A,expected_unit='unit-a',host_boot_id=BOOT,clock=clock,owner_probe=lambda:True)


def test_real_generic_gfsk_grants_aead_flow_and_debt_without_frame_changes(tmp_path):
 clock=Clock();payload=bytes(range(256))*4;flows=[];workers=[];journals=[]
 with ExitStack() as stack:
  for unit in ('unit-a','unit-b'):
   path=tmp_path/unit;gfsk_plan(path,unit,payload,clock)
   journal=AttemptJournal(path/'attempts.db',binding=bytes(32),max_attempts=256,max_airtime_us=60000000,create=True,request_clock=lambda:clock()[0]);stack.callback(journal.close)
   worker=ModelWorker(journal,clock);journals.append(journal);workers.append(worker)
   authority=RunAuthority(path,radio_admission=A,expected_unit=unit,host_boot_id=BOOT,clock=clock,owner_probe=lambda:True);stack.callback(authority.close)
   store=SessionStore(path/'wire.db',authority.grant,authority.local_peer,boot_id=BOOT,clock=clock,create=True);stack.callback(store.close)
   cipher=SessionCipher(authority.grant,authority.local_peer,store)
   ledger=Ledger(BudgetPolicy(180000000,60000000,120000000,1000000,quantization_us=1),authority.ledger_epoch,clock()[1])
   endpoint=BenchFlow(authority=authority,worker=worker,cipher=cipher,journal=journal,flow_key=authority.flow_key,contract=authority.contract,profile=authority.carrier_profile,ledger=ledger,scheduler_epoch=authority.scheduler_epoch,broker_epoch=authority.broker_epoch,sends=unit=='unit-a',clock=clock);stack.callback(endpoint.close);flows.append(endpoint)
   assert authority.route_record['radio_modem']=='gfsk125' and type(authority.profile) is GfskProfile
  workers[0].peer=workers[1];workers[1].peer=workers[0];flows[0].send(payload)
  for _ in range(1500):
   flows[0].step();flows[1].step();clock.advance(50000)
   if flows[0].flow.complete and flows[1].flow.complete:break
  assert flows[0].flow.complete and flows[1].flow.complete and bytes(flows[1].received)==payload
  assert all(j.snapshot()['attempts']>0 and j.snapshot()['reserved_estimated_airtime_us']>0 for j in journals)
