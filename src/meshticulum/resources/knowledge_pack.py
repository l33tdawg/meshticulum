#!/usr/bin/env python3
"""SAGE knowledge pack v2: content-addressed chunks behind a signed manifest.

Prototype format. OPEN-DST-001 (canonical pack encoding, chunking, compression
and POUF) is undecided, so this layout is non-normative and may change before
the G2 interop freeze. What it does fix, for our own pipeline:

* DST-001 - a pack manifest is immutable, content-addressed and signed.
* SYS-008 - a carrier is trusted for transport only. Every chunk is verified
  against the manifest, so a relay is never a trust input.

Canonical bytes are a JCS-shaped subset of RFC 8785 rather than full JCS:
object keys are ASCII only, so Python's ``sorted()`` matches JCS UTF-16 key
order, and no floating-point number ever reaches a hashed or signed structure
(embedding vectors travel as base64 float32). Those are the two places where a
Python canonical-JSON implementation diverges from RFC 8785 in practice, and
both are excluded rather than approximated.

Layout of one pack directory::

    <pack>/manifest.json            canonical manifest, the signed root
    <pack>/manifest.sig.json        detached ed25519 signature over manifest.json
    <pack>/chunks/<sha256>.json     one chunk record per content address
    <pack>/delta-v1-to-v2.json      optional per-chunk delta against a parent

A chunk record carries its own content address; the address covers the
curatorial content (id, title, text, tags, source, date, confidence) and
deliberately not the embedding, so re-embedding with a better model does not
change chunk identity. Embedding provenance lives in the manifest. A pack also
carries a class - ``reference`` (curated facts) or ``brief`` (dated third-party
material) - which fixes how its chunks enter a node: a brief is an observation
below the fact confidence and its answers carry the day and the source.
"""

from __future__ import annotations

import base64
import contextlib
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import struct
import tarfile
import tempfile


SCHEMA_CHUNK = "sage.knowledge-pack.chunk.v2"
SCHEMA_MANIFEST = "sage.knowledge-pack.manifest.v2"
SCHEMA_DELTA = "sage.knowledge-pack.delta.v2"
SCHEMA_SIGNATURE = "sage.knowledge-pack.signature.v1"
SCHEMA_TRUST_ROOT = "sage.knowledge-pack.trust-root.v1"

CHUNK_CONTENT_FIELDS = ("id", "title", "text", "tags", "source", "date", "confidence_bp",
                        "licence")
CHUNK_RECORD_FIELDS = set(CHUNK_CONTENT_FIELDS) | {"schema", "sha256", "embedding"}
MANIFEST_FIELDS = {"schema", "pack", "embedding", "chunks", "totals", "parent"}
PACK_FIELDS = {"id", "version", "class", "title", "licence", "provenance", "created_at"}
PROVENANCE_FIELDS = {"source", "curator", "url", "note"}
EMBEDDING_FIELDS = {"model", "dimension", "normalized", "embedded_chunks"}
MANIFEST_CHUNK_FIELDS = {"id", "sha256", "bytes", "title", "tags"}
TOTALS_FIELDS = {"chunks", "text_bytes", "chunk_bytes", "embedded_chunks"}
DELTA_FIELDS = {"schema", "pack_id", "from_version", "to_version", "from_manifest_sha256",
                "to_manifest_sha256", "added", "removed", "unchanged", "added_bytes"}
SIGNATURE_FIELDS = {"schema", "algorithm", "key_id", "public_key_hex", "manifest_sha256",
                    "signature_base64"}
TRUST_ROOT_FIELDS = {"schema", "note", "keys"}
TRUST_KEY_FIELDS = {"key_id", "algorithm", "public_key_hex", "status", "created_at", "note"}
KEY_STATUSES = ("active", "retired", "revoked")
VECTOR_FIELDS = {"model", "dimension", "vector_f32_le"}

PACK_ID = re.compile(r"[a-z0-9][a-z0-9.-]{0,63}\Z")
CHUNK_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX_KEY = re.compile(r"[0-9a-f]{64}\Z")
DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}\Z")

# A pack has a class because reference material and dated news are different
# claims. A reference pack is curated fact at the fact confidence (9500 bp); a
# brief is third-party dated material that can be wrong, so it is an observation
# at 8000 bp and MUST stay below the fact band - a rolling brief that enters the
# node as a 0.95 fact would be asserting the morning's news as settled truth.
PACK_CLASSES = ("reference", "brief")
REFERENCE_CONFIDENCE_BP = 9500
BRIEF_CONFIDENCE_BP = 8000
MAX_CONFIDENCE_BP = 10000
BRIEF_MAX_CONFIDENCE_BP = REFERENCE_CONFIDENCE_BP - 1

# A pack licence is the umbrella declaration; a chunk licence is what that item
# actually is. A uniform pack names its licence once and every chunk repeats it.
# A pack that mixes sources - CC BY-SA article text beside CC0 structured data -
# must declare ``mixed``, and then every chunk has to name its own licence; the
# builder refuses an item that would otherwise inherit a licence it may not have.
MIXED_LICENCE = "mixed"

