"""Compact resource blocks for an already admitted request/reply carrier.

The exchange callable sends bytes and returns bounded bytes under the same
absolute deadline. It can be implemented by a radio flow, serial stream or IP
transport. This codec provides NO peer authentication, admission or RF owner;
those must already exist below it. No device or listener opens on import.

Small carriers move raw chunks in bounded blocks, never HTTP or base64. Host
translation to Lamplighter's batch API happens on either side of this boundary.
Each block is bound to the exact request; completed chunks still require the
independently pinned manifest and publisher verification in Lamplighter.
"""
import base64
import hashlib
import json
import re
import struct
import time

REQUEST = struct.Struct('>3sB32sIH')
RESPONSE = struct.Struct('>3s16sBII')
MAGIC = b'MR1'
MAX_BLOCK = 16384
MAX_RESOURCE = 8 * 1024 * 1024
RESOURCE = re.compile(r'/packs/([0-9a-f]{64})/(manifest\.json|manifest\.sig\.json|availability\.json|chunks/([0-9a-f]{64})\.json)\Z')
NAMES = {1: 'manifest.json', 2: 'manifest.sig.json', 3: 'availability.json'}


def require(value, message):
    if not value:
        raise ValueError(message)


def encode_request(path, offset, size):
    match = RESOURCE.fullmatch(path)
    require(match is not None, 'unsupported resource')
    require(type(offset) is int and 0 <= offset < MAX_RESOURCE
            and type(size) is int and 1 <= size <= MAX_BLOCK, 'invalid block range')
    digest, name, chunk = match.groups()
    kind = 4 if chunk else next(k for k, v in NAMES.items() if v == name)
    return REQUEST.pack(MAGIC, kind, bytes.fromhex(digest), offset, size) + (bytes.fromhex(chunk) if chunk else b'')


def decode_request(raw):
    require(type(raw) is bytes and len(raw) in (REQUEST.size, REQUEST.size + 32), 'invalid resource request size')
    magic, kind, digest, offset, size = REQUEST.unpack(raw[:REQUEST.size])
    require(magic == MAGIC and kind in (1, 2, 3, 4)
            and len(raw) == REQUEST.size + (32 if kind == 4 else 0), 'invalid resource request')
    require(offset < MAX_RESOURCE and 1 <= size <= MAX_BLOCK, 'invalid block range')
    name = 'chunks/' + raw[REQUEST.size:].hex() + '.json' if kind == 4 else NAMES[kind]
    return '/packs/' + digest.hex() + '/' + name, offset, size


class BlockServer:
    """Read from a caller-owned verified public-resource allowlist."""
    def __init__(self, read):
        require(callable(read), 'invalid resource store')
        self.read = read

    def handle(self, request):
        path, offset, size = decode_request(request)
        binding = hashlib.sha256(request).digest()[:16]
        try:
            raw = self.read(path)
            if raw is None:
                raise FileNotFoundError('missing resource')
            require(type(raw) is bytes and 0 < len(raw) <= MAX_RESOURCE and offset < len(raw),
                    'invalid resource range')
            return RESPONSE.pack(MAGIC, binding, 0, len(raw), offset) + raw[offset:offset + size]
        except (OSError, ValueError):
            return RESPONSE.pack(MAGIC, binding, 1, 0, offset)


class BlockReader:
    """Resource adapter over a bounded, deadline-enforcing exchange callable.

    max_block must fit the underlying admitted flow's receive allowance, not
    the PHY MTU. The underlying reliable flow performs packet fragmentation.
    Retries/budgets/cipher sessions remain that flow owner's responsibility.
    """
    def __init__(self, exchange, *, max_block=1024):
        require(callable(exchange) and type(max_block) is int and 1 <= max_block <= MAX_BLOCK,
                'invalid block adapter')
        self.exchange, self.max_block = exchange, max_block

    def _resource(self, path, limit, deadline):
        require(type(limit) is int and 1 <= limit <= MAX_RESOURCE, 'invalid resource limit')
        result, total = bytearray(), None
        while total is None or len(result) < total:
            if time.monotonic() >= deadline:
                raise TimeoutError('resource deadline expired')
            size = min(self.max_block, limit - len(result))
            request = encode_request(path, len(result), size)
            reply = self.exchange(request, RESPONSE.size + size, deadline)
            if time.monotonic() >= deadline:
                raise TimeoutError('resource deadline expired')
            require(type(reply) is bytes and RESPONSE.size <= len(reply) <= RESPONSE.size + size,
                    'invalid block response size')
            magic, binding, status, length, offset = RESPONSE.unpack(reply[:RESPONSE.size])
            require(magic == MAGIC and binding == hashlib.sha256(request).digest()[:16]
                    and offset == len(result), 'response binding mismatch')
            if status == 1:
                require(length == 0 and len(reply) == RESPONSE.size, 'invalid missing response')
                raise FileNotFoundError('resource unavailable')
            require(status == 0 and 0 < length <= limit and (total is None or length == total),
                    'resource size changed or exceeds limit')
            total = length
            block = reply[RESPONSE.size:]
            require(len(block) == min(size, total - len(result)), 'short or oversized resource block')
            result.extend(block)
        return bytes(result)

    def __call__(self, path, limit, deadline):
        # No JSON/base64 batch envelope crosses the scarce carrier. It is
        # reconstructed locally to reuse the existing whole-batch verifier.
        marker = '/chunks/batch/'
        if marker not in path:
            return self._resource(path, limit, deadline)
        base, names = path.split(marker)
        names = names.split(',')
        require(1 <= len(names) <= 32 and len(set(names)) == len(names)
                and all(re.fullmatch('[0-9a-f]{64}', n) for n in names), 'invalid batch names')
        output = {}
        for name in names:
            raw = self._resource(base + '/chunks/' + name + '.json', min(limit, 65536), deadline)
            output[name] = base64.b64encode(raw).decode('ascii')
            # Bound local expansion before fetching another resource.
            require(sum(len(k) + len(v) + 8 for k, v in output.items()) <= limit, 'batch exceeds limit')
        return json.dumps(output, sort_keys=True, separators=(',', ':')).encode('ascii')
