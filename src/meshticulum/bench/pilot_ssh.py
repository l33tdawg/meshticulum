"""Explicit finite SSH management adapter for the public pilot.

No connection occurs on import or construction. Target configuration and a
current device window come from the operator. Sudo credentials stay in memory;
package data uses a non-PTY channel and never appears in command arguments.
"""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import select
import shlex
import subprocess
import time
import uuid

from .pilot_provision import identifier
from .wire import require

PROMPT = b'MESHTICULUM_SUDO_PROMPT:'

# This script receives only the private package on stdin, without a terminal.
STAGE = '''import hashlib,json,os,stat,sys
from pathlib import Path
digest=sys.argv[1]
data=sys.stdin.buffer.read(100001)
assert len(data)<=100000 and hashlib.sha256(data).hexdigest()==digest
root=Path.home()/'meshticulum-bench-staging'
root.mkdir(mode=0o700,exist_ok=True)
s=root.lstat();assert stat.S_ISDIR(s.st_mode) and s.st_uid==os.geteuid() and stat.S_IMODE(s.st_mode)==0o700
target=root/('pilot-'+digest+'.json')
fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())
fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:os.fsync(fd)
finally:os.close(fd)
print(json.dumps(dict(schema='meshticulum.staged-pilot-package.v1',path=str(target),sha256=digest)))
'''

# The fixed root launcher is checked against the operator's manifest pin, not
# merely against a potentially changed manifest's own source map.
INVOKE = '''import hashlib,json,os,runpy,stat,sys
from pathlib import Path
path,pin,*args=sys.argv[1:]
root=Path(path).parent
for parent in (root,*root.parents):
 s=parent.lstat();assert stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022
def read(p):
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
 try:
  s=os.fstat(fd);assert stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022 and s.st_size<=2000000
  return os.read(fd,2000001)
 finally:os.close(fd)
raw=read(root/'manifest.json');assert hashlib.sha256(raw).hexdigest()==pin
m=json.loads(raw);assert m['schema']=='meshticulum.bench-runtime-bundle.v1' and m['private_services_enabled'] is False
assert hashlib.sha256(read(Path(path))).hexdigest()==m['files']['bench_node.py']
sys.argv=[path,*args]
runpy.run_path(path,run_name='__main__')
'''


def one_record(raw, schema):
    records=[]
    for line in raw.replace(PROMPT,b'').decode('utf-8').splitlines():
        try:value=json.loads(line)
        except ValueError:continue
        if type(value) is dict and value.get('schema')==schema:records.append(value)
    require(len(records)==1,'missing_or_ambiguous_native_record')
    return records[0]


class Job:
    """An owned local SSH process; native liveness is checked separately."""
    def __init__(self,argv,*,password=None,deadline,clock=time.monotonic,popen=subprocess.Popen):
        self.process=popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                           bufsize=0,start_new_session=True)
        self.password=password;self.deadline=deadline;self.clock=clock
        self.output=bytearray();self.prompted=False;self.closed=False

    def pump(self):
        if self.closed:return
        while select.select([self.process.stdout],[],[],0)[0]:
            part=os.read(self.process.stdout.fileno(),4096)
            if not part:break
            self.output.extend(part)
            require(len(self.output)<=1000000,'native_management_output_limit')
            if self.password and self.password.encode() in self.output:
                self.output.clear()
                raise RuntimeError('credential_echo_refused')
            count=self.output.count(PROMPT)
            if count:
                require(count==1 and self.password is not None,'unexpected_or_repeated_sudo_prompt')
                if not self.prompted:
                    self.process.stdin.write(self.password.encode()+b'\n');self.process.stdin.flush()
                    self.prompted=True

    def poll(self):
        self.pump()
        code=self.process.poll()
        require(code is not None or self.clock()<self.deadline,'native_management_deadline')
        return code

    def wait(self):
        while self.poll() is None:time.sleep(.01)
        self.pump()
        return self.process.returncode

    def close(self):
        if self.closed:return
        if self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=2)
        self.process.stdin.close();self.process.stdout.close()
        self.password=None;self.closed=True