# Prototype chunk sizing: retrieval carries whole chunks, so a chunk has to stay
# inside a comfortable context slice. Raise it only with retrieval evidence.
MAX_TEXT_CHARS = 4000
MAX_CHUNKS = 200000


class PackFormatError(ValueError):
    """The pack does not match the v2 prototype format, or fails verification."""


def require(condition, message):
    if not condition:
        raise PackFormatError(message)


def _reject_floats(value, where="canonical value"):
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        raise PackFormatError(f"{where} may not contain a float; integers, strings, "
                              "booleans, null and containers of those only")
    if isinstance(value, list):
        for item in value:
            _reject_floats(item, where)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            require(isinstance(key, str) and key.isascii(),
                    f"{where} keys must be ASCII strings")
            _reject_floats(item, where)
        return
    raise PackFormatError(f"{where} contains an unsupported value of type {type(value).__name__}")


def canonical_json(value) -> bytes:
    """Canonical bytes for hashing and signing (JCS-shaped ASCII-key subset)."""
    _reject_floats(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
                      allow_nan=False).encode("utf-8")


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def content_object(chunk: dict) -> dict:
    return {field: chunk[field] for field in CHUNK_CONTENT_FIELDS}


def content_address(chunk: dict) -> str:
    return sha256_hex(canonical_json(content_object(chunk)))


def encode_vector(values) -> str:
    require(isinstance(values, (list, tuple)) and values, "embedding vector must be a non-empty list")
    packed = bytearray()
    for value in values:
        require(isinstance(value, (int, float)) and not isinstance(value, bool),
                "embedding vector entries must be numbers")
        number = float(value)
        require(number == number and number not in (float("inf"), float("-inf")),
                "embedding vector entries must be finite")
        packed += struct.pack("<f", number)
    return base64.b64encode(bytes(packed)).decode("ascii")


def decode_vector(text: str, dimension: int) -> list[float]:
    require(isinstance(text, str), "encoded vector must be a string")
    try:
        raw = base64.b64decode(text, validate=True)
    except (ValueError, TypeError) as error:
        raise PackFormatError("encoded vector is not canonical base64") from error
    require(base64.b64encode(raw).decode("ascii") == text,
            "encoded vector is not canonical base64")
    require(len(raw) == dimension * 4,
            f"encoded vector is {len(raw)} bytes, expected {dimension * 4}")
    return list(struct.unpack(f"<{dimension}f", raw))


def build_chunk(identifier: str, text: str, *, title: str = "", tags=(), source: str = "",
                date: str = "", confidence_bp: int = REFERENCE_CONFIDENCE_BP, licence: str = "",
                embedding=None, embedding_model=None) -> dict:
    record = {
        "schema": SCHEMA_CHUNK,
        "id": identifier,
        "title": title,
        "text": text,
        "tags": sorted({tag for tag in tags if tag}),
        "source": source,
        "date": date,
        "confidence_bp": confidence_bp,
        "licence": licence,
    }
    record["sha256"] = content_address(record)
    if embedding is not None:
        require(embedding_model, "an embedding needs its model name")
        record["embedding"] = {"model": embedding_model, "dimension": len(embedding),
                               "vector_f32_le": encode_vector(embedding)}
    validate_chunk(record)
    return record


def chunk_bytes(record: dict) -> bytes:
    return canonical_json(record) + b"\n"


def validate_chunk(record) -> None:
    require(isinstance(record, dict), "chunk record must be an object")
    require(set(record) in (CHUNK_RECORD_FIELDS, CHUNK_RECORD_FIELDS - {"embedding"}),
            f"chunk record fields must be {sorted(CHUNK_RECORD_FIELDS)}, with embedding optional")
    require(record["schema"] == SCHEMA_CHUNK, f"chunk schema must be {SCHEMA_CHUNK}")
    require(CHUNK_ID.match(record["id"] or ""), "chunk id must be a lowercase slug")
    require(isinstance(record["text"], str) and record["text"].strip(),
            f"chunk {record['id']} has empty text")
    require(len(record["text"]) <= MAX_TEXT_CHARS,
            f"chunk {record['id']} is {len(record['text'])} characters, over the "
            f"{MAX_TEXT_CHARS}-character prototype limit")
    for field in ("title", "source", "date"):
        require(isinstance(record[field], str), f"chunk {record['id']} field {field} must be a string")
    require(isinstance(record["tags"], list) and all(isinstance(tag, str) and tag
            for tag in record["tags"]) and record["tags"] == sorted(set(record["tags"])),
            f"chunk {record['id']} tags must be a sorted list of unique non-empty strings")
    require(isinstance(record["confidence_bp"], int) and not isinstance(record["confidence_bp"], bool)
            and 1 <= record["confidence_bp"] <= MAX_CONFIDENCE_BP,
            f"chunk {record['id']} confidence_bp must be an integer in 1..{MAX_CONFIDENCE_BP}")
    require(isinstance(record["licence"], str) and record["licence"].strip(),
            f"chunk {record['id']} needs a licence; an item whose licence is unknown "
            "is not distributable")
    require(record["licence"] != MIXED_LICENCE,
            f"chunk {record['id']} must name its own concrete licence, not the pack-level "
            f"{MIXED_LICENCE!r} marker")
    require(HEX64.match(record["sha256"] or ""), "chunk sha256 must be lowercase hex")
    require(record["sha256"] == content_address(record),
            f"chunk {record['id']} content address does not match its content")
    if "embedding" in record:
        embedding = record["embedding"]
        require(isinstance(embedding, dict) and set(embedding) == VECTOR_FIELDS,
                f"chunk {record['id']} embedding block fields must be {sorted(VECTOR_FIELDS)}")
        require(isinstance(embedding["model"], str) and embedding["model"],
                f"chunk {record['id']} embedding model must be a non-empty string")
        require(isinstance(embedding["dimension"], int) and not isinstance(embedding["dimension"], bool)
                and 1 <= embedding["dimension"] <= 8192,
                f"chunk {record['id']} embedding dimension is out of range")
        decode_vector(embedding["vector_f32_le"], embedding["dimension"])


