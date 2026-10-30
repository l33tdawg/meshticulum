#!/usr/bin/env python3
"""Opt-in signed public byte-bundle/MB1 example. See examples/BUNDLE.md.

100,000 bytes use five existing 23,900-byte parts, not seven 16 KiB objects.
This adds no transport capability, grant issuer, RF permission or SDK policy.
"""
import argparse
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from meshticulum.bench.public_block import REPRESENTATION_MARKER, TRANSPORT_DESCRIPTOR_MARKER
from meshticulum.bench.services import ServiceTransfer
from meshticulum.bench.wire import require
from meshticulum.resources import byte_bundle, bundle_wire, knowledge_pack as kp
from meshticulum.resources import lamplighter, resource

import radio_file as base

FILE_WINDOW_HEADROOM_US = 300_000_000
MAX_BATCH_PARTS = (kp.BUNDLE_BYTES + byte_bundle.PART_BYTES - 1) // byte_bundle.PART_BYTES


def max_file_window_us(part_count):
    require(type(part_count) is int and part_count > 0, 'invalid_example_part_count')
    if part_count <= 5:
        return 240_000_000
    return part_count * base.ACK_GRACE_US + FILE_WINDOW_HEADROOM_US


def request_for(manifest_digest, entry, response_bytes):
    return resource.encode_request('/packs/' + manifest_digest + '/chunks/' + entry['sha256'] + '.json',
        0, response_bytes - resource.RESPONSE.size, public32=True)


def transport_pin(entry, response_bytes, response_sha256):
    return base.sha(kp.canonical_json(dict(chunk_content_address=entry['sha256'],
        response_bytes=response_bytes, response_sha256=response_sha256)))


def response_for(manifest_digest, entry, raw):
    wire = bundle_wire.encode_chunk(raw, entry)
    request = request_for(manifest_digest, entry, resource.RESPONSE.size + len(wire))
    expected_path, _, _ = resource.decode_request(request, public32=True)
    return resource.BlockServer(lambda path: wire if path == expected_path else None,
                                public32=True).handle(request)


def service_for(entry, response, expires):
    return dict(kind='knowledge', format='MR1.response', application_expires_epoch_us=expires,
        public_response_max_bytes=32768, **{REPRESENTATION_MARKER: 'MB1',
            TRANSPORT_DESCRIPTOR_MARKER: transport_pin(entry, len(response), base.sha(response))})


class BundleTransfer(base.ChunkTransfer):
    """Existing service and publisher verification inside the original grace."""
    def __init__(self, authority, worker, journal, cipher, entry, manifest_digest,
                 index, receiver, window, *, clock, confirm_receipt=False):
        super().__init__(authority, worker, journal, cipher, authority.plan['object'], self, clock=clock)
        self.service = ServiceTransfer(self.endpoint)
        self.entry, self.index, self.receiver, self.window = entry, index, receiver, window
        self.request = request_for(manifest_digest, entry, authority.plan['object']['bytes'])
        require(self.service.format == 'MR1.response' and self.service.public32
                and self.service.mb1_descriptor_sha256 == transport_pin(entry,
                    authority.plan['object']['bytes'], authority.plan['object']['sha256']),
                'example_bundle_transport_binding_mismatch')
        require(type(confirm_receipt) is bool, 'invalid_receipt_confirmation_option')
        if confirm_receipt:
            context = kp.canonical_json(dict(schema='meshticulum.bundle-receipt-context.v1',
                manifest=window.record['manifest'], part=index,
                issued_epoch_us=window.record['issued_epoch_us'],
                expires_epoch_us=window.record['expires_epoch_us']))
            self.enable_receipt_confirmation(base.hashlib.sha256(context).digest())

    def send(self, payload):
        self.service.send(payload)

    def commit(self, descriptor, payload):
        self.window.check()
        response = self.service.received()
        require(response == payload, 'example_bundle_response_changed')
        magic, binding, status, total, offset = resource.RESPONSE.unpack_from(response)
        wire = response[resource.RESPONSE.size:]
        require(magic == resource.MAGIC and binding == base.hashlib.sha256(self.request).digest()[:16]
                and (status, total, offset) == (0, len(wire), 0), 'example_bundle_response_binding')
        canonical = bundle_wire.decode_chunk(wire, self.entry)
        self.receiver.accept(self.index, canonical)
        self.window.check()


