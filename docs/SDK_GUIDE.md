# Python SDK guide

The published experimental alpha provides reusable Python libraries for authenticated transport, bounded resource exchange and offline encrypted envelopes. You can explore signed public resources without a radio, Lantern or the SAGE SDK. Native deployment and protected SAGE integration belong to the separate [Lantern repository](https://github.com/l33tdawg/sage-lantern).

The [public radio examples](../examples/README.md) have two fresh five-part 100 kB measurements at **1.95–2.03 kB/s including sender startup**. A separate clean installed-wheel check completed another 100 kB file at **1.74 kB/s**, including startup and a five-second pause between verified parts. All used a direct near-bench GFSK250 path. [The report](RADIO_SDK_MEASUREMENTS.md) gives the exact packages, conditions and limits. The offline walkthrough below has its own scope.

## Install the alpha or checkout

Use Python **3.11 or later**. Package version **`1.0.0a1`** is published as the [`v1.0.0-alpha` GitHub prerelease](https://github.com/l33tdawg/meshticulum/releases/tag/v1.0.0-alpha). Download the wheel from that release page and install the local file, or use the source-checkout commands below.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install ./meshticulum-1.0.0a1-py3-none-any.whl
```

The release also includes `SHA256SUMS`, the normalized source archive, the firmware source-build guide and the public proof records. Prebuilt firmware is not included.

```sh
git clone https://github.com/l33tdawg/meshticulum.git
cd meshticulum
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

Check that Python resolves the installed package, then display the public CLI:

```sh
python -I -c "from importlib.metadata import version; import meshticulum; print(version('meshticulum')); print(meshticulum.__file__)"
meshticulum-seed --help
```

The [installed-wheel record](../provenance/PUBLIC_INSTALLED_SDK_PROOF.json) used normal virtual environments on Linux aarch64 with Python 3.11.2 and 3.14.4, hash-pinned dependency wheels and all 67 runtime files checked against release source `673eb60`. The package dependencies are `cryptography` and `PyNaCl`, with the supported ranges in [pyproject.toml](../pyproject.toml). For development, install the optional `dev` extra in editable mode to run the standalone tests:

```sh
python -m pip install -e '.[dev]'
python -m pytest
python tools/verify_frozen_flow.py
```

## Verify a signed resource entirely offline

Save the following as `offline_resource_demo.py` and run `python offline_resource_demo.py`. It creates a temporary public pack, signs its manifest with an in-memory test key, exchanges MR1 blocks through a local callable, and verifies the returned chunk. It opens no socket or device.

```python
import tempfile
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from meshticulum.resources import knowledge_pack as kp
from meshticulum.resources import lamplighter as ll
from meshticulum.resources import resource as mr

key = Ed25519PrivateKey.generate()
public = kp.public_key_hex(key)
trust = {
    "schema": "sage.knowledge-pack.trust-root.v1",
    "keys": [{
        "key_id": kp.key_id_for(public),
        "algorithm": "ed25519",
        "public_key_hex": public,
        "status": "active",
        "created_at": "2026-10-11T00:00:00Z",
    }],
}
chunk = kp.build_chunk(
    "demo", "A small public reference.",
    title="SDK example", licence="CC0-1.0",
    source="Local SDK example", date="2026-10-11",
)
manifest = kp.build_manifest(
    pack_id="sdk-demo", version=1, pack_class="reference",
    title="SDK demo", licence="CC0-1.0",
    provenance={"source": "Local SDK example"},
    created_at="2026-10-11T00:00:00Z",
    chunks=[chunk], embedding_model="none", embedding_dimension=1,
)
raw_manifest = kp.manifest_bytes(manifest)
digest = kp.manifest_digest(manifest)

with tempfile.TemporaryDirectory() as temporary:
    directory = Path(temporary) / "pack"
    kp.write_pack(
        directory, manifest, {chunk["sha256"]: chunk},
        kp.sign_manifest(raw_manifest, key),
    )
    cache = ll.VerifiedCache(directory, trust, digest)
    server = mr.BlockServer(cache.read)

    def exchange(request, response_limit, deadline):
        if time.monotonic() >= deadline:
            raise TimeoutError("original deadline expired")
        reply = server.handle(request)
        if len(reply) > response_limit:
            raise ValueError("response exceeds caller bound")
        return reply

    reader = mr.BlockReader(exchange, max_block=512)
    path = f"/packs/{digest}/chunks/{chunk['sha256']}.json"
    raw_chunk = reader(path, 65536, time.monotonic() + 10)
    verified = ll.chunk(raw_chunk, manifest["chunks"][0])
    kp.validate_pack_rules(manifest, [verified])
    assert verified["text"] == "A small public reference."
    print(f"Verified public chunk: {len(raw_chunk)} bytes")
```

The key is a disposable example publisher, not an enrolled network identity. Applications pin their actual publisher/trust root independently. `VerifiedCache` verifies the signed manifest and rechecks piece hashes; an availability claim alone cannot establish trustworthy content.

## Resource APIs

| API | Purpose |
| --- | --- |
| `kp.build_chunk(...)`, `kp.build_manifest(...)` | Build bounded, canonical public knowledge records and their manifest. |
| `kp.sign_manifest(raw, key)` | Sign the exact manifest bytes; verification also checks the configured trust root and key status. |
| `ll.VerifiedCache(directory, trust, digest)` | Read or seed only locally verified public pieces. |
| `mr.encode_request(path, offset, size)` | Encode one compact MR1 resource request. |
| `mr.BlockServer(read).handle(request)` | Serve an explicitly supplied public-resource allowlist. |
| `mr.BlockReader(exchange, max_block=512)(path, limit, deadline)` | Read a bounded resource through the caller's exchange, under one absolute monotonic deadline. |

`BlockReader` is **callable**; it has no `get()` or `read()` method. Its constructor defaults to **1,024 bytes per block**; the default codec permits an explicitly chosen block size up to **16,384 bytes**. The optional `public32=True` codec accepts up to **32,740 block bytes**, leaving room for the 28-byte response header in a 32 KiB response. Both ends and the underlying flow must explicitly admit that capability. It does not widen an existing grant. These resource caps differ from the **255-byte radio packet**: an admitted reliable flow fragments data into packets.

The source also includes [byte bundles](../src/meshticulum/resources/byte_bundle.py), [bundle transport](../src/meshticulum/resources/bundle_transport.py) and an [opaque MPF2 relay](../src/meshticulum/privacy/forward_relay.py). These are experimental library components; native qualification remains separate. See [the transport source update](TRANSPORT_SOURCE_UPDATE.md) for their scope.

The reader can reconstruct a host-side batch wrapper to reuse the resource verifier. That does not authorize arbitrary files or carry an HTTP server inside the radio.

The optional public resource command exposes its arguments without starting a server:

```sh
python -m meshticulum.resources.lamplighter --help
```

Serving HTTP, fetching from peers and binding a radio/TCP carrier require deliberate application setup. See [IP transport](IP_TRANSPORT.md) and [architecture](ARCHITECTURE.md).

## Connect a real carrier

The offline `exchange()` is a test callable. A real carrier composition must supply independently authorized peers and keys, exact object descriptors, current grants/consent, original epoch and monotonic deadlines, exclusive ports, durable counters/journals and bounded cleanup.

`ServiceTransfer` binds one admitted object to a concrete flow. `PreparedBlockExchange` uses separately admitted request and reply flows. The public `ServiceRunner` refuses protected message jobs; a private application must provide its own policy and storage composition. A later session uses fresh nonce/counter state and retains earlier attempt debt.

The library does not discover or enroll peers, generate device grants, open a radio or start a SAGE service on import. Follow [device setup](DEVICE_SETUP.md) for the hardware boundary and [SAGE integration](SAGE_INTEGRATION.md) for the one-way **Lantern → Meshticulum** dependency.

## Security and qualification

Public-resource signatures establish publisher/content trust; authenticated carrier encryption is a separate layer. The current transport uses provisioned pairwise secrets and has no proved forward secrecy. The offline recipient envelope similarly uses a long-term recipient key. [The comparison](COMPARISON.md#security-properties-and-gaps) states these gaps explicitly.

This walkthrough proves local API and content-verification behavior. It is not a hardware throughput result, native private admission or deployment certification. [Performance](PERFORMANCE.md) and [release readiness](RELEASE_READINESS.md) keep those evidence boundaries separate.
