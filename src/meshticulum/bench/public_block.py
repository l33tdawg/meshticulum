"""Explicit public MR1 size capability; supplies no application/RF authority."""
from .wire import require

DEFAULT_OBJECT_BYTES = 16384
PUBLIC32_OBJECT_BYTES = 32768
PUBLIC32_MARKER = 'public_response_max_bytes'


def public32_service(service):
    if PUBLIC32_MARKER not in service:
        return False
    require(type(service[PUBLIC32_MARKER]) is int
            and service[PUBLIC32_MARKER] == PUBLIC32_OBJECT_BYTES
            and service.get('kind') == 'knowledge'
            and service.get('format') in ('MR1.request','MR1.response'),
            'invalid_public32_service_capability')
    return True


def object_limit(service):
    return (PUBLIC32_OBJECT_BYTES if public32_service(service)
            and service['format'] == 'MR1.response' else DEFAULT_OBJECT_BYTES)
