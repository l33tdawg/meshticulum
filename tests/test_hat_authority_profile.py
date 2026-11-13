import sys
from meshticulum.bench.authority_timing import AuthorityPhaseTimings
from tests.test_hat_activity_wait import rig

def test_exact_live_owner_authorize_and_clock_calls_unchanged(rig):
 chip,worker,journal,state=rig
 codes={worker._clock.__code__:'clock',worker.authorize.__code__:'authorize',worker._dispatch.__code__:'dispatch'}
 def capture(enabled):
  calls=[];worker.authority_timing=AuthorityPhaseTimings()if enabled else None
  def profile(frame,event,arg):
   if event=='call'and frame.f_code in codes:calls.append(codes[frame.f_code])
  old=sys.getprofile()
  try:
   sys.setprofile(profile);result=worker._live()
  finally:sys.setprofile(old)
  return result,calls,list(chip.commands),journal.snapshot()
 assert capture(False)==capture(True)