def validate_pack_rules(manifest: dict, records) -> None:
    """Rules that need the manifest and the chunk records together.

    ``records`` is any iterable of chunk records (a list while building, the
    loaded dict values afterwards).
    """
    for record in records:
        if manifest["pack"]["class"] == "brief":
            require(record["confidence_bp"] <= BRIEF_MAX_CONFIDENCE_BP,
                    f"brief chunk {record['id']} carries confidence "
                    f"{record['confidence_bp'] / 10000:g}; a brief is never a fact - "
                    "keep it below 0.95")
            require(DATE_ONLY.match(record["date"] or ""),
                    f"brief chunk {record['id']} needs a YYYY-MM-DD date; news without a day "
                    "cannot be answered 'as of'")
            require(record["source"].strip(),
                    f"brief chunk {record['id']} needs a source; news without attribution "
                    "cannot be answered 'per'")
        pack_licence = manifest["pack"]["licence"]
        if pack_licence != MIXED_LICENCE:
            require(record["licence"] == pack_licence,
                    f"chunk {record['id']} is licensed {record['licence']} but the pack "
                    f"declares {pack_licence}; declare the pack licence {MIXED_LICENCE!r} "
                    "to carry per-item licences")


def build_manifest(*, pack_id: str, version: int, pack_class: str, title: str, licence: str,
                   provenance: dict, created_at: str, chunks, embedding_model: str,
                   embedding_dimension: int, parent=None) -> dict:
    ordered = sorted(chunks, key=lambda record: record["id"])
    require(len({record["sha256"] for record in ordered}) == len(ordered),
            "two chunks share a content address; de-duplicate the source")
    require(len({record["id"] for record in ordered}) == len(ordered),
            "two chunks share an id")
    embedded = sum(1 for record in ordered if "embedding" in record)
    for record in ordered:
        if "embedding" in record:
            require(record["embedding"]["model"] == embedding_model
                    and record["embedding"]["dimension"] == embedding_dimension,
                    f"chunk {record['id']} embedding provenance contradicts the manifest")
    manifest = {
        "schema": SCHEMA_MANIFEST,
        "pack": {"id": pack_id, "version": version, "class": pack_class, "title": title,
                 "licence": licence, "provenance": provenance, "created_at": created_at},
        "embedding": {"model": embedding_model, "dimension": embedding_dimension,
                      "normalized": True, "embedded_chunks": embedded},
        "chunks": [{"id": record["id"], "sha256": record["sha256"],
                    "bytes": len(chunk_bytes(record)), "title": record["title"],
                    "tags": record["tags"]} for record in ordered],
        "totals": {"chunks": len(ordered),
                   "text_bytes": sum(len(record["text"].encode("utf-8")) for record in ordered),
                   "chunk_bytes": sum(len(chunk_bytes(record)) for record in ordered),
                   "embedded_chunks": embedded},
        "parent": parent,
    }
    validate_manifest(manifest)
    validate_pack_rules(manifest, ordered)
    return manifest


def manifest_bytes(manifest: dict) -> bytes:
    return canonical_json(manifest) + b"\n"


def manifest_digest(manifest: dict) -> str:
    """Digest of the manifest exactly as stored: canonical bytes plus the newline.

    One definition only. The signature document, a parent link and a delta all
    name the manifest by this value, so it has to be the file's digest and not
    the digest of a re-serialisation that happens to differ by a trailing byte.
    """
    return sha256_hex(manifest_bytes(manifest))


