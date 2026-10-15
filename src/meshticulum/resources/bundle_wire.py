"""Opt-in reversible transport for existing signed public byte-bundle chunks.

MB1 carries raw file bytes and a padding length, then reconstructs the exact
original canonical JSON chunk before the existing manifest-entry verifier.
Manifest bytes, signatures, chunk identities and quarantine/resume formats do
not change. No MR1 default, request kind, admission, cipher or flow is changed.
The caller must explicitly bind the compact wire length/hash in its carrier
grant and supply an entry from an independently verified publisher manifest.
"""
import base64
from copy import deepcopy
import json
import struct

from . import knowledge_pack as kp, lamplighter as ll

MAGIC=b'MB1\0'
HEADER=struct.Struct('>4sHHH')
REMOVED=frozenset(('payload_base64','wire_padding'))
_FIELD_BYTES=len(kp.canonical_json({name:'' for name in REMOVED}))-1


def _json(raw):
    def unique(pairs):
        result={}
        for key,value in pairs:
            kp.require(key not in result,'duplicate compact bundle metadata key')
            result[key]=value
        return result
    try:return json.loads(raw,object_pairs_hook=unique)
    except (ValueError,UnicodeError,RecursionError):
        raise ValueError('invalid compact bundle metadata') from None


def encode_chunk(raw,entry):
    """Opt in to MB1 for a canonical chunk matching an authenticated entry.

    The caller authenticates the publisher manifest and binds this transport
    representation's length and hash in its carrier grant.
    """
    kp.require(type(raw) is bytes and 0<len(raw)<=kp.BUNDLE_CHUNK_BYTES,
               'invalid compact bundle input size')
    record=ll.chunk(raw,entry)
    kp.require('bundle_part' in record,'compact transport requires a byte-bundle part')
    part=record['bundle_part'];payload=kp.decode_bundle_part(part)
    padding=len(part['wire_padding'])
    metadata=deepcopy(record)
    for name in REMOVED:metadata['bundle_part'].pop(name)
    encoded=kp.canonical_json(metadata)
    wire=HEADER.pack(MAGIC,len(encoded),len(payload),padding)+encoded+payload
    kp.require(len(wire)<=kp.BUNDLE_CHUNK_BYTES,'compact bundle exceeds resource limit')
    return wire


def decode_chunk(wire,entry):
    """Reconstruct a canonical chunk matching an authenticated manifest entry.

    MB1 is explicit opt-in; legacy canonical JSON is also accepted. The caller
    must authenticate the publisher manifest before supplying its entry. This
    function verifies the original chunk identity, not the manifest signature
    or a transfer's replay, expiry or whole-file state; the existing receiver
    performs those checks on the returned bytes. All reconstruction lengths
    are bounded, and no transport hash substitutes for those verifications.
    """
    kp.require(type(wire) is bytes and 0<len(wire)<=kp.BUNDLE_CHUNK_BYTES,
               'invalid compact bundle wire size')
    if not wire.startswith(MAGIC):
        record=ll.chunk(wire,entry)
        kp.require('bundle_part' in record,'compact transport requires a byte-bundle part')
        return wire
    kp.require(len(wire)>=HEADER.size,'short compact bundle header')
    magic,metadata_bytes,payload_bytes,padding_bytes=HEADER.unpack_from(wire)
    kp.require(magic==MAGIC and metadata_bytes>0 and 1<=payload_bytes<=kp.BUNDLE_PART_BYTES
               and padding_bytes<=kp.BUNDLE_CHUNK_BYTES
               and HEADER.size+metadata_bytes+payload_bytes==len(wire),
               'compact bundle length mismatch')
    canonical_size=metadata_bytes+((payload_bytes+2)//3)*4+padding_bytes+_FIELD_BYTES+1
    kp.require(canonical_size<=kp.BUNDLE_CHUNK_BYTES and type(entry) is dict
               and type(entry.get('bytes')) is int and canonical_size==entry['bytes'],
               'compact bundle reconstructed size mismatch')
    encoded=wire[HEADER.size:HEADER.size+metadata_bytes]
    record=_json(encoded)
    kp.require(type(record) is dict and type(record.get('bundle_part')) is dict
               and set(record['bundle_part'])==kp.BUNDLE_PART_FIELDS-REMOVED
               and kp.canonical_json(record)==encoded,'noncanonical compact bundle metadata')
    part=record['bundle_part'];payload=wire[HEADER.size+metadata_bytes:]
    part['payload_base64']=base64.b64encode(payload).decode('ascii')
    part['wire_padding']='_'*padding_bytes
    raw=kp.chunk_bytes(record)
    kp.require(len(raw)==canonical_size,'compact bundle reconstruction changed')
    ll.chunk(raw,entry)
    return raw