def load_bundle(args):
    raw = base.read_file(args.pack / 'manifest.json', 65536, pin=args.manifest_sha256)
    signature = base.read_file(args.pack / 'manifest.sig.json', 4096)
    trust = json.loads(base.read_file(args.trust_root, 65536, pin=args.trust_root_sha256))
    manifest = lamplighter.manifest(raw, signature, trust, args.manifest_sha256)
    expected = byte_bundle.descriptor(manifest)
    return raw, signature, trust, manifest, expected


def describe_part(args):
    _, _, _, manifest, expected = load_bundle(args)
    require(type(args.part) is int and 0 <= args.part < len(expected['parts']),
            'example_bundle_part_index')
    entry = manifest['chunks'][args.part]
    raw = base.read_file(args.pack / 'chunks' / (entry['sha256'] + '.json'), kp.BUNDLE_CHUNK_BYTES)
    response = response_for(args.manifest_sha256, entry, raw)
    return dict(object=dict(object_id=expected['sha256'], chunk_index=args.part,
                            bytes=len(response), sha256=base.sha(response)),
                service=service_for(entry, response, args.file_expires_epoch_us))


def sent_parts(state, count, manifest_sha256, window):
    """Read exact local peer-receipt markers; these do not authorize a new part."""
    verified = []
    for index in range(count):
        path = state / ('sent-' + str(index) + '.json')
        if path.exists():
            require(base.read_file(path, 4096, private=True) == kp.canonical_json(
                dict(manifest_sha256=manifest_sha256,
                     file_window_sha256=base.sha(window.raw), part=index)),
                'example_bundle_local_receipt_changed')
            verified.append(index)
    window.check()
    return tuple(verified)


def transfer_batch(part_args):
    """Run a bounded list of pre-admitted parts in this process, in caller order.

    Each option namespace has the same fields as ``transfer``. All parts share
    one file/window/device binding, but every part consumes its own pinned grant
    and creates, drains, disarms and retires fresh worker/journal/cipher state.
    OS ownership is held across the file, not the ARM or crypto session.
    A subset is permitted for verified-piece continuation; no missing part is
    silently skipped and list order need not match manifest order. Yielded
    ``{'part': index, 'result': ...}`` records follow successful cleanup. Any
    exception stops iteration before another grant is consumed. The iterator
    holds hardware between yields: consume it completely or call ``close()``
    in a ``finally`` block (or use ``contextlib.closing``) when stopping early.

    This function opens no management connection and issues no grants. Existing
    run_object/factory hooks can observe per-part entry and DATA; later calls are
    part invocations, not new process-start measurements.
    """
    require(type(part_args) in (list, tuple) and 0 < len(part_args) <= MAX_BATCH_PARTS,
            'example_bundle_batch_bound')
    parts = tuple(argparse.Namespace(**vars(args)) for args in part_args)
    first = parts[0]
    shared = ('pack', 'manifest_sha256', 'trust_root', 'trust_root_sha256', 'state',
        'file_issued_epoch_us', 'file_expires_epoch_us', 'unit', 'interface', 'device',
        'usb_serial', 'admission', 'admission_sha256', 'firmware_source',
        'firmware_source_sha256', 'profile_host', 'confirm_receipt')
    require(all(all(getattr(args, name, None) == getattr(first, name, None)
                    for name in shared) for args in parts),
            'example_bundle_batch_binding_changed')
    _, _, _, _, expected = load_bundle(first)
    indexes = tuple(args.part for args in parts)
    require(all(type(index) is int and 0 <= index < len(expected['parts'])
                for index in indexes) and len(set(indexes)) == len(indexes),
            'example_bundle_batch_part_indices')
    require(len({args.grant_directory for args in parts}) == len(parts),
            'example_bundle_batch_requires_fresh_grants')

    def run():
        with base.FileHardware(first) as hardware:
            for args in parts:
                yield dict(part=args.part, result=transfer(args, hardware=hardware))

    return run()


