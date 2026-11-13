"""Actual run_object cleanup path with model resources; never opens devices."""
from types import SimpleNamespace
import pytest
from meshticulum.bench import authority_timing as timing
from tests.test_host_timing_example import examples,run_model


def enable(example,monkeypatch):
 original=example.run_object
 def invoke(args,*rest,**kwargs):
  args.profile_authority=True
  return original(args,*rest,**kwargs)
 monkeypatch.setattr(example,'run_object',invoke)


def leaves(error):
 return [x for e in error.exceptions for x in leaves(e)] if isinstance(error,BaseExceptionGroup) else [error]

@pytest.mark.parametrize('operation_error',[False,True])
def test_actual_run_restores_all_bound_objects_on_success_and_failure(examples,run_model,monkeypatch,operation_error):
 example=examples[0];enable(example,monkeypatch);objects=[];original=RuntimeError('operation')
 class Binding(timing.AuthorityTimingBindings):
  def bind(self,obj):
   objects.append((obj,vars(obj).get('authority_timing','missing')));super().bind(obj)
 monkeypatch.setattr(example,'AuthorityTimingBindings',Binding)
 invoke,calls,_=run_model(enabled=False,operation_error=original if operation_error else None)
 if operation_error:
  with pytest.raises(BaseExceptionGroup)as caught:invoke()
  assert original in leaves(caught.value)
  assert len(caught.value.authority_phase_timings['phases'])==5
 else:assert len(invoke()['authority_phase_timings']['phases'])==5
 assert len(objects)==4
 for obj,old in objects:
  assert vars(obj).get('authority_timing','missing')==old
 assert 'transport.close'in calls and 'owner.close'in calls and 'authority.close'in calls


def test_binding_allocation_failure_precedes_any_open(examples,run_model,monkeypatch):
 example=examples[0];enable(example,monkeypatch);invoke,calls,collectors=run_model(enabled=False)
 monkeypatch.setattr(example,'AuthorityTimingBindings',lambda *_:(_ for _ in ()).throw(MemoryError('binding allocation')))
 with pytest.raises(MemoryError):invoke()
 assert calls==[] and collectors==[]

@pytest.mark.parametrize('cancel',[False,True])
def test_failure_snapshot_cannot_replace_operation_error(examples,run_model,monkeypatch,cancel):
 example=examples[0];enable(example,monkeypatch);original=RuntimeError('body');invoke,calls,_=run_model(enabled=False,operation_error=original)
 def fail(*_):raise KeyboardInterrupt() if cancel else MemoryError()
 monkeypatch.setattr(timing.AuthorityPhaseTimings,'snapshot',fail)
 with pytest.raises(BaseExceptionGroup)as caught:invoke()
 assert original in leaves(caught.value) and caught.value.authority_phase_timings_unavailable
 assert 'transport.close'in calls

@pytest.mark.parametrize('operation_error',[False,True])
def test_restore_failure_marks_unavailable_and_preserves_body(examples,run_model,monkeypatch,operation_error):
 example=examples[0];enable(example,monkeypatch);original=RuntimeError('body')
 class BadState(dict):
  def pop(self,*args):raise MemoryError('restore')
 class Binding(timing.AuthorityTimingBindings):
  def bind(self,obj):
   super().bind(obj)
   if len(self.saved)==1:self.saved.append((BadState(authority_timing=self.timing),self.missing))
 monkeypatch.setattr(example,'AuthorityTimingBindings',Binding)
 invoke,calls,_=run_model(enabled=False,operation_error=original if operation_error else None)
 if operation_error:
  with pytest.raises(BaseExceptionGroup)as caught:invoke()
  assert original in leaves(caught.value);result=caught.value.authority_phase_timings
  assert caught.value.authority_phase_timings_unavailable
 else:
  value=invoke();assert value['authority_phase_timings_unavailable'];result=value['authority_phase_timings']
 assert result['binding_errors']>0 and 'transport.close'in calls