def validate_manifest(manifest) -> None:
    require(isinstance(manifest, dict) and set(manifest) == MANIFEST_FIELDS,
            f"manifest fields must be {sorted(MANIFEST_FIELDS)}")
    require(manifest["schema"] == SCHEMA_MANIFEST, f"manifest schema must be {SCHEMA_MANIFEST}")

    pack = manifest["pack"]
    require(isinstance(pack, dict) and set(pack) == PACK_FIELDS,
            f"manifest pack fields must be {sorted(PACK_FIELDS)}")
    require(PACK_ID.match(pack["id"] or ""), "pack id must be a lowercase slug")
    require(isinstance(pack["version"], int) and not isinstance(pack["version"], bool)
            and pack["version"] >= 1, "pack version must be an integer >= 1")
    require(pack["class"] in PACK_CLASSES,
            f"pack class must be one of {list(PACK_CLASSES)}")
    require(isinstance(pack["title"], str) and pack["title"], "pack title must be non-empty")
    require(isinstance(pack["licence"], str) and pack["licence"],
            "pack licence must be non-empty; a pack without provenance is not distributable")
    require(isinstance(pack["created_at"], str) and re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z",
            pack["created_at"]), "pack created_at must be an RFC3339 UTC instant")
    provenance = pack["provenance"]
    require(isinstance(provenance, dict) and set(provenance) <= PROVENANCE_FIELDS
            and provenance.get("source"), "pack provenance must name its source")
    require(all(isinstance(value, str) and value for value in provenance.values()),
            "pack provenance values must be non-empty strings")

    embedding = manifest["embedding"]
    require(isinstance(embedding, dict) and set(embedding) == EMBEDDING_FIELDS,
            f"manifest embedding fields must be {sorted(EMBEDDING_FIELDS)}")
    require(isinstance(embedding["model"], str) and embedding["model"],
            "manifest embedding model must be non-empty")
    require(isinstance(embedding["dimension"], int) and not isinstance(embedding["dimension"], bool)
            and 1 <= embedding["dimension"] <= 8192, "manifest embedding dimension is out of range")
    require(isinstance(embedding["normalized"], bool), "manifest normalized must be a boolean")

    chunks = manifest["chunks"]
    require(isinstance(chunks, list) and 0 < len(chunks) <= MAX_CHUNKS,
            f"manifest must list 1..{MAX_CHUNKS} chunks")
    seen_ids, seen_hashes = set(), set()
    for entry in chunks:
        require(isinstance(entry, dict) and set(entry) == MANIFEST_CHUNK_FIELDS,
                f"manifest chunk fields must be {sorted(MANIFEST_CHUNK_FIELDS)}")
        require(CHUNK_ID.match(entry["id"] or ""), "manifest chunk id must be a lowercase slug")
        require(HEX64.match(entry["sha256"] or ""), "manifest chunk sha256 must be lowercase hex")
        require(isinstance(entry["bytes"], int) and entry["bytes"] > 0,
                f"manifest chunk {entry['id']} bytes must be positive")
        require(isinstance(entry["tags"], list) and all(isinstance(tag, str) for tag in entry["tags"]),
                f"manifest chunk {entry['id']} tags must be a list of strings")
        require(entry["id"] not in seen_ids and entry["sha256"] not in seen_hashes,
                f"manifest repeats chunk {entry['id']}")
        seen_ids.add(entry["id"])
        seen_hashes.add(entry["sha256"])
    require([entry["id"] for entry in chunks] == sorted(entry["id"] for entry in chunks),
            "manifest chunks must be sorted by id")

    totals = manifest["totals"]
    require(isinstance(totals, dict) and set(totals) == TOTALS_FIELDS,
            f"manifest totals fields must be {sorted(TOTALS_FIELDS)}")
    require(totals["chunks"] == len(chunks), "manifest totals.chunks contradicts the chunk list")
    require(totals["chunk_bytes"] == sum(entry["bytes"] for entry in chunks),
            "manifest totals.chunk_bytes contradicts the chunk list")
    require(isinstance(totals["text_bytes"], int) and totals["text_bytes"] > 0,
            "manifest totals.text_bytes must be positive")
    require(totals["embedded_chunks"] == embedding["embedded_chunks"]
            and 0 <= totals["embedded_chunks"] <= len(chunks),
            "manifest embedded_chunks contradicts the chunk list")

    parent = manifest["parent"]
    if parent is not None:
        require(isinstance(parent, dict) and set(parent) == {"version", "manifest_sha256"},
                "manifest parent must be null or {version, manifest_sha256}")
        require(isinstance(parent["version"], int) and parent["version"] >= 1
                and parent["version"] < pack["version"],
                "manifest parent version must be a lower integer")
        require(HEX64.match(parent["manifest_sha256"] or ""),
                "manifest parent digest must be lowercase hex")