def transfer(args, *, hardware=None):
    """Consume one pinned part grant, optionally borrowing held file hardware."""
    require(hardware is None or type(hardware) is base.FileHardware,
            'example_requires_file_hardware')
    try:
        return _transfer(args, hardware=hardware)
    except BaseException as error:
        if hardware is not None:
            try:
                hardware.poison()
            except BaseException as cleanup:
                raise BaseExceptionGroup('Part transfer and hardware poison failed',
                                         [error, cleanup]) from None
        raise


def _transfer(args, *, hardware=None):
    raw_manifest, signature, trust, manifest, expected = load_bundle(args)
    count, index = len(expected['parts']), args.part
    require(type(index) is int and 0 <= index < count and count * base.ACK_GRACE_US
            < args.file_expires_epoch_us - args.file_issued_epoch_us,
            'example_bundle_cannot_fit_original_window')
    entry = manifest['chunks'][index]
    raw = base.read_file(args.grant_directory / 'grant.json', 16384,
                        pin=args.grant_sha256, private=True)
    plan = json.loads(raw)
    obj, service = plan['object'], plan['service']
    require(plan['schema'] == 'meshticulum.service-run.v1' and base.plan_modem(plan) in base.MODEMS
            and obj['object_id'] == expected['sha256'] and obj['chunk_index'] == index
            and resource.RESPONSE.size < obj['bytes'] <= 32768
            and plan['issued_epoch_us'] == args.file_issued_epoch_us
            and plan['expires_epoch_us'] == args.file_expires_epoch_us
            and service == dict(kind='knowledge', format='MR1.response',
                application_expires_epoch_us=args.file_expires_epoch_us,
                public_response_max_bytes=32768, **{REPRESENTATION_MARKER: 'MB1',
                    TRANSPORT_DESCRIPTOR_MARKER: transport_pin(entry, obj['bytes'], obj['sha256'])}),
            'example_requires_exact_bundle_service_grant')
    require(0 < args.file_expires_epoch_us - args.file_issued_epoch_us <= max_file_window_us(count),
            'example_file_window_bound')
    admission = json.loads(base.read_file(args.admission, 8192, pin=args.admission_sha256, private=True))
    for name in ('frequencies_hz', 'powers_dbm'):
        if name in admission:
            admission[name] = tuple(admission[name])
    if 'waveforms' in admission:
        admission['waveforms'] = tuple(tuple(row) for row in admission['waveforms'])
    admission = base.modem_classes(base.plan_modem(plan))[3](**admission)
    version = base.firmware_identity(args.firmware_source, args.firmware_source_sha256)
    args.state.mkdir(mode=0o700, exist_ok=True)
    info = args.state.lstat()
    require(base.stat.S_ISDIR(info.st_mode) and info.st_uid == base.os.geteuid()
            and base.stat.S_IMODE(info.st_mode) == 0o700, 'example_bundle_state_not_private')
    window = base.FileWindow(args.state, dict(sha256=args.manifest_sha256,
        bundle_sha256=expected['sha256'], parts=count),
        args.file_issued_epoch_us, args.file_expires_epoch_us,
        max_window_us=max_file_window_us(count))
    args.chunk = index  # Shared hardware receipt index, not a new transport mode.
    sends = plan['sender_unit'] == args.unit
    if sends:
        require(args.interface in ('hat', 'usb'), 'example_bundle_sender_requires_hat_or_usb')
        verified = sent_parts(args.state, count, args.manifest_sha256, window)
        window.require_capacity(max(1, count - len(verified)))
        payload = response_for(args.manifest_sha256, entry, base.read_file(
            args.pack / 'chunks' / (entry['sha256'] + '.json'), kp.BUNDLE_CHUNK_BYTES))
        require((len(payload), base.sha(payload)) == (obj['bytes'], obj['sha256']),
                'example_bundle_sender_object_mismatch')
        hardware_kw = {} if hardware is None else dict(hardware=hardware)
        result = base.run_object(args, raw, admission, version, payload, window,
            lambda a, w, j, c: BundleTransfer(a, w, j, c, entry, args.manifest_sha256,
                                            index, None, window, clock=base.clock,
                                            confirm_receipt=getattr(args, 'confirm_receipt', False)),
            **hardware_kw)
        window.check()
        receipt = kp.canonical_json(dict(manifest_sha256=args.manifest_sha256,
            file_window_sha256=base.sha(window.raw), part=index))
        receipt_path = args.state / ('sent-' + str(index) + '.json')
        if index in verified:
            require(base.read_file(receipt_path, 4096, private=True) == receipt,
                    'example_bundle_local_receipt_changed')
        else:
            base.publish(receipt_path, receipt)
        return result
    require(args.interface == 'usb', 'example_bundle_receiver_requires_usb')
    directory = args.state / 'receiver'
    directory.mkdir(mode=0o700, exist_ok=True)
    with byte_bundle.Receiver(directory, raw_manifest, signature, trust, args.manifest_sha256,
            expires_epoch_us=args.file_expires_epoch_us, clock=window.check, boot_reader=base.host_boot) as receiver:
        window.require_capacity(max(1, len(receiver.missing_parts())))
        hardware_kw = {} if hardware is None else dict(hardware=hardware)
        result = base.run_object(args, raw, admission, version, None, window,
            lambda a, w, j, c: BundleTransfer(a, w, j, c, entry, args.manifest_sha256,
                                            index, receiver, window, clock=base.clock,
                                            confirm_receipt=getattr(args, 'confirm_receipt', False)),
            **hardware_kw)
        window.check()
        if not receiver.missing_parts():
            result['whole_file'] = receiver.finish()
            result['whole_file_verified'] = True
            window.check()
        return result


def parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('build', help='create a signed public pack offline; grants no RF authority')
    build.add_argument('--input', required=True, type=Path)
    build.add_argument('--output', required=True, type=Path)
    build.add_argument('--signing-key', required=True, type=Path)
    build.add_argument('--pack-id', required=True)
    build.add_argument('--created-at', required=True)
    describe = commands.add_parser('describe', help='print protocol object/service metadata, not a grant')
    run = commands.add_parser('transfer', help='consume one approved MB1 grant; transmits DATA or ACK')
    for sub in (describe, run):
        sub.add_argument('--pack', required=True, type=Path)
        sub.add_argument('--manifest-sha256', required=True)
        sub.add_argument('--trust-root', required=True, type=Path)
        sub.add_argument('--trust-root-sha256', required=True)
        sub.add_argument('--part', required=True, type=int)
        sub.add_argument('--file-expires-epoch-us', required=True, type=int)
    run.add_argument('--confirm-receipt', action='store_true',
        help='send/accept an authenticated sender receipt to shorten ACK grace; loss keeps the original grace')
    run.add_argument('--file-issued-epoch-us', required=True, type=int)
    run.add_argument('--grant-directory', required=True, type=Path)
    run.add_argument('--grant-sha256', required=True)
    run.add_argument('--admission', required=True, type=Path)
    run.add_argument('--admission-sha256', required=True)
    base.add_operation_limit_arguments(run)
    base.add_host_timing_argument(run)
    run.add_argument('--firmware-source', required=True, type=Path)
    run.add_argument('--firmware-source-sha256', required=True)
    run.add_argument('--unit', required=True)
    run.add_argument('--interface', choices=('hat', 'usb'), required=True)
    run.add_argument('--device', type=Path)
    run.add_argument('--usb-serial')
    run.add_argument('--state', required=True, type=Path)
    return parser


def main():
    args = parser().parse_args()
    if args.command == 'build':
        key = base.read_file(args.signing_key, 65, private=True).strip().decode('ascii')
        require(base.re.fullmatch('[0-9a-f]{64}', key), 'example_requires_raw_ed25519_key')
        manifest, chunks, signature = byte_bundle.build(base.read_file(args.input, kp.BUNDLE_BYTES),
            pack_id=args.pack_id, created_at=args.created_at,
            private_key=Ed25519PrivateKey.from_private_bytes(bytes.fromhex(key)))
        args.output.mkdir(mode=0o700)
        kp.write_pack(args.output, manifest, chunks, signature)
        result = dict(manifest_sha256=kp.manifest_digest(manifest),
                      bundle=byte_bundle.descriptor(manifest), parts=len(manifest['chunks']))
    elif args.command == 'describe':
        result = describe_part(args)
    else:
        require(args.interface != 'usb' or (args.device is not None and args.usb_serial),
                'example_usb_identity_required')
        result = transfer(args)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
