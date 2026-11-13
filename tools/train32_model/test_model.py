import unittest
from model import admissible,simulate,Turn,DEPTH
class ModelTests(unittest.TestCase):
 def test_exact_native_budget_equality_and_depth(self):
  e=35920;c=e+105000
  self.assertTrue(admissible(now=100,original_deadline=100+3*c+5000,pending_estimates=[e,e],candidate_estimate=e,next_start=5100))
  self.assertFalse(admissible(now=100,original_deadline=99+3*c+5000,pending_estimates=[e,e],candidate_estimate=e,next_start=5100))
  self.assertFalse(admissible(now=0,original_deadline=2000000,pending_estimates=[e]*DEPTH,candidate_estimate=e,next_start=0))
 def test_static_three_normal_feasible_without_empty_tail(self):
  for delay in (6000,12000,24000,30000):
   for service in (44200,44800,47027):
    r=simulate(delivery_delay=delay,service=service,static_depth=3)
    with self.subTest(delay=delay,service=service):
     self.assertEqual(r['sent'],32);self.assertFalse(r['empty_midturn']);self.assertEqual(r['refused'],0)
     self.assertLess(r['completed_at'],r['original_deadline'])
     self.assertLessEqual(max(x['pending'] for x in r['rows']),3)
     for previous,nxt in zip(r['rows'],r['rows'][1:]):self.assertLess(nxt['arrival'],previous['finish'])
 def test_initial_reverse_hold_and_cooldown_fit(self):
  r=simulate(delivery_delay=24000,static_depth=3,initial_next_start=100000,cooldown_bound=100000)
  self.assertEqual(r['sent'],32);self.assertFalse(r['empty_midturn']);self.assertEqual(r['refused'],0)
  for previous,nxt in zip(r['rows'],r['rows'][1:]):self.assertLess(nxt['arrival'],previous['finish'])
 def test_pause_cannot_pretend_contiguous_train(self):
  for delay in (40000,50000,100000,250000):
   r=simulate(delivery_delay=delay,static_depth=3)
   self.assertLess(r['sent'],32);self.assertTrue(r['empty_midturn']);self.assertEqual(r['original_deadline'],1980000)
 def test_slow_service_refuses_original_headroom(self):
  r=simulate(service=70000,static_depth=3)
  self.assertLess(r['sent'],32);self.assertEqual(r['refused'],1);self.assertEqual(r['original_deadline'],1980000)
 def test_empty_turn_latches_and_never_renews(self):
  t=Turn(1980000);t.offer();t.terminal()
  self.assertTrue(t.retired);self.assertEqual(t.original_deadline,1980000)
  with self.assertRaises(ValueError):t.offer()
  with self.assertRaises(ValueError):t.renew()
if __name__=='__main__':unittest.main()
