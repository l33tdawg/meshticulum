# SPDX-License-Identifier: Apache-2.0
"""Normalize source-archive metadata without changing file bytes or modes."""
import argparse
import copy
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tarfile
import tempfile


def normalize(source, destination, epoch):
    source, destination = Path(source), Path(destination)
    if not 0 <= epoch <= 0xFFFFFFFF:
        raise ValueError('source_date_epoch_out_of_range')
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    with tarfile.open(source, 'r:gz') as incoming:
        members = incoming.getmembers()
        names = set()
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not name.parts:
                raise ValueError('unsafe_archive_path')
            canonical = str(name)
            if canonical in names:
                raise ValueError('duplicate_archive_path')
            names.add(canonical)
            if not (member.isfile() or member.isdir()):
                raise ValueError('unsupported_archive_member')
        fd, temporary = tempfile.mkstemp(prefix='.sdist-', dir=destination.parent)
        try:
            with os.fdopen(fd, 'wb') as raw:
                with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=epoch) as compressed:
                    with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as outgoing:
                        for member in sorted(members, key=lambda item: item.name):
                            item = copy.copy(member)
                            item.mtime, item.uid, item.gid = epoch, 0, 0
                            item.uname = item.gname = ''
                            item.pax_headers = {k: v for k, v in member.pax_headers.items()
                                if k not in ('mtime', 'atime', 'ctime', 'uid', 'gid', 'uname', 'gname')}
                            payload = incoming.extractfile(member) if member.isfile() else None
                            try:
                                outgoing.addfile(item, payload)
                            finally:
                                if payload is not None:
                                    payload.close()
                raw.flush()
                os.fsync(raw.fileno())
            # Publish only the complete local file and refuse an existing path.
            os.link(temporary, destination)
        finally:
            os.unlink(temporary)
    return {'source_date_epoch': epoch, 'members': len(members),
            'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'normalized_sha256': hashlib.sha256(destination.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--source-date-epoch', required=True, type=int)
    args = parser.parse_args()
    print(json.dumps(normalize(args.source, args.destination, args.source_date_epoch), sort_keys=True))


if __name__ == '__main__':
    main()
