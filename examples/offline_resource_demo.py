# SPDX-License-Identifier: Apache-2.0
"""Build, exchange and verify a signed public resource without hardware.

Run from a checkout after installing Meshticulum:
    python examples/offline_resource_demo.py

The publisher key is disposable and local to this example. No socket or device
is opened, and the temporary pack is removed on exit.
"""

import tempfile
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from meshticulum.resources import knowledge_pack as kp
from meshticulum.resources import lamplighter as ll
from meshticulum.resources import resource as mr


def main():
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
        if verified["text"] != "A small public reference.":
            raise ValueError("verified chunk text differs from the fixture")
        print(f"Verified public chunk: {len(raw_chunk)} bytes")


if __name__ == "__main__":
    main()
