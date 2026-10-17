"""Kernel socket identity remains valid after graceful TCP disconnection."""
import ctypes
import errno
from functools import lru_cache
import os
import socket
import struct
import sys


@lru_cache(maxsize=1)
def _darwin_reader():
    library=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True)
    reader=library.proc_pidfdinfo
    reader.argtypes=(ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_void_p,ctypes.c_int)
    reader.restype=ctypes.c_int
    return reader


def socket_identity(channel,info=None):
    info=os.fstat(channel.fileno()) if info is None else info
    if (info.st_dev,info.st_ino)!=(0,0):
        return ('stat',info.st_dev,info.st_ino)
    if sys.platform!='darwin' or struct.calcsize('P')!=8 or sys.byteorder!='little':
        raise OSError(errno.ENOTSUP,'kernel_socket_identity_unavailable')
    # Public 64-bit socket_fdinfo ABI from <sys/proc_info.h>. Check its exact
    # size and TCP type before using the opaque socket/PCB handles and generation.
    # Never fall back to a cached endpoint or the non-unique Darwin (0,0) stat.
    raw=ctypes.create_string_buffer(792)
    size=_darwin_reader()(os.getpid(),channel.fileno(),3,raw,len(raw))
    if size!=len(raw):
        raise OSError(errno.ENOTSUP,'kernel_socket_identity_unavailable')
    so,pcb,kind,protocol,family=struct.unpack_from('<QQiii',raw,160)
    tcp_kind=struct.unpack_from('<i',raw,256)[0]
    generation=struct.unpack_from('<Q',raw,272)[0]
    if not (so and pcb and generation and kind==socket.SOCK_STREAM
            and protocol==socket.IPPROTO_TCP and family==channel.family and tcp_kind==2):
        raise OSError(errno.ENOTSUP,'kernel_socket_identity_unavailable')
    return ('darwin-socket',so,pcb,generation)
