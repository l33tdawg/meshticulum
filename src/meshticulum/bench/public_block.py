"""Explicit public MR1 size capability; supplies no application/RF authority."""
from .wire import require

DEFAULT_OBJECT_BYTES = 16384
PUBLIC32_OBJECT_BYTES = 32768
PUBLIC32_MARKER = 'public_response_max_bytes'
REPRESENTATION_MARKER = 'public_resource_representation'
TRANSPORT_DESCRIPTOR_MARKER = 'public_transport_descriptor_sha256'


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


def public_mb1_service(service):
    """Return the explicit descriptor pin; this supplies no grant or authority.

    Both representation and descriptor are included in the authenticated
    service/route. The legacy public32 marker alone still means exact32 JSON.
    """
    fields={REPRESENTATION_MARKER,TRANSPORT_DESCRIPTOR_MARKER}
    if not fields.intersection(service):return None
    pin=service.get(TRANSPORT_DESCRIPTOR_MARKER)
    require(fields<=set(service) and public32_service(service)
        and service[REPRESENTATION_MARKER]=='MB1' and type(pin) is str and len(pin)==64
        and all(c in '0123456789abcdef' for c in pin),'invalid_public_mb1_service_capability')
    return pin
