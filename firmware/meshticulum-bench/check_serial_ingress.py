# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Bounded disarmed USB ingress diagnostic. Sends only invalid-body STATUS.

No ARM or TRANSMIT message is constructed. Compare large, escaped frames
written in one burst with the same frame paced into USB packet-size chunks.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

PIN = '034722769952c4f48f6fd6c05aab268bbb49e5afe80e8013568bcb6096b5e003'
BUNDLE = Path('/opt/meshticulum-bench/runtime-c19364452c8d2fa4')


def main():
    assert os.geteuid() == 0 and sys.flags.isolated
    assert len(sys.argv) == 2 and sys.argv[1] in ('unit-a', 'unit-b')
    raw = (BUNDLE / 'manifest.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == PIN
    for name, pin in json.loads(raw)['files'].items():
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
        assert hashlib.sha256((BUNDLE / name).read_bytes()).hexdigest() == pin
    sys.path.insert(0, str(BUNDLE))
    from meshticulum.bench import node
    from meshticulum.bench.serial_protocol import Frame, Message
    node.roles(sys.argv[1])
    channel, worker, before = node._serial(sys.argv[1])
    rows = []
    try:
        for size, paced in ((70, False), (192, False), (255, False), (192, True), (255, True)):
            request = time.time_ns() // 1000
            data = Frame(Message.STATUS, worker.identity.boot, request, bytes([0xC0]) * size).encode()
            deadline = channel.monotonic() + 2.5
            result = {'payload_bytes': size, 'encoded_bytes': len(data), 'paced_64_bytes_10ms': paced}
            try:
                for start in range(0, len(data), 64 if paced else len(data)):
                    channel.write(data[start:start + (64 if paced else len(data))], deadline)
                    if paced: time.sleep(.01)
                reply = worker._wait(request, deadline)
                result.update(reply_type=reply.type.name, reply_body_hex=reply.body.hex())
                assert reply.type is Message.REFUSED
            except Exception as error:
                result['error'] = type(error).__name__ + ':' + str(error)
            rows.append(result)
        request = time.time_ns() // 1000
        deadline = channel.monotonic() + 2
        worker._send(Frame(Message.STATUS, worker.identity.boot, request), deadline)
        reply = worker._wait(request, deadline)
        assert reply.type is Message.STATUS_REPLY and len(reply.body) == 49 and reply.body[46:] == bytes(3)
        after = {'disarmed': True, 'attempts': int.from_bytes(reply.body[30:34], 'big'),
                 'reserved_airtime_us': int.from_bytes(reply.body[34:42], 'big')}
        assert after['attempts'] == before['boot_attempts']
        assert after['reserved_airtime_us'] == before['boot_reserved_estimated_airtime_us']
        print(json.dumps({'unit': sys.argv[1], 'before': before, 'after': after, 'trials': rows,
                          'arm_commands': 0, 'transmit_commands': 0}))
    finally:
        channel.close()


if __name__ == '__main__': main()