class SSHOperator:
    def __init__(self,config,*,provision_source,runtime_source=None,passwords=None,clock=time.monotonic,epoch=None):
        require(type(config) is dict and set(config)=={'schema','runtime_sha256','manifest_sha256',
            'provision_sha256','runtime_bootstrap_sha256','handoff_end_epoch_us','port_base','units'}
            and config['schema']=='meshticulum.finite-ssh-pilot.v1','invalid_ssh_pilot_config')
        for key in ('runtime_sha256','manifest_sha256','provision_sha256','runtime_bootstrap_sha256'):
            require(type(config[key]) is str and re.fullmatch('[0-9a-f]{64}',config[key]),'invalid_ssh_pilot_pin')
        require(hashlib.sha256(provision_source).hexdigest()==config['provision_sha256'], 'pilot_provision_source_changed')
        if runtime_source is not None:
            require(hashlib.sha256(runtime_source).hexdigest()==config['runtime_bootstrap_sha256'],'pilot_runtime_bootstrap_changed')
        require(type(config['units']) is dict and set(config['units'])=={'unit-a','unit-b','unit-c'},'pilot_three_exact_hosts_required')
        self.config=json.loads(json.dumps(config));self.source=provision_source.decode('utf-8')
        self.runtime_source=runtime_source.decode('utf-8') if runtime_source is not None else None
        self.clock=clock;self.epoch=epoch or (lambda:time.time_ns()//1000)
        remaining=(config['handoff_end_epoch_us']-self.epoch())/1e6
        require(20<remaining<=900,'current_finite_ssh_window_required')
        self.deadline=self.clock()+remaining;self.passwords=dict(passwords or {})
        self.next_port=config['port_base'];require(type(self.next_port) is int and 1024<=self.next_port<=65400,'invalid_pilot_port_range')
        self.jobs={};self.events=[]
        for unit,row in self.config['units'].items():
            require(type(row) is dict and set(row)=={'ssh','python','sudo','host_boot_id','ip_address'},'invalid_pilot_host_config')
            require(type(row['sudo']) is bool and type(row['python']) is str and row['python'].startswith('/'),'invalid_pilot_host_python')
            require(str(uuid.UUID(row['host_boot_id']))==row['host_boot_id'],'invalid_pilot_host_boot')
            require(str(ipaddress.ip_address(row['ip_address']))==row['ip_address'],'invalid_pilot_host_address')
            self._ssh(unit,pty=False)

    def _ssh(self,unit,*,pty):
        argv=self.config['units'][unit]['ssh']
        require(type(argv) is list and len(argv)>=6 and Path(argv[0]).name=='ssh'
                and all(type(x) is str and '\n' not in x for x in argv),'invalid_pinned_ssh_argv')
        allowed={'BatchMode','StrictHostKeyChecking','ConnectTimeout','HostKeyAlias','UserKnownHostsFile','IdentitiesOnly','ServerAliveInterval','ServerAliveCountMax'}
        index=1;options={};result=[argv[0],'-tt' if pty else '-T']
        while index<len(argv)-1:
            flag=argv[index]
            if flag in ('-T','-tt'):index+=1;continue
            require(flag in ('-o','-i','-p') and index+1<len(argv)-1,'unsupported_ssh_option')
            value=argv[index+1]
            if flag=='-o':
                key,sep,entry=value.partition('=');require(sep and key in allowed and key not in options,'unsupported_ssh_override')
                options[key]=entry
            result.extend((flag,value));index+=2
        require(index==len(argv)-1 and not argv[-1].startswith('-')
                and re.fullmatch('[A-Za-z0-9._:@\\[\\]-]+',argv[-1])
                and options.get('StrictHostKeyChecking')=='yes' and options.get('BatchMode')=='yes',
                'pinned_noninteractive_ssh_required')
        result.extend(('-o','ControlMaster=no','-o','ClearAllForwardings=yes','-o','ForwardAgent=no',
                       '-o','ForwardX11=no','-o','PermitLocalCommand=no',argv[-1]))
        return result

    def _argv(self,unit,command,*,root):
        row=self.config['units'][unit];sudo=root and row['sudo']
        if sudo:command=['sudo','-p',PROMPT.decode(),*(['-n'] if self.passwords.get(unit) is None else []),'--',*command]
        return self._ssh(unit,pty=sudo)+[shlex.join(command)]

    def _command(self,unit,command,schema,*,root=True,cleanup=False):
        deadline=min(self.clock()+5,self.deadline)
        require(self.clock()<deadline or cleanup,'ssh_pilot_window_expired')
        if cleanup:deadline=self.clock()+5
        job=Job(self._argv(unit,command,root=root),password=self.passwords.get(unit),deadline=deadline,clock=self.clock)
        try:
            require(job.wait()==0,'native_management_command_failed')
            value=one_record(bytes(job.output),schema)
            self.events.append(dict(unit=unit,schema=schema,cleanup=cleanup,outside_handoff=self.clock()>=self.deadline))
            return value
        finally:job.close()

    def _action(self,unit,action,run_id=None,*extra,cleanup=False,schema):
        row=self.config['units'][unit]
        launcher='/opt/meshticulum-bench/runtime-'+self.config['runtime_sha256'][:16]+'/bench_node.py'
        args=[action,unit,*(['--run-id',identifier(run_id)] if run_id is not None else []),*extra]
        return self._command(unit,[row['python'],'-I','-B','-c',INVOKE,launcher,self.config['manifest_sha256'],*args],schema,cleanup=cleanup)

    def endpoints(self,sender,receiver):
        require(self.clock()<self.deadline and self.next_port<=65534,'ssh_pilot_port_or_window_exhausted')
        value={sender:(self.config['units'][sender]['ip_address'],self.next_port),
               receiver:(self.config['units'][receiver]['ip_address'],self.next_port+1)}
        self.next_port+=2;return value

    def _package(self,package,schema):
        unit=package['unit'];row=self.config['units'][unit]
        raw=json.dumps(package,sort_keys=True,separators=(',',':')).encode();digest=hashlib.sha256(raw).hexdigest()
        require(len(raw)<=100000 and self.clock()<self.deadline,'pilot_staging_bounds')
        result=subprocess.run(self._argv(unit,['python3','-I','-B','-c',STAGE,digest],root=False),input=raw,
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=min(5,self.deadline-self.clock()))
        require(result.returncode==0,'pilot_package_staging_failed')
        staged=one_record(result.stdout,'meshticulum.staged-pilot-package.v1')
        path=Path(staged['path'])
        require(staged['sha256']==digest and path.is_absolute() and path.name=='pilot-'+digest+'.json'
                and path.parent.name=='meshticulum-bench-staging' and '..' not in path.parts,
                'wrong_staged_pilot_package')
        value=self._command(unit,[row['python'],'-I','-B','-c',self.source,unit,staged['path'],digest,
            self.config['runtime_sha256'],self.config['manifest_sha256']],schema)
        require(value['unit']==unit and value['package_sha256']==digest,'wrong_native_provision_receipt')
        return value

    def prepare(self,package):
        row=self.config['units'][package['unit']]
        require(package['grant']['host_boot_id']==row['host_boot_id']
                and package['application_expires_epoch_us']<=self.config['handoff_end_epoch_us']-10000000,
                'pilot_package_outside_boot_or_cleanup_window')
        return self._package(package,'meshticulum.provisioned-public-pilot.v1')

    def seed(self,package):
        require(package['host_boot_id']==self.config['units'][package['unit']]['host_boot_id'],'public_seed_wrong_native_boot')
        return self._package(package,'meshticulum.installed-public-pilot-seed.v1')

    def install_runtime(self,unit,archive):
        require(self.runtime_source is not None and len(archive)<=2000000
                and hashlib.sha256(archive).hexdigest()==self.config['runtime_sha256']
                and self.clock()<self.deadline,'pilot_runtime_archive_changed_or_expired')
        # Same create-only private staging discipline, with a separate name and
        # bound for a binary runtime archive rather than a grant package.
        stage=STAGE.replace('100001','2000001').replace('100000','2000000')
        stage=stage.replace("'pilot-'+digest+'.json'","'runtime-'+digest+'.tar.gz'")
        stage=stage.replace('meshticulum.staged-pilot-package.v1','meshticulum.staged-pilot-runtime.v1')
        result=subprocess.run(self._argv(unit,['python3','-I','-B','-c',stage,self.config['runtime_sha256']],root=False),
            input=archive,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=min(5,self.deadline-self.clock()))
        require(result.returncode==0,'pilot_runtime_staging_failed')
        staged=one_record(result.stdout,'meshticulum.staged-pilot-runtime.v1')
        path=Path(staged['path'])
        require(path.is_absolute() and path.parent.name=='meshticulum-bench-staging'
                and path.name=='runtime-'+self.config['runtime_sha256']+'.tar.gz'
                and staged['sha256']==self.config['runtime_sha256'],'wrong_staged_pilot_runtime')
        # Check the independent host boot before the installer makes any write.
        prefix="from pathlib import Path\nimport sys\nassert Path('/proc/sys/kernel/random/boot_id').read_text().strip()==sys.argv.pop()\n"
        row=self.config['units'][unit]
        value=self._command(unit,[row['python'],'-I','-B','-c',prefix+self.runtime_source,unit,
            staged['path'],self.config['runtime_sha256'],row['host_boot_id']], 'meshticulum.native-bench-inspection.v1')
        require(value['unit']==unit and value['host_boot_id']==row['host_boot_id']
                and value['bundle_manifest_sha256']==self.config['manifest_sha256'],'wrong_installed_pilot_runtime')
        return value

    def start(self,unit,pair):
        run=identifier(pair['run_id']);require((unit,run) not in self.jobs and self.clock()<self.deadline,'pilot_job_duplicate_or_expired')
        row=self.config['units'][unit];launcher='/opt/meshticulum-bench/runtime-'+self.config['runtime_sha256'][:16]+'/bench_node.py'
        action='service-run' if pair['transport']=='lora' else 'ip-service-run'
        command=[row['python'],'-I','-B','-c',INVOKE,launcher,self.config['manifest_sha256'],action,unit,'--run-id',run]
        if unit==pair['receiver'] and pair.get('test_rx_cutoff') is not None:
            command.extend(('--test-rx-cutoff',str(pair['test_rx_cutoff'])))
        job=Job(self._argv(unit,command,root=True),password=self.passwords.get(unit),deadline=self.deadline-10,clock=self.clock)
        self.jobs[(unit,run)]=job;return job

    def status(self,unit,run):return self._action(unit,'pilot-status',run,schema='meshticulum.native-public-job-status.v1')
    def stop(self,unit,run):return self._action(unit,'pilot-stop',run,cleanup=True,schema='meshticulum.native-public-job-status.v1')
    def revoke(self,unit,run,transport):return self._action(unit,'pilot-retire',run,cleanup=True,schema='meshticulum.retired-public-pilot.v1')
    def collect(self,unit,run):return self._action(unit,'pilot-result',run,schema='meshticulum.public-pilot-result-record.v1')['result']
    def verify(self,unit,campaign,request,response):
        return self._action(unit,'pilot-verify',response,'--campaign-id',identifier(campaign),'--request-run-id',identifier(request),
                            schema='meshticulum.pilot-verified-piece.v1')
    def facts(self,unit):
        value=self._action(unit,'pilot-facts',schema='meshticulum.native-pilot-facts.v1')
        require(value['unit']==unit and value['host_boot_id']==self.config['units'][unit]['host_boot_id'],'native_host_boot_changed')
        return value
    def cache_status(self,unit,campaign):
        return self._action(unit,'pilot-cache-status',None,'--campaign-id',identifier(campaign),
                            schema='meshticulum.public-pilot-cache-status.v1')
    def reap(self,job):job.close()
