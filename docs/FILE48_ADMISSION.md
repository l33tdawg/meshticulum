# Explicit singleton 48KiB file admission

`radio_file --file48` selects exactly one 49,152-byte object. It is mutually exclusive with `--file64`; ordinary 16KiB chunks and the exact 65,536-byte mode remain unchanged. The public manifest schema is `meshticulum.example-radio-file48.v1`, and its only chunk has index zero and a chunk hash equal to the whole-file hash.

A matching bench grant must carry `meshticulum.file48-capability.v1` with exact `max_object_bytes: 49152`, `max_total_frames: 512`, and the SHA256 of the exact manifest bytes. It requires explicit operation limits and rejects service grants, other sizes, malformed scalar types and mismatched manifest pins. The capability is part of the immutable route/admission binding. This increases neither physical attempt budgets nor original deadlines.

`VerifiedFile48` stores verified bytes under a separate `file48-v1` namespace. Its reads, writes and assembly require the exact singleton descriptor and whole hash. Neither the ordinary chunk cache nor the file64 cache accepts this descriptor. The public hardware loan also binds the chosen mode before acquisition.

An encrypted source-model transfer using the real serializer, RunAuthority, journal and public FileTransfer produces 255 DATA frames plus one confirmation CONTROL frame without loss. Tests use a sender limit of290 attempts and receiver192, and retain the original finite expiry on loss/failure paths. These are source-model counts, not measured target delivery or permission for RF. The wire format, frozen flow, receive guards and firmware are unchanged. The composed checked-rearm option remains independently default off.
