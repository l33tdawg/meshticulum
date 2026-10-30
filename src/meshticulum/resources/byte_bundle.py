"""Signed public file parts and verified-only local resume; no radio authority.

Useful bytes are the decoded original file. Base64, JSON and optional exact32
padding are wire overhead. Completion requires the signed whole-file length
and SHA256, not a sum of independently completed or timed pieces.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import threading

from . import knowledge_pack as kp, lamplighter as ll

PART_BYTES = 23900  # Leaves room for typed metadata below 32740 serialized bytes.
SCHEMA = 'meshticulum.public-byte-bundle.v1'
CONTEXT = 'meshticulum.public-byte-bundle-context.v1'


def build(payload, *, pack_id, created_at, private_key, exact32=False):
    kp.require(type(payload) is bytes and 1 <= len(payload) <= kp.BUNDLE_BYTES
               and type(exact32) is bool, 'invalid public bundle payload')
    whole = kp.sha256_hex(payload); count = (len(payload)+PART_BYTES-1)//PART_BYTES
    chunks, entries = {}, []
    for index in range(count):
        raw = payload[index*PART_BYTES:(index+1)*PART_BYTES]
        part = dict(schema='meshticulum.public-byte-bundle-part.v1',encoding='base64',index=index,
            parts=count,part_bytes=PART_BYTES,offset=index*PART_BYTES,raw_bytes=len(raw),raw_sha256=kp.sha256_hex(raw),
            bundle_bytes=len(payload),bundle_sha256=whole,payload_base64=base64.b64encode(raw).decode('ascii'),wire_padding='')
        record = kp.build_chunk('part-%06d'%index,'Public file bytes.',title='Public file part',
            source='SAGE Foundation',date=created_at[:10],licence='CC0-1.0',bundle_part=part)
        if exact32:
            part['wire_padding']='_'*(kp.BUNDLE_CHUNK_BYTES-len(kp.chunk_bytes(record)))
            record=kp.build_chunk(record['id'],record['text'],title=record['title'],source=record['source'],
                date=record['date'],licence=record['licence'],bundle_part=part)
            kp.require(len(kp.chunk_bytes(record))==kp.BUNDLE_CHUNK_BYTES,'exact32 padding mismatch')
        chunks[record['sha256']]=record
        entries.append(dict(index=index,offset=part['offset'],raw_bytes=len(raw),raw_sha256=part['raw_sha256'],
                            chunk_sha256=record['sha256']))
    descriptor=dict(schema=SCHEMA,bytes=len(payload),sha256=whole,part_bytes=PART_BYTES,parts=entries)
    manifest=kp.build_manifest(pack_id=pack_id,version=1,pack_class='reference',title='Public byte bundle',
        licence='CC0-1.0',provenance=dict(source='SAGE Foundation public test data',note=kp.canonical_json(descriptor).decode()),
        created_at=created_at,chunks=chunks.values(),embedding_model='none',embedding_dimension=1)
    raw=kp.manifest_bytes(manifest)
    return manifest,chunks,kp.sign_manifest(raw,private_key)


def descriptor(manifest):
    """Extract signed whole-file expectations with exact order and no overlaps."""
    note=manifest['pack']['provenance'].get('note','')
    kp.require(type(note) is str and len(note)<=16384,'invalid bundle descriptor')
    value=json.loads(note)
    kp.require(kp.canonical_json(value).decode()==note and type(value) is dict and set(value)=={'schema','bytes','sha256','part_bytes','parts'}
               and value['schema']==SCHEMA and type(value['bytes']) is int and 1<=value['bytes']<=kp.BUNDLE_BYTES
               and type(value['sha256']) is str and kp.HEX64.fullmatch(value['sha256'])
               and type(value['part_bytes']) is int and 1<=value['part_bytes']<=kp.BUNDLE_PART_BYTES
               and type(value['parts']) is list and 1<=len(value['parts'])<=42
               and len(value['parts'])==(value['bytes']+value['part_bytes']-1)//value['part_bytes']
               and len(manifest['chunks'])==len(value['parts']) and manifest['embedding']['embedded_chunks']==0,
               'invalid bundle descriptor')
    for index,(part,chunk) in enumerate(zip(value['parts'],manifest['chunks'])):
        kp.require(type(part) is dict and set(part)=={'index','offset','raw_bytes','raw_sha256','chunk_sha256'}
                   and all(type(part[k]) is int for k in ('index','offset','raw_bytes'))
                   and part['index']==index and part['offset']==index*value['part_bytes']
                   and part['raw_bytes']==min(value['part_bytes'],value['bytes']-part['offset'])
                   and type(part['raw_sha256']) is str and kp.HEX64.fullmatch(part['raw_sha256'])
                   and part['chunk_sha256']==chunk['sha256'] and chunk['id']=='part-%06d'%index
                   and chunk['bytes']<=kp.BUNDLE_CHUNK_BYTES,'invalid ordered bundle part')
    return value


class Receiver:
    """Bounded protected quarantine; data verification supplies no admission.

    Reopening retains the original epoch+monotonic deadline and host boot.
    Every resumed part is reverified against the original publisher manifest.
    Callers must separately prove transport provenance and actual packet debt.
    """
    def __init__(self,directory,manifest_raw,signature_raw,trust,manifest_sha256,*,expires_epoch_us,clock,boot_reader):
        self.path=Path(directory).absolute();self.clock,self.boot_reader,self.trust=clock,boot_reader,trust
        self.raw,self.signature,self.digest=manifest_raw,signature_raw,manifest_sha256
        self.pid,self.thread=os.getpid(),threading.get_ident();self.fd=self.context_fd=self.parts_fd=None
        self.owned={};self.chain=[]
        self.closed=False;self.reused=0
        try:
            kp.require(callable(clock) and callable(boot_reader) and self.path.resolve()==self.path,'invalid bundle owner')
            kp.require(type(manifest_raw) is bytes and 0<len(manifest_raw)<=65536
                       and type(signature_raw) is bytes and 0<len(signature_raw)<=4096,'bundle metadata too large')
            self._private(self.path.lstat(),True);parent=None
            kp.require(len(self.path.parts)<=64,'bundle ancestry too long')
            for name in self.path.parts:
                fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
                info=os.fstat(fd);identity=self._identity(info)[:4]
                self.owned[fd]=identity[:2];self.chain.append((fd,parent,name,identity))
                kp.require(info.st_uid in (0,os.geteuid()),'foreign bundle ancestor');parent=fd
            self.fd=parent
            self.identity=self._identity(os.fstat(self.fd))[:4];self._manifest()
            self.binding=dict(schema=CONTEXT,manifest_sha256=self.digest,bundle_sha256=self.expected['sha256'],
                bundle_bytes=self.expected['bytes'],host_boot_id=boot_reader(),expires_epoch_us=expires_epoch_us)
            first=clock();self._clock(first)
            kp.require(type(expires_epoch_us) is int and 0<expires_epoch_us-first[0]<=86400000000,
                       'invalid original bundle lifetime')
            try:self.context_fd=os.open('context.json',os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=self.fd)
            except FileNotFoundError:
                kp.require(not os.listdir(self.fd),'unverified existing bundle quarantine')
                context=dict(self.binding,original_epoch_us=first[0],original_monotonic_us=first[1],
                    deadline_monotonic_us=first[1]+expires_epoch_us-first[0])
                self._atomic(self.fd,'context.json',kp.canonical_json(context))
                self.context_fd=os.open('context.json',os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=self.fd)
                self.owned[self.context_fd]=self._identity(os.fstat(self.context_fd))[:2]
                os.mkdir('parts',mode=0o700,dir_fd=self.fd)
            self.owned[self.context_fd]=self._identity(os.fstat(self.context_fd))[:2]
            self._private(os.fstat(self.context_fd));self.context_raw=os.pread(self.context_fd,2049,0)
            kp.require(len(self.context_raw)<=2048,'bundle context too large');self.context=json.loads(self.context_raw)
            keys=set(self.binding)|{'original_epoch_us','original_monotonic_us','deadline_monotonic_us'}
            kp.require(kp.canonical_json(self.context)==self.context_raw and set(self.context)==keys and all(self.context[k]==v for k,v in self.binding.items())
                       and type(self.binding['host_boot_id']) is str and 0<len(self.binding['host_boot_id'])<=128
                       and all(type(self.context[k]) is int for k in ('original_epoch_us','original_monotonic_us','deadline_monotonic_us'))
                       and self.context['deadline_monotonic_us']==self.context['original_monotonic_us']+
                           expires_epoch_us-self.context['original_epoch_us'],'bundle original context changed')
            self.context_identity=self._file_identity(os.fstat(self.context_fd))
            self.parts_fd=os.open('parts',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=self.fd)
            self._private(os.fstat(self.parts_fd),True);self.parts_identity=self._identity(os.fstat(self.parts_fd))[:4]
            self.owned[self.parts_fd]=self.parts_identity[:2]
            self.refs=(self.path,self.clock,self.boot_reader,self.trust,self.raw,self.signature,self.digest,self.fd,self.context_fd,self.parts_fd)
            self.last=None;self._live()
            names={e['chunk_sha256']+'.json' for e in self.expected['parts']}
            kp.require(set(os.listdir(self.path))<={'context.json','parts','bundle.bin'}
                       and set(os.listdir(self.path/'parts'))<=names,'foreign bundle quarantine entry')
            for index,entry in enumerate(self.expected['parts']):
                if (self.path/'parts'/(entry['chunk_sha256']+'.json')).exists():self._part(index);self.reused+=1
        except BaseException:self.close();raise
    @staticmethod
    def _identity(info):return info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_nlink
    @classmethod
    def _file_identity(cls,info):return cls._identity(info)+(info.st_size,info.st_mtime_ns,info.st_ctime_ns)
    @staticmethod
    def _private(info,directory=False):
        kp.require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
                   and info.st_uid==os.geteuid() and stat.S_IMODE(info.st_mode)==(0o700 if directory else 0o600)
                   and (directory or info.st_nlink==1),'unsafe bundle quarantine')
    @staticmethod
    def _clock(now):kp.require(type(now) is tuple and len(now)==2 and all(type(v) is int and 0<=v<2**63 for v in now),'invalid bundle clock')
    def _check_chain(self):
        for fd,parent,name,identity in self.chain:
            kp.require(self._identity(os.fstat(fd))[:4]==identity
                       ==self._identity(os.stat(name,dir_fd=parent,follow_symlinks=False))[:4],'bundle ancestry rebound')
    def _published(self,directory_fd,name,raw,identity=None):
        fd=os.open(name,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=directory_fd)
        try:
            info=os.fstat(fd);self._private(info);original=self._file_identity(info)
            kp.require((identity is None or original[:2]==identity) and info.st_size==len(raw)
                       and os.pread(fd,len(raw)+1,0)==raw and original==self._file_identity(os.fstat(fd))
                       ==self._file_identity(os.stat(name,dir_fd=directory_fd,follow_symlinks=False)),
                       'published bundle bytes changed')
        finally:os.close(fd)
    def _atomic(self,directory_fd,name,raw):
        """O_EXCL publication relative to the retained directory, never a path."""
        temporary='.bundle-'+secrets.token_hex(16);fd=None;identity=None
        try:
            fd=os.open(temporary,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=directory_fd)
            identity=self._identity(os.fstat(fd))[:2];self._private(os.fstat(fd))
            view=memoryview(raw)
            while view:
                count=os.write(fd,view);kp.require(count>0,'short bundle write');view=view[count:]
            os.fsync(fd);written=self._file_identity(os.fstat(fd))
            if hasattr(self,'refs'):self._live()
            else:self._check_chain()
            kp.require(self._file_identity(os.fstat(fd))==written
                       ==self._file_identity(os.stat(temporary,dir_fd=directory_fd,follow_symlinks=False))
                       and os.pread(fd,len(raw)+1,0)==raw,'bundle staging rebound')
            os.link(temporary,name,src_dir_fd=directory_fd,dst_dir_fd=directory_fd,follow_symlinks=False)
            os.unlink(temporary,dir_fd=directory_fd);os.fsync(directory_fd)
            self._published(directory_fd,name,raw,identity)
        finally:
            if identity is not None:
                try:
                    if self._identity(os.stat(temporary,dir_fd=directory_fd,follow_symlinks=False))[:2]==identity:
                        os.unlink(temporary,dir_fd=directory_fd)
                except FileNotFoundError:pass
            if fd is not None:
                try:
                    if self._identity(os.fstat(fd))[:2]==identity:os.close(fd)
                except OSError:pass
    def _manifest(self):
        self.manifest=ll.manifest(self.raw,self.signature,self.trust,self.digest);self.expected=descriptor(self.manifest)
    def _live(self):
        kp.require(not self.closed and (self.pid,self.thread)==(os.getpid(),threading.get_ident())
                   and all(a is b for a,b in zip((self.path,self.clock,self.boot_reader,self.trust,self.raw,self.signature,
                   self.digest,self.fd,self.context_fd,self.parts_fd),self.refs)),'bundle owner changed')
        self._private(os.fstat(self.fd),True);kp.require(self._identity(os.fstat(self.fd))[:4]==self.identity==self._identity(self.path.lstat())[:4],'bundle root rebound')
        self._private(os.fstat(self.parts_fd),True);kp.require(self._identity(os.fstat(self.parts_fd))[:4]==self.parts_identity
            ==self._identity((self.path/'parts').lstat())[:4],'bundle parts rebound')
        self._private(os.fstat(self.context_fd));kp.require(self._file_identity(os.fstat(self.context_fd))==self.context_identity
            ==self._file_identity((self.path/'context.json').lstat()) and os.pread(self.context_fd,2049,0)==self.context_raw,'bundle context rebound')
        context=json.loads(self.context_raw);original_raw=self.context_raw
        kp.require(kp.canonical_json(self.context)==original_raw,'parsed bundle context changed')
        self._manifest();now=self.clock();self._clock(now);observed_boot=self.boot_reader()
        kp.require(self.context_raw is original_raw and kp.canonical_json(self.context)==original_raw
                   and self._file_identity(os.fstat(self.context_fd))==self.context_identity
                   ==self._file_identity((self.path/'context.json').lstat())
                   and os.pread(self.context_fd,2049,0)==original_raw,'bundle context changed during callback')
        self._manifest()
        kp.require(observed_boot==context['host_boot_id'] and context['original_epoch_us']<=now[0]<context['expires_epoch_us']
                   and context['original_monotonic_us']<=now[1]<context['deadline_monotonic_us']
                   and (self.last is None or all(a>=b for a,b in zip(now,self.last))),'original bundle deadline expired')
        self.last=now
        self._check_chain()
    def _validate_part(self,index,raw):
        kp.require(type(index) is int and 0<=index<len(self.expected['parts']) and type(raw) is bytes
                   and len(raw)<=kp.BUNDLE_CHUNK_BYTES,'invalid bundle part input')
        record=ll.chunk(raw,self.manifest['chunks'][index]);kp.validate_pack_rules(self.manifest,[record])
        part=record['bundle_part'];entry=self.expected['parts'][index];decoded=kp.decode_bundle_part(part)
        kp.require(part['index']==index and part['parts']==len(self.expected['parts']) and part['part_bytes']==self.expected['part_bytes']
                   and part['offset']==entry['offset'] and part['raw_bytes']==entry['raw_bytes'] and part['raw_sha256']==entry['raw_sha256']
                   and part['bundle_bytes']==self.expected['bytes'] and part['bundle_sha256']==self.expected['sha256'],
                   'bundle part belongs to another file')
        return decoded,part
    def _part(self,index):
        name=self.expected['parts'][index]['chunk_sha256']+'.json'
        fd=os.open(name,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=self.parts_fd)
        try:
            self._private(os.fstat(fd));original=self._file_identity(os.fstat(fd));raw=os.pread(fd,kp.BUNDLE_CHUNK_BYTES+1,0)
            kp.require(original==self._file_identity(os.fstat(fd))==self._file_identity(os.stat(name,dir_fd=self.parts_fd,follow_symlinks=False)),
                       'bundle part changed')
            return raw,self._validate_part(index,raw)
        finally:os.close(fd)
    def accept(self,index,raw):
        self._live();self._validate_part(index,raw)
        target=self.path/'parts'/(self.expected['parts'][index]['chunk_sha256']+'.json')
        kp.require(not target.exists() and not target.is_symlink(),'duplicate bundle part')
        self._live();self._atomic(self.parts_fd,target.name,raw);self._live()
    def verified_parts(self):
        """Return sorted retained indexes after fresh publisher/owner checks.

        This is a verified snapshot, not transport completion or admission.
        Invalid retained bytes raise instead of being treated as missing. The
        opening-time ``reused`` count is diagnostic and is never consulted.
        """
        self._live()
        names=tuple(entry['chunk_sha256']+'.json' for entry in self.expected['parts'])
        present=set(os.listdir(self.parts_fd))
        kp.require(set(os.listdir(self.fd))<={'context.json','parts','bundle.bin'}
                   and present<=set(names),'foreign bundle quarantine entry')
        verified=[];identities={}
        for index,name in enumerate(names):
            if name not in present:continue
            self._live()
            original=self._file_identity(os.stat(name,dir_fd=self.parts_fd,follow_symlinks=False))
            self._part(index)
            kp.require(original==self._file_identity(os.stat(name,dir_fd=self.parts_fd,follow_symlinks=False)),
                       'bundle part changed')
            identities[name]=original;verified.append(index)
        # A later part or clock callback must not invalidate an earlier result.
        self._live()
        kp.require(set(os.listdir(self.parts_fd))==present
                   and set(os.listdir(self.fd))<={'context.json','parts','bundle.bin'}
                   and all(self._file_identity(os.stat(name,dir_fd=self.parts_fd,follow_symlinks=False))==identity
                           for name,identity in identities.items()),'bundle inventory changed')
        return tuple(verified)
    def missing_parts(self):
        """Return sorted absent indexes, revalidating every retained part first."""
        verified=set(self.verified_parts())
        return tuple(index for index in range(len(self.expected['parts'])) if index not in verified)
    def finish(self):
        self._live();whole=bytearray();wire=encoded=padding=0
        for index in range(len(self.expected['parts'])):
            raw,(decoded,part)=self._part(index);whole.extend(decoded)
            wire+=len(raw);encoded+=len(part['payload_base64']);padding+=len(part['wire_padding']);self._live()
        kp.require(len(whole)==self.expected['bytes'] and kp.sha256_hex(whole)==self.expected['sha256'],'whole public bundle mismatch')
        self._live();self._atomic(self.fd,'bundle.bin',whole);self._live();self._published(self.fd,'bundle.bin',whole)
        return dict(schema=SCHEMA,complete=True,publisher_verified=True,manifest_sha256=self.digest,
            useful_decoded_bytes=len(whole),whole_file_sha256=self.expected['sha256'],parts=len(self.expected['parts']),
            reused_verified_parts=self.reused,serialized_chunk_bytes=wire,base64_bytes=encoded,padding_bytes=padding,
            json_overhead_bytes=wire-encoded-padding,original_expires_epoch_us=self.context['expires_epoch_us'],
            original_deadline_monotonic_us=self.context['deadline_monotonic_us'],
            original_start_epoch_us=self.context['original_epoch_us'],original_start_monotonic_us=self.context['original_monotonic_us'],
            verified_epoch_us=self.last[0],verified_monotonic_us=self.last[1],native_transport_qualified=False)
    def _file_matches(self,name,size,digest,identity):
        """Recheck a published/staged file without reading the whole file at once."""
        self._live()
        fd=os.open(name,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=self.fd)
        original=None
        try:
            info=os.fstat(fd);self._private(info);original=self._file_identity(info)
            kp.require(original[:2]==identity and info.st_size==size,'bundle file size or identity changed')
            hashed=hashlib.sha256();offset=0
            while offset<size:
                self._live()
                raw=os.pread(fd,min(PART_BYTES,size-offset),offset)
                kp.require(0<len(raw)<=min(PART_BYTES,size-offset),'short bundle file read')
                hashed.update(raw);offset+=len(raw)
                self._live()
            kp.require(not os.pread(fd,1,offset) and hashed.hexdigest()==digest
                       and original==self._file_identity(os.fstat(fd))
                       ==self._file_identity(os.stat(name,dir_fd=self.fd,follow_symlinks=False)),
                       'bundle file bytes changed')
            self._live()
        finally:
            try:
                if original is None or self._identity(os.fstat(fd))[:2]==original[:2]:os.close(fd)
            except OSError:pass
    def finish_file(self):
        """Verify and atomically publish bundle.bin using one bounded part at a time.

        The signed whole-file length/hash, publisher trust and original owner,
        boot and clocks are rechecked. A failed finalization removes only its
        own exact staging/publication inode; it never overwrites an existing
        bundle. This method grants no transport or application authority.
        """
        self._live();temporary='.bundle-'+secrets.token_hex(16);directory_fd=self.fd
        fd=None;identity=None;published=complete=False
        size=wire=encoded=padding=0;hashed=hashlib.sha256()
        try:
            fd=os.open(temporary,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600,dir_fd=directory_fd)
            info=os.fstat(fd);identity=self._identity(info)[:2];self._private(info)
            for index in range(len(self.expected['parts'])):
                self._live();raw,(decoded,part)=self._part(index)
                kp.require(size+len(decoded)<=self.expected['bytes'],'bundle file exceeds signed length')
                view=memoryview(decoded)
                while view:
                    self._live();count=os.write(fd,view)
                    kp.require(type(count) is int and 0<count<=len(view),'short bundle write')
                    view=view[count:]
                hashed.update(decoded);size+=len(decoded)
                wire+=len(raw);encoded+=len(part['payload_base64']);padding+=len(part['wire_padding'])
                self._live()
            kp.require(size==self.expected['bytes'] and hashed.hexdigest()==self.expected['sha256'],
                       'whole public bundle mismatch')
            os.fsync(fd);written=self._file_identity(os.fstat(fd));self._live()
            self._file_matches(temporary,size,self.expected['sha256'],identity)
            kp.require(written==self._file_identity(os.fstat(fd))
                       ==self._file_identity(os.stat(temporary,dir_fd=directory_fd,follow_symlinks=False)),
                       'bundle staging rebound')
            os.link(temporary,'bundle.bin',src_dir_fd=directory_fd,dst_dir_fd=directory_fd,follow_symlinks=False)
            published=True;os.unlink(temporary,dir_fd=directory_fd);os.fsync(directory_fd)
            self._live();self._file_matches('bundle.bin',size,self.expected['sha256'],identity);self._live()
            complete=True
            return dict(schema=SCHEMA,complete=True,publisher_verified=True,manifest_sha256=self.digest,
                useful_decoded_bytes=size,whole_file_sha256=self.expected['sha256'],parts=len(self.expected['parts']),
                reused_verified_parts=self.reused,serialized_chunk_bytes=wire,base64_bytes=encoded,padding_bytes=padding,
                json_overhead_bytes=wire-encoded-padding,original_expires_epoch_us=self.context['expires_epoch_us'],
                original_deadline_monotonic_us=self.context['deadline_monotonic_us'],
                original_start_epoch_us=self.context['original_epoch_us'],original_start_monotonic_us=self.context['original_monotonic_us'],
                verified_epoch_us=self.last[0],verified_monotonic_us=self.last[1],native_transport_qualified=False,
                file_backed=True,read_block_bytes=PART_BYTES)
        finally:
            names=(temporary,'bundle.bin') if published and not complete else (temporary,)
            try:
                if identity is not None:
                    for name in names:
                        try:
                            if self._identity(os.stat(name,dir_fd=directory_fd,follow_symlinks=False))[:2]==identity:
                                os.unlink(name,dir_fd=directory_fd);os.fsync(directory_fd)
                        except FileNotFoundError:pass
            finally:
                if fd is not None:
                    try:
                        if identity is None or self._identity(os.fstat(fd))[:2]==identity:os.close(fd)
                    except OSError:pass
    def close(self):
        if self.closed:return
        if (self.pid,self.thread)!=(os.getpid(),threading.get_ident()):return
        self.closed=True
        for fd,identity in list(self.owned.items()):
            try:
                if self._identity(os.fstat(fd))[:2]==identity:os.close(fd)
            except OSError:pass
        self.owned.clear()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()