def delta_document(old_manifest: dict, new_manifest: dict) -> dict:
    """Per-chunk delta between two manifests of the same pack."""
    validate_manifest(old_manifest)
    validate_manifest(new_manifest)
    require(old_manifest["pack"]["id"] == new_manifest["pack"]["id"],
            "a delta needs two versions of the same pack")
    old = {entry["sha256"]: entry for entry in old_manifest["chunks"]}
    new = {entry["sha256"]: entry for entry in new_manifest["chunks"]}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    return {
        "schema": SCHEMA_DELTA,
        "pack_id": old_manifest["pack"]["id"],
        "from_version": old_manifest["pack"]["version"],
        "to_version": new_manifest["pack"]["version"],
        "from_manifest_sha256": manifest_digest(old_manifest),
        "to_manifest_sha256": manifest_digest(new_manifest),
        "added": added,
        "removed": removed,
        "unchanged": len(set(old) & set(new)),
        "added_bytes": sum(new[digest]["bytes"] for digest in added),
    }


def validate_delta(document) -> None:
    require(isinstance(document, dict) and set(document) == DELTA_FIELDS,
            f"delta fields must be {sorted(DELTA_FIELDS)}")
    require(document["schema"] == SCHEMA_DELTA, f"delta schema must be {SCHEMA_DELTA}")
    require(PACK_ID.match(document["pack_id"] or ""), "delta pack id must be a lowercase slug")
    require(isinstance(document["from_version"], int) and document["from_version"] >= 1
            and isinstance(document["to_version"], int)
            and document["to_version"] > document["from_version"],
            "delta versions must increase")
    for field in ("from_manifest_sha256", "to_manifest_sha256"):
        require(HEX64.match(document[field] or ""), f"delta {field} must be lowercase hex")
    for field in ("added", "removed"):
        require(isinstance(document[field], list)
                and all(HEX64.match(digest or "") for digest in document[field])
                and document[field] == sorted(set(document[field])),
                f"delta {field} must be a sorted list of unique content addresses")
    require(set(document["added"]).isdisjoint(document["removed"]),
            "a chunk cannot be both added and removed")
    require(isinstance(document["unchanged"], int) and document["unchanged"] >= 0,
            "delta unchanged must be a non-negative integer")
    require(isinstance(document["added_bytes"], int) and document["added_bytes"] >= 0,
            "delta added_bytes must be a non-negative integer")


# --- signatures -------------------------------------------------------------

def load_signing_key(path: Path):
    """Load an ed25519 private key from a raw-hex file or a PEM file."""
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives import serialization

    raw = Path(path).read_bytes()
    text = raw.decode("ascii", "ignore").strip()
    if "BEGIN" in text:
        key = serialization.load_pem_private_key(raw, password=None)
        require(isinstance(key, ed25519.Ed25519PrivateKey), "signing key must be ed25519")
        return key
    require(HEX_KEY.match(text), "signing key file must be 32-byte lowercase hex or an ed25519 PEM")
    return ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(text))


def public_key_hex(private_key) -> str:
    from cryptography.hazmat.primitives import serialization

    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw).hex()


def key_id_for(public_hex: str) -> str:
    """Self-certifying key id: ``pk-`` plus the first 16 hex of the key's own digest.

    A key id that is derived from the key cannot be mismatched with it by
    accident, which is the whole point: the trust root names keys by id, and the
    id names the key back.
    """
    require(HEX_KEY.match(public_hex or ""), "public key must be 32-byte lowercase hex")
    return "pk-" + sha256_hex(bytes.fromhex(public_hex))[:16]


def sign_manifest(raw: bytes, private_key) -> dict:
    signature = private_key.sign(raw)
    public_hex = public_key_hex(private_key)
    return {
        "schema": SCHEMA_SIGNATURE,
        "algorithm": "ed25519",
        "key_id": key_id_for(public_hex),
        "public_key_hex": public_hex,
        "manifest_sha256": sha256_hex(raw),
        "signature_base64": base64.b64encode(signature).decode("ascii"),
    }


