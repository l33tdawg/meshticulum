"""Pure native-clock admission model. No SDK/firmware behavior is enabled."""
from dataclasses import dataclass

DEPTH=8
TRAIN=32
HOLD=2_000_000
RX_GAP=5_000
START_GUARD=100_000
REVERSE=100_000


def admissible(*,now,original_deadline,pending_estimates,candidate_estimate,next_start):
 assert type(now) is int and 0<=now
 assert all(type(x) is int and x>0 for x in pending_estimates)
 assert type(candidate_estimate) is int and candidate_estimate>0
 if len(pending_estimates)>=DEPTH:return False
 needed=sum(x+START_GUARD+RX_GAP for x in (*pending_estimates,candidate_estimate))+max(0,next_start-now)
 return now<original_deadline and needed<=original_deadline-now


@dataclass
class Turn:
 original_deadline:int
 count:int=0
 pending:int=0
 retired:bool=False
 def offer(self):
  if self.retired or self.count>=TRAIN or self.pending>=DEPTH:raise ValueError('turn_not_available')
  self.count+=1;self.pending+=1
 def terminal(self):
  if not self.pending:raise ValueError('no_pending')
  self.pending-=1
  # A drained train cannot be promoted to another 2s deadline in-place.
  if not self.pending:self.retired=True
 def renew(self):raise ValueError('original_deadline_immutable')


def simulate(*,frames=32,airtime=35920,service=44800,delivery_delay=24000,margin=20000,static_depth=None,cooldown_bound=100000,initial_next_start=0):
 """Illustrative native-clock schedule, NOT an MCU/host rate proof.

 Fixed first deadline1.98s; service/deivery are test premises, never authority.
 Predict arrival at now+delivery_delay. Active packet retains full estimate.
 """
 now=0;deadline=HOLD-margin;done=[];sent=0;refused=0;empty=False;rows=[]
 while sent<frames:
  done=[x for x in done if x>now]
  # Trial future arrival sees conservative current pending, including any finishing in transit.
  arrival=now+delivery_delay
  limit=static_depth or DEPTH
  if sent and done and arrival>=done[-1]:
   empty=True;break
  has_slot=len(done)<limit
  fits=admissible(now=arrival,original_deadline=deadline,pending_estimates=[airtime]*len(done),candidate_estimate=airtime,next_start=arrival+cooldown_bound)
  if static_depth is not None and has_slot and not fits:
   refused+=1;break
  can=has_slot and fits
  if can:
   # service is start-to-start; RF_DONE precedes the preserved RX/setup gap.
   gap=8400
   finish=max(arrival,done[-1]+gap if done else initial_next_start)+(service-gap)
   if sent and not done:empty=True
   done.append(finish);sent+=1;rows.append(dict(index=sent,arrival=arrival,pending=len(done),finish=finish,deadline=deadline));now=arrival
  elif done:
   now=done[0]
   # Host should refill before last completion; reaching this boundary means stop, not renew.
   if len(done)==1:break
  else:refused+=1;break
 return dict(sent=sent,completed_at=done[-1] if done else now,original_deadline=deadline,empty_midturn=empty,refused=refused,rows=rows)