def test_restore_cancellation_restores_other_owners_before_propagating(examples,run_model,monkeypatch):
 example=examples[0];enable(example,monkeypatch);objects=[]
 class BadState(dict):
  def pop(self,*args):raise KeyboardInterrupt('restore cancellation')
 class Binding(timing.AuthorityTimingBindings):
  def bind(self,obj):
   super().bind(obj);objects.append(obj)
   self.saved.append((BadState(authority_timing=self.timing),self.missing))
 monkeypatch.setattr(example,'AuthorityTimingBindings',Binding)
 invoke,calls,_=run_model(enabled=False)
 with pytest.raises(KeyboardInterrupt):invoke()
 assert all('authority_timing'not in vars(obj)for obj in objects)
 assert 'transport.close'in calls


def test_invalid_option_before_open_and_default_no_sampling(examples,run_model,monkeypatch):
 example=examples[0];original=example.run_object;invoke,calls,collectors=run_model(enabled=False)
 def invalid(args,*rest,**kwargs):
  args.profile_authority=1;return original(args,*rest,**kwargs)
 monkeypatch.setattr(example,'run_object',invalid)
 with pytest.raises(ValueError,match='invalid_authority_profile_option'):invoke()
 assert calls==[] and collectors==[]

def test_file_window_authority_clock_last_and_call_counts_unchanged(examples,monkeypatch):
 example=examples[0];calls=[];window=example.FileWindow.__new__(example.FileWindow)
 class Path:
  def lstat(self):calls.append('stat');return SimpleNamespace(st_dev=1,st_ino=2)
 window.path=Path();window.identity=(1,2);window.raw=b'raw';window.record={'host_boot_id':'boot','original_epoch_us':1,'expires_epoch_us':100,'original_monotonic_us':1};window.deadline_us=100;window.last=None
 window.boot_reader=lambda:calls.append('boot') or 'boot'
 window.clock=lambda:calls.append('authority-clock') or (5,5)
 monkeypatch.setattr(example,'read_file',lambda *a,**k:calls.append('read') or b'raw')
 expected=None
 for enabled in [False,True]:
  window.authority_timing=timing.AuthorityPhaseTimings()if enabled else None;calls.clear();window.last=None
  assert window.check()==(5,5)
  if expected is None:expected=list(calls)
  else:assert calls==expected
  assert calls==['stat','read','boot','authority-clock']
  window.last=(6,6);calls.clear()
  with pytest.raises(ValueError):window.check()
  assert calls==expected

@pytest.mark.parametrize('fail',[False,True])
def test_actual_borrowed_hardware_owner_and_window_prior_attributes_restored(examples,run_model,monkeypatch,fail):
 example=examples[0];original_error=RuntimeError('borrowed body');invoke,calls,_=run_model(enabled=False,operation_error=original_error if fail else None)
 owner_previous=object();window_previous=object();seen=[]
 class Hardware:
  def __init__(self):
   self.transport=example.ExclusiveSerial();self.owner=SimpleNamespace(authority_timing=owner_previous);self.releases=[]
  def borrow(self,args,window,timing):return self.transport,self.owner
  def release(self,success):self.releases.append(success)
 monkeypatch.setattr(example,'FileHardware',Hardware);hardware=Hardware();original=example.run_object
 def run(args,raw,admission,version,payload,window,factory,**kwargs):
  args.profile_authority=True;window.authority_timing=window_previous;seen.append(window)
  return original(args,raw,admission,version,payload,window,factory,hardware=hardware)
 monkeypatch.setattr(example,'run_object',run)
 if fail:
  with pytest.raises(BaseExceptionGroup)as caught:invoke()
  assert original_error in leaves(caught.value)
 else:invoke()
 assert hardware.owner.authority_timing is owner_previous
 assert seen[0].authority_timing is window_previous
 assert hardware.releases==[not fail]
 assert 'transport.close'not in calls # Borrowed closure remains with the owner.


def test_profile_authority_cli_explicit_default_off(examples):
 import argparse
 parser=argparse.ArgumentParser();examples[0].add_host_timing_argument(parser)
 assert parser.parse_args([]).profile_authority is False
 assert parser.parse_args(['--profile-authority']).profile_authority is True
