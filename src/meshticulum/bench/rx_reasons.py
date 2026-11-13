"""Fixed, optional per-worker software observations; never packet-loss claims."""
NAMES = ('irq_crc', 'irq_header', 'irq_crc_header', 'irq_timeout',
         'zero_length', 'packet_read_failure', 'physical_packet_returned',
         'poll_failure', 'rearm_attempt', 'rearm_failure', 'receive_failure')
LIMIT = 2**32 - 1


class ReceiveReasons:
    def __init__(self):
        self._counts = [0] * len(NAMES)
        self._saturated = 0

    def record(self, name):
        index = NAMES.index(name)
        if self._counts[index] == LIMIT:
            self._saturated |= 1 << index
        else:
            self._counts[index] += 1

    def snapshot(self):
        return dict(schema='meshticulum.hat-receive-reasons.v1',
                    scope='worker-lifetime-software-observations',
                    packet_loss_attribution=False, overlapping_counters=True,
                    rearm_scope='HATWorker.receive only; excludes construction and transmit restoration',
                    physical_packet_scope='driver return before worker rearm and authentication; not accepted DATA',
                    saturated_mask=self._saturated,
                    counts=dict(zip(NAMES, self._counts)))
