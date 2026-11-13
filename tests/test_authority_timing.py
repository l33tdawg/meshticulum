import json
from types import SimpleNamespace
import pytest
from meshticulum.bench import authority_timing as t
P=t.AuthorityPhase.HAT_LIVE

def clocks(monkeypatch,wall=(10,30),cpu=(2,8)):
 w=iter(wall);c=iter(cpu)
 monkeypatch.setattr(t,'_WALL',lambda:next(w));monkeypatch.setattr(t,'_CPU',lambda:next(c))
def row(a):return a.snapshot()['phases']['hat_live']

def test_fixed_independent_channels_and_bounded_snapshot(monkeypatch):
 clocks(monkeypatch);a=t.AuthorityPhaseTimings()
 with t.authority_phase(a,P):pass
 assert row(a)['wall']['total_ns']==20 and row(a)['thread_cpu']['total_ns']==6
 assert len(a.snapshot()['phases'])==5 and a.snapshot()['overlapping'] is True
 assert len(json.dumps(a.snapshot()))<8192 and a.snapshot()['active_phase_mask']==0
 from meshticulum.bench.host_timing import Phase,HostPhaseTimings
 assert len(Phase)==32 and HostPhaseTimings().snapshot()['schema']=='meshticulum.host-phase-timing.v1'

def test_unsupported_cpu_retains_wall(monkeypatch):
 clocks(monkeypatch);monkeypatch.setattr(t,'_CPU',None);a=t.AuthorityPhaseTimings()
 with t.authority_phase(a,P):pass
 assert row(a)['wall']['count']==1 and row(a)['thread_cpu'] is None

@pytest.mark.parametrize('channel',['wall','cpu'])
def test_failed_channel_never_discards_other(monkeypatch,channel):
 clocks(monkeypatch)
 def failed():raise OSError('diagnostic clock')
 monkeypatch.setattr(t,'_WALL'if channel=='wall'else'_CPU',failed);a=t.AuthorityPhaseTimings()
 with t.authority_phase(a,P):pass
 bad='wall'if channel=='wall'else'thread_cpu';good='thread_cpu'if channel=='wall'else'wall'
 assert row(a)[bad]['invalid']==1 and row(a)[good]['count']==1
 assert a._active==0

def test_reentrancy_dropped_and_storage_saturates(monkeypatch):
 clocks(monkeypatch);a=t.AuthorityPhaseTimings()
 with t.authority_phase(a,P):
  with t.authority_phase(a,P):pass
 assert row(a)['wall']['count']==1 and row(a)['wall']['dropped']==1 and a._active==0
 clocks(monkeypatch);a._wall[0]=(t.MAX_VALUE,t.MAX_VALUE,None,None,0,0,False)
 with t.authority_phase(a,P):pass
 assert row(a)['wall']['saturated'] and row(a)['wall']['count']==t.MAX_VALUE

@pytest.mark.parametrize('value',[None,False,object()])
def test_disabled_or_mutated_wrong_type_reads_no_clocks(monkeypatch,value):
 def fail():raise AssertionError('diagnostic clock called')
 monkeypatch.setattr(t,'_WALL',fail);monkeypatch.setattr(t,'_CPU',fail)
 class Owner:
  authority_timing=value
  @t.authority_timed(P)
  def operation(self):return False
 assert Owner().operation() is False

@pytest.mark.parametrize('fault',['getattr','allocation','finish','malformed_storage'])
def test_diagnostic_fault_preserves_return_and_original_error(monkeypatch,fault):
 a=t.AuthorityPhaseTimings();original=ValueError('original')
 class Owner:
  authority_timing=a
  @t.authority_timed(P)
  def operation(self,fail):
   if fail:raise original
   return False
 if fault=='getattr':Owner.authority_timing=property(lambda self:(_ for _ in ()).throw(MemoryError()))
 if fault=='allocation':monkeypatch.setattr(t,'_Measurement',lambda *args:(_ for _ in ()).throw(MemoryError()))
 if fault=='finish':monkeypatch.setattr(t.AuthorityPhaseTimings,'_finish',lambda *args:(_ for _ in ()).throw(MemoryError()))
 if fault=='malformed_storage':a._wall=[];a._cpu=[]
 assert Owner().operation(False) is False
 with pytest.raises(ValueError)as caught:Owner().operation(True)
 assert caught.value is original and a._active==0

