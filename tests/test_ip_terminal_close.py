"""A real TCP EOF must not erase a preceding authenticated final receipt."""
import time

from meshticulum.bench.services import ServiceTransfer, resource
from tests.test_ip_transport import pair


def test_final_authenticated_receipt_before_tcp_eof_completes_sender(tmp_path):
    payload = resource.encode_request('/packs/' + 'a' * 64 + '/manifest.json', 0, 32)
    with pair(tmp_path / 'terminal', payload) as (flows, workers, stores, clock):
        ServiceTransfer(flows[0]).send(payload)
        for _ in range(500):
            flows[0].step(); flows[1].step()
            if flows[1].flow.complete:
                break
            time.sleep(.001)
        assert flows[1].flow.complete and not flows[0].flow.complete
        time.sleep(.01)
        flows[1].step()
        assert workers[1].journal.snapshot()['lanes']['ACK'] >= 2
        workers[1].close()  # Final ACK and FIN are both queued for the sender.
        assert flows[0].step() is True
        assert flows[0].flow.complete and not flows[0].closed
        assert not workers[0].journal.snapshot()['fenced']