def verify_manifest_signature(raw: bytes, document, *, trust_root: dict | None = None,
                              trusted_public_hex: str | None = None) -> dict:
    """Verify a detached signature and place its signer in the trust model.

    Cryptographic validity is not authorization: a signature by a revoked key
    verifies and is still refused. Old packs signed by a retired key stay valid,
    which is what makes rotation possible without re-signing the archive.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric import ed25519

    require(isinstance(document, dict) and set(document) == SIGNATURE_FIELDS,
            f"signature fields must be {sorted(SIGNATURE_FIELDS)}")
    require(document["schema"] == SCHEMA_SIGNATURE, f"signature schema must be {SCHEMA_SIGNATURE}")
    require(document["algorithm"] == "ed25519", "signature algorithm must be ed25519")
    require(HEX_KEY.match(document["public_key_hex"] or ""), "signature public key must be 32-byte hex")
    require(document["key_id"] == key_id_for(document["public_key_hex"]),
            "signature key id does not match the key it carries")
    require(document["manifest_sha256"] == sha256_hex(raw),
            "signature covers a different manifest than the one present")
    try:
        key = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(document["public_key_hex"]))
    except ValueError as error:
        raise PackFormatError("signature public key is not a valid ed25519 key") from error
    try:
        key.verify(base64.b64decode(document["signature_base64"], validate=True), raw)
    except (InvalidSignature, ValueError, TypeError) as error:
        raise PackFormatError("manifest signature does not verify") from error

    status = None
    if trust_root is not None:
        entry = trust_root_entry(trust_root, document["key_id"])
        require(entry["public_key_hex"] == document["public_key_hex"],
                f"trust root entry {entry['key_id']} carries a different public key")
        require(entry["status"] != "revoked",
                f"manifest is signed by {entry['key_id']}, which the trust root marks revoked")
        status = entry["status"]
    if trusted_public_hex is not None:
        require(HEX_KEY.match(trusted_public_hex or ""), "trusted public key must be 32-byte hex")
        require(document["public_key_hex"] == trusted_public_hex,
                f"manifest is signed by {document['public_key_hex']}, not the trusted key")
    return {"key_id": document["key_id"], "public_key_hex": document["public_key_hex"],
            "key_status": status}


# --- trust root -------------------------------------------------------------

def validate_trust_root(document) -> None:
    require(isinstance(document, dict) and set(document) == TRUST_ROOT_FIELDS,
            f"trust root fields must be {sorted(TRUST_ROOT_FIELDS)}")
    require(document["schema"] == SCHEMA_TRUST_ROOT, f"trust root schema must be {SCHEMA_TRUST_ROOT}")
    require(isinstance(document["note"], str), "trust root note must be a string")
    keys = document["keys"]
    require(isinstance(keys, list), "trust root keys must be a list")
    seen_ids, seen_keys = set(), set()
    for entry in keys:
        require(isinstance(entry, dict) and set(entry) == TRUST_KEY_FIELDS,
                f"trust root key fields must be {sorted(TRUST_KEY_FIELDS)}")
        require(entry["algorithm"] == "ed25519", "trust root keys must be ed25519")
        require(HEX_KEY.match(entry["public_key_hex"] or ""),
                "trust root public keys must be 32-byte lowercase hex")
        require(entry["key_id"] == key_id_for(entry["public_key_hex"]),
                f"trust root key id {entry['key_id']} is not derived from its public key")
        require(entry["status"] in KEY_STATUSES,
                f"trust root key status must be one of {list(KEY_STATUSES)}")
        require(re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z", entry["created_at"] or ""),
                "trust root keys need an RFC3339 UTC created_at")
        require(isinstance(entry["note"], str), "trust root key note must be a string")
        require(entry["key_id"] not in seen_ids and entry["public_key_hex"] not in seen_keys,
                f"trust root repeats key {entry['key_id']}")
        seen_ids.add(entry["key_id"])
        seen_keys.add(entry["public_key_hex"])


def load_trust_root(path) -> dict:
    """Load the reviewed key list. A malformed root refuses; it never degrades to empty."""
    try:
        document = json.loads(Path(path).read_text("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PackFormatError(f"trust root {path} is unreadable: {error}") from error
    validate_trust_root(document)
    return document


def trust_root_entry(root: dict, identifier: str) -> dict:
    for entry in root["keys"]:
        if entry["key_id"] == identifier:
            return entry
    known = ", ".join(entry["key_id"] for entry in root["keys"]) or "none"
    raise PackFormatError(
        f"manifest is signed by {identifier}, which is not in the trust root (known keys: {known})")


def active_key_ids(root: dict) -> list[str]:
    return [entry["key_id"] for entry in root["keys"] if entry["status"] == "active"]


# --- pack directories -------------------------------------------------------

class Pack:
    """A loaded, verified pack directory."""

    def __init__(self, directory: Path, manifest: dict, raw: bytes, chunks: dict,
                 signature: dict | None, trust: dict | None):
        self.directory = Path(directory)
        self.manifest = manifest
        self.raw = raw
        self.chunks = chunks
        self.signature = signature
        self.trust = trust

    @property
    def signer(self) -> str | None:
        return self.trust["public_key_hex"] if self.trust else None

    @property
    def key_id(self) -> str | None:
        return self.trust["key_id"] if self.trust else None

    @property
    def key_status(self) -> str | None:
        return self.trust["key_status"] if self.trust else None

    @property
    def pack_id(self) -> str:
        return self.manifest["pack"]["id"]

    @property
    def version(self) -> int:
        return self.manifest["pack"]["version"]

    @property
    def pack_class(self) -> str:
        return self.manifest["pack"]["class"]

    @property
    def tag(self) -> str:
        return f"pack:{self.pack_id}-v{self.version}"

    def chunk_tag(self, digest: str) -> str:
        return f"chunk:{digest}"

    def vector(self, digest: str):
        record = self.chunks[digest]
        if "embedding" not in record:
            return None
        embedding = record["embedding"]
        if embedding["model"] != self.manifest["embedding"]["model"] \
                or embedding["dimension"] != self.manifest["embedding"]["dimension"]:
            return None
        return decode_vector(embedding["vector_f32_le"], embedding["dimension"])

    def summary(self) -> dict:
        return {"pack": self.pack_id, "version": self.version, "chunks": len(self.chunks),
                "class": self.pack_class,
                "text_bytes": self.manifest["totals"]["text_bytes"],
                "chunk_bytes": self.manifest["totals"]["chunk_bytes"],
                "embedded_chunks": self.manifest["totals"]["embedded_chunks"],
                "manifest_sha256": sha256_hex(self.raw), "signed": self.signature is not None,
                "key_id": self.key_id, "key_status": self.key_status, "signer": self.signer,
                "licence": self.manifest["pack"]["licence"]}


def load_pack(directory, *, trust_root: dict | None = None, trusted_public_hex: str | None = None,
              allow_unsigned: bool = False) -> Pack:
    """Verify a pack directory and return it. Every check is fail-closed."""
    directory = Path(directory)
    manifest_path = directory / "manifest.json"
    require(manifest_path.is_file(), f"{manifest_path} is missing")
    raw = manifest_path.read_bytes()
    require(raw.endswith(b"\n"), "manifest.json must end with a newline")
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PackFormatError(f"manifest.json is not valid UTF-8 JSON: {error}") from error
    validate_manifest(manifest)
    require(manifest_bytes(manifest) == raw,
            "manifest.json is not in canonical form; re-run the builder instead of editing it")

    signature_path = directory / "manifest.sig.json"
    signature = trust = None
    if signature_path.is_file():
        payload = signature_path.read_bytes()
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PackFormatError(f"manifest.sig.json is not valid UTF-8 JSON: {error}") from error
        require(canonical_json(document) + b"\n" == payload,
                "manifest.sig.json is not in canonical form")
        trust = verify_manifest_signature(raw, document, trust_root=trust_root,
                                          trusted_public_hex=trusted_public_hex)
        signature = document
    else:
        require(allow_unsigned, f"{signature_path} is missing and unsigned packs are not accepted here")
        require(trusted_public_hex is None, "a trusted public key was supplied but the pack is unsigned")

    store = directory / "chunks"
    require(store.is_dir(), f"chunk store {store} is missing")
    chunks = {}
    for entry in manifest["chunks"]:
        path = store / f"{entry['sha256']}.json"
        require(path.is_file(), f"chunk file {path.name} is missing")
        payload = path.read_bytes()
        require(payload.endswith(b"\n"), f"chunk file {path.name} must end with a newline")
        try:
            record = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PackFormatError(f"chunk file {path.name} is not valid UTF-8 JSON: {error}") from error
        validate_chunk(record)
        require(chunk_bytes(record) == payload,
                f"chunk file {path.name} is not in canonical form")
        require(record["sha256"] == entry["sha256"], f"chunk file {path.name} is misnamed")
        require(content_address(record) == entry["sha256"],
                f"chunk file {path.name} does not hash to its name")
        require(record["id"] == entry["id"] and record["title"] == entry["title"]
                and record["tags"] == entry["tags"],
                f"chunk file {path.name} contradicts the manifest")
        require(len(payload) == entry["bytes"],
                f"chunk file {path.name} is {len(payload)} bytes, manifest says {entry['bytes']}")
        chunks[entry["sha256"]] = record
    present = {path.name[:-len(".json")] for path in store.glob("*.json")}
    stray = sorted(present - set(chunks))
    if stray:
        raise PackFormatError(
            f"chunk store holds {len(stray)} file(s) the manifest does not list "
            f"(first: {stray[0]}); build into a fresh directory rather than reusing one")
    validate_pack_rules(manifest, chunks.values())
    return Pack(directory, manifest, raw, chunks, signature, trust)


def load_delta(path) -> dict:
    document = json.loads(Path(path).read_text("utf-8"))
    validate_delta(document)
    return document


def delta_filename(from_version: int, to_version: int) -> str:
    return f"delta-v{from_version}-to-v{to_version}.json"


def find_pack_delta(directory, *, to_version: int | None = None) -> dict | None:
    """The delta document a pack directory ships, if any.

    A delta is a convenience for peers, never an authority: it is not covered by
    the manifest signature, so the roll path recomputes the removed set from the
    two manifests and only uses this document as a cross-check.
    """
    documents = []
    for path in sorted(Path(directory).glob("delta-*.json")):
        try:
            document = json.loads(path.read_text("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PackFormatError(f"delta {path} is unreadable: {error}") from error
        validate_delta(document)
        if to_version is None or document["to_version"] == to_version:
            documents.append((path, document))
    require(len(documents) <= 1,
            f"{directory} holds {len(documents)} delta documents for version {to_version}; "
            "a pack has at most one delta per target version")
    return documents[0][1] if documents else None


def check_delta_matches(document: dict, computed: dict) -> None:
    """A shipped delta may only repeat what the two manifests already say."""
    for field in ("pack_id", "from_version", "to_version", "from_manifest_sha256",
                  "to_manifest_sha256", "added", "removed", "unchanged", "added_bytes"):
        require(document[field] == computed[field],
                f"delta {field} contradicts the manifests it claims to describe")


ARCHIVE_SUFFIX = ".tar.gz"
# Prototype guards on a delivery archive. The 1-10 GB class is not supported yet;
# these exist so a hostile or corrupt archive cannot fill the disk anyway.
MAX_ARCHIVE_MEMBERS = 400000
MAX_ARCHIVE_BYTES = 16 * 1024 ** 3


def write_archive(directory, destination) -> dict:
    """Deterministic ``.tar.gz`` of a pack directory - the unit a release ships.

    Fixed mtimes, ids, owners, modes and member order, so the same pack produces
    the same archive bytes and the archive digest is a stable release identifier
    rather than a build timestamp.
    """
    directory, destination = Path(directory), Path(destination)
    require(destination.is_absolute() or destination.parent != Path(""),
            "an archive needs a destination path")
    require(not destination.resolve().is_relative_to(directory.resolve()),
            "the archive must live outside the pack directory, which holds only verified pack bytes")
    buffer, members = io.BytesIO(), 0
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for path in sorted(directory.rglob("*")):
            relative = path.relative_to(directory).as_posix()
            info = tarfile.TarInfo(relative)
            info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
            if path.is_dir():
                info.type, info.mode, info.size = tarfile.DIRTYPE, 0o755, 0
                archive.addfile(info)
            else:
                payload = path.read_bytes()
                info.type, info.mode, info.size = tarfile.REGTYPE, 0o644, len(payload)
                archive.addfile(info, io.BytesIO(payload))
            members += 1
    raw = gzip.compress(buffer.getvalue(), compresslevel=9, mtime=0)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    return {"archive": str(destination), "bytes": len(raw), "sha256": sha256_hex(raw),
            "members": members}


def _extract_pack_archive(source: Path, destination: Path) -> None:
    """Extract a delivery archive without ever trusting a member name or type."""
    with tarfile.open(source, "r:gz") as archive:
        members = archive.getmembers()
        require(len(members) <= MAX_ARCHIVE_MEMBERS,
                f"archive holds {len(members)} members, over the {MAX_ARCHIVE_MEMBERS} prototype limit")
        total = 0
        for member in members:
            parts = Path(member.name).parts
            require(parts and not Path(member.name).is_absolute() and ".." not in parts,
                    f"archive member {member.name} escapes the pack root")
            require(member.isdir() or member.isreg(),
                    f"archive member {member.name} is neither a file nor a directory")
            total += member.size
            require(total <= MAX_ARCHIVE_BYTES,
                    f"archive expands past the {MAX_ARCHIVE_BYTES}-byte prototype limit")
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = archive.extractfile(member)
            require(payload is not None, f"archive member {member.name} has no content")
            with payload, open(target, "wb") as handle:
                shutil.copyfileobj(payload, handle, 1024 * 1024)


@contextlib.contextmanager
def pack_directory(source):
    """Yield a pack directory for a directory or a ``.tar.gz`` delivery archive."""
    source = Path(source)
    if source.is_dir():
        yield source
        return
    require(source.is_file(), f"{source} is neither a pack directory nor a {ARCHIVE_SUFFIX} archive")
    temporary = tempfile.mkdtemp(prefix="sage-pack-")
    try:
        _extract_pack_archive(source, Path(temporary))
        root = Path(temporary)
        if not (root / "manifest.json").is_file():
            entries = sorted(root.iterdir())
            require(len(entries) == 1 and entries[0].is_dir(),
                    "the archive must hold the pack at its root or inside exactly one directory")
            root = entries[0]
        yield root
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def write_pack(directory, manifest: dict, chunks, signature: dict | None = None,
               delta: dict | None = None, delta_name: str | None = None) -> None:
    """Write a verified pack directory. Callers pass already-validated records."""
    directory = Path(directory)
    store = directory / "chunks"
    store.mkdir(parents=True, exist_ok=True)
    written = 0
    for digest, record in sorted(chunks.items()):
        path = store / f"{digest}.json"
        payload = chunk_bytes(record)
        if path.exists():
            require(path.read_bytes() == payload, f"{path} exists with different bytes")
            continue
        path.write_bytes(payload)
        written += 1
    raw = manifest_bytes(manifest)
    manifest_path = directory / "manifest.json"
    manifest_path.write_bytes(raw)
    if signature is not None:
        (directory / "manifest.sig.json").write_text(
            canonical_json(signature).decode("utf-8") + "\n", "utf-8")
    if delta is not None:
        name = delta_name or f"delta-v{delta['from_version']}-to-v{delta['to_version']}.json"
        (directory / name).write_text(canonical_json(delta).decode("utf-8") + "\n", "utf-8")
    return written