def test_diagnostic_cancellation_rules(monkeypatch):
 a=t.AuthorityPhaseTimings();original=ValueError('body')
 def cancel():raise KeyboardInterrupt()
 monkeypatch.setattr(t,'_WALL',cancel)
 with pytest.raises(KeyboardInterrupt):
  with t.authority_phase(a,P):raise AssertionError('must not enter')
 assert a._active==0
 clocks(monkeypatch);a=t.AuthorityPhaseTimings()
 with pytest.raises(ValueError)as caught:
  with t.authority_phase(a,P):
   monkeypatch.setattr(t,'_WALL',cancel);raise original
 assert caught.value is original and a._active==0
 clocks(monkeypatch);a=t.AuthorityPhaseTimings()
 with pytest.raises(KeyboardInterrupt):
  with t.authority_phase(a,P):monkeypatch.setattr(t,'_WALL',cancel)
 assert a._active==0

def test_bindings_restore_deleted_or_preexisting_attributes():
 a=t.AuthorityPhaseTimings();old=object();x=SimpleNamespace(authority_timing=old);y=SimpleNamespace()
 b=t.AuthorityTimingBindings(a);b.bind(x);b.bind(y);del y.authority_timing;b.restore()
 assert x.authority_timing is old and not hasattr(y,'authority_timing') and not b.failed

@pytest.mark.parametrize('when',['bind','restore'])
def test_binding_faults_are_best_effort_and_explicit(when):
 class State(dict):
  fail=False
  def __setitem__(self,key,value):
   if self.fail:raise MemoryError()
   return super().__setitem__(key,value)
  def pop(self,*args):
   if self.fail:raise MemoryError()
   return super().pop(*args)
 state=State();obj=type('Owner',(),{})();obj.__dict__=state;a=t.AuthorityPhaseTimings();b=t.AuthorityTimingBindings(a)
 if when=='bind':state.fail=True
 b.bind(obj)
 if when=='restore':state.fail=True
 b.restore();assert b.failed and a.snapshot()['binding_errors']>0

def test_invalid_explicit_api_refuses():
 with pytest.raises(ValueError):t.AuthorityTimingBindings(object())
 with pytest.raises(ValueError):t.authority_phase(None,'unbounded-label')

def test_maximum_schema_saturated_all_channels():
 a=t.AuthorityPhaseTimings();maximum=(t.MAX_VALUE,)*6+(True,)
 a._wall=[maximum for _ in t.AuthorityPhase]
 if a._cpu is not None:a._cpu=[maximum for _ in t.AuthorityPhase]
 a._binding_errors=t.MAX_VALUE;a._active=(1<<len(t.AuthorityPhase))-1
 raw=json.dumps(a.snapshot(),sort_keys=True,separators=(',',':')).encode()
 assert len(raw)<8192
 print('maximum authority profile bytes',len(raw))


def test_borrowed_prior_collector_restored_without_getattr_setattr_callbacks():
 old=object();owner=type('Owner',(),{})();owner.authority_timing=old
 a=t.AuthorityPhaseTimings();b=t.AuthorityTimingBindings(a);b.bind(owner)
 assert owner.authority_timing is a
 b.restore();assert owner.authority_timing is old


def test_explicit_phase_allocation_failure_is_best_effort(monkeypatch):
 a=t.AuthorityPhaseTimings();executed=[]
 monkeypatch.setattr(t,'_Measurement',lambda *args:(_ for _ in ()).throw(MemoryError()))
 with t.authority_phase(a,P):executed.append(True)
 assert executed==[True] and a._active==0 and row(a)['wall']['dropped']==1
