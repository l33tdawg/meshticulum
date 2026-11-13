"""Explicit single-operation file memory admission; grants no RF permission."""
from .wire import require
from .public_block import object_limit

FILE64_BYTES = 65536
FILE64_FRAMES = 512
FILE64_SCHEMA = 'meshticulum.example-radio-file.v2'
FILE48_BYTES = 49152
FILE48_FRAMES = 512
FILE48_SCHEMA = 'meshticulum.example-radio-file48.v1'

def file64_capability(plan):
    if 'file_capability' not in plan:
        return False
    cap = plan['file_capability']
    require(plan.get('schema') == 'meshticulum.bench-run.v1'
            and 'service' not in plan and 'operation_limits' in plan
            and type(cap) is dict
            and set(cap) == {'schema', 'max_object_bytes', 'max_total_frames', 'manifest_sha256'}
            and cap['schema'] == 'meshticulum.file64-capability.v1'
            and type(cap['max_object_bytes']) is int and cap['max_object_bytes'] == FILE64_BYTES
            and type(cap['max_total_frames']) is int and cap['max_total_frames'] == FILE64_FRAMES
            and type(cap['manifest_sha256']) is str and len(cap['manifest_sha256']) == 64
            and all(c in '0123456789abcdef' for c in cap['manifest_sha256']),
            'invalid_file64_capability')
    obj = plan.get('object', {})
    require(type(obj) is dict and type(obj.get('bytes')) is int and obj['bytes'] == FILE64_BYTES
            and type(obj.get('chunk_index')) is int and obj['chunk_index'] == 0
            and obj.get('object_id') == obj.get('sha256'), 'invalid_file64_object')
    return True

def file48_capability(plan):
    if 'file_capability' not in plan:
        return False
    cap = plan['file_capability']
    require(plan.get('schema') == 'meshticulum.bench-run.v1'
            and 'service' not in plan and 'operation_limits' in plan
            and type(cap) is dict
            and set(cap) == {'schema', 'max_object_bytes', 'max_total_frames', 'manifest_sha256'}
            and cap['schema'] == 'meshticulum.file48-capability.v1'
            and type(cap['max_object_bytes']) is int and cap['max_object_bytes'] == FILE48_BYTES
            and type(cap['max_total_frames']) is int and cap['max_total_frames'] == FILE48_FRAMES
            and type(cap['manifest_sha256']) is str and len(cap['manifest_sha256']) == 64
            and all(c in '0123456789abcdef' for c in cap['manifest_sha256']),
            'invalid_file48_capability')
    obj = plan.get('object', {})
    require(type(obj) is dict and type(obj.get('bytes')) is int and obj['bytes'] == FILE48_BYTES
            and type(obj.get('chunk_index')) is int and obj['chunk_index'] == 0
            and obj.get('object_id') == obj.get('sha256'), 'invalid_file48_object')
    return True

def file_capability_mode(plan):
    if 'file_capability' not in plan:
        return None
    cap = plan['file_capability']
    if type(cap) is dict and cap.get('schema') == 'meshticulum.file48-capability.v1':
        require(file48_capability(plan), 'invalid_file48_capability')
        return 'file48'
    require(file64_capability(plan), 'invalid_file64_capability')
    return 'file64'

def admitted_object_limit(plan):
    mode = file_capability_mode(plan)
    return FILE48_BYTES if mode == 'file48' else (FILE64_BYTES if mode == 'file64' else object_limit(plan.get('service', {})))

def admitted_frame_limit(plan, *, public32=False):
    return FILE64_FRAMES if file_capability_mode(plan) is not None else (256 if public32 else 128)
