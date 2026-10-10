"""Run a finite, recorded single-radio matrix using the existing pinned operators.

The sudo password is read once from the controlling terminal, kept only in
memory, and sent only after the exact remote sudo prompt. Each run has fresh
grants, independently collected endpoints, and a recorded disarmed MCU reset
before a new bounded batch. A failed transfer stays in the denominator.
"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
WORK = ROOT / 'work/meshticulum-abc-deployment-20261009'
PAIRS = {'ca': ('unit-c', 'unit-a'), 'cb': ('unit-c', 'unit-b'),
         'ab': ('unit-a', 'unit-b'), 'ac': ('unit-a', 'unit-c'),
         'bc': ('unit-b', 'unit-c'), 'ba': ('unit-b', 'unit-a')}
PROMPT = b'[sudo] password for sage:'


def records(raw):
    values = []
    for line in raw.decode('utf-8').splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            values.append(value)
    return values


class Runner:
    def __init__(self, password):
        self.password = password

    def commands(self, commands, *, timeout=120, check=True):
        selector = selectors.DefaultSelector()
        jobs = []
        deadline = time.monotonic() + timeout
        try:
            for argv in commands:
                process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, bufsize=0, cwd=ROOT)
                job = {'process': process, 'output': bytearray(), 'prompts': 0}
                jobs.append(job)
                selector.register(process.stdout, selectors.EVENT_READ, job)
            while selector.get_map():
                if time.monotonic() > deadline:
                    raise TimeoutError('Bounded management operation expired')
                for key, _ in selector.select(.25):
                    job = key.data
                    part = os.read(key.fileobj.fileno(), 4096)
                    if not part:
                        selector.unregister(key.fileobj)
                        continue
                    job['output'].extend(part)
                    if len(job['output']) > 1000000:
                        raise RuntimeError('Management output limit')
                    prompts = job['output'].count(PROMPT)
                    if prompts > job['prompts']:
                        if prompts != 1 or not self.password:
                            raise RuntimeError('Unexpected or repeated sudo prompt')
                        job['process'].stdin.write(self.password.encode() + b'\n')
                        job['process'].stdin.flush()
                        job['prompts'] = prompts
            result = [{'returncode': job['process'].wait(timeout=2),
                       'output': bytes(job['output'])} for job in jobs]
            if check and any(row['returncode'] for row in result):
                # Never include arbitrary terminal output in the exception.
                raise RuntimeError('Management command failed: ' +
                                   ','.join(str(row['returncode']) for row in result))
            return result
        finally:
            selector.close()
            for job in jobs:
                p = job['process']
                if p.poll() is None:
                    p.terminate()
                    try:
                        p.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        p.kill(); p.wait(timeout=3)
                p.stdin.close(); p.stdout.close()


def provision_and_run(runner, operator, sender, receiver):
    def command(action, unit):
        return [sys.executable, str(operator), action, unit]
    # No endpoint starts RF unless both provisioning commands succeed.
    for unit in (receiver, sender):
        runner.commands([command('provision', unit)], timeout=35)
    try:
        return runner.commands([command('run', receiver), command('run', sender)],
                               timeout=115, check=False)
    except BaseException:
        # Revoke surviving authority, then stop this entire matrix. No retry.
        for unit in (receiver, sender):
            try:
                runner.commands([command('revoke', unit)], timeout=25)
            except Exception:
                pass
        raise


def write(path, value):
    data = json.dumps(value, indent=2) + '\n'
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as out:
        out.write(data); out.flush(); os.fsync(out.fileno())
    tmp.replace(path)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--series', required=True)
    p.add_argument('--pairs', nargs='+', choices=PAIRS, required=True)
    p.add_argument('--repetitions', type=int, choices=range(1, 11), required=True)
    p.add_argument('--country', choices=['MY'], required=True)
    p.add_argument('--antennas-confirmed', action='store_true', required=True)
    p.add_argument('--profile', choices=['baseline', 'sf7-250', 'sf7-500'], default='baseline')
    p.add_argument('--payload-bytes', type=int, choices=[2048, 8192, 16384], default=2048)
    args = p.parse_args()
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', args.series) or len(set(args.pairs)) != len(args.pairs):
        p.error('Use a unique simple series name and distinct pair labels')
    os.umask(0o077)
    from stage_runtime import SSH
    bundle = json.loads((WORK / 'RUNTIME_BUNDLE.json').read_text())
    if not bundle['installed']:
        raise RuntimeError('Current runtime is not installed')
    digest = bundle['sha256']
    manifest = json.loads((WORK / 'A_RUNTIME_INSTALLED.json').read_text())['bundle_manifest_sha256']
    for unit in {u for pair in args.pairs for u in PAIRS[pair]}:
        receipt = json.loads((WORK / (unit[-1].upper() + '_RUNTIME_INSTALLED.json')).read_text())
        if receipt['bundle_manifest_sha256'] != manifest:
            raise RuntimeError('Installed runtime manifests differ')
    source_files = ['prepare_run.py', 'grant_bootstrap.py', 'collect_run.py', 'reset_serial_radio.py',
                    'run_matrix.py', 'analyze_runs.py', 'make_object.py', 'stage_runtime.py']
    pins = {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest() for name in source_files}
    directory = WORK / 'matrices' / args.series
    directory.mkdir(parents=True, exist_ok=False)
    status = {'schema': 'meshticulum.finite-matrix.v1', 'series': args.series,
              'runtime_archive_sha256': digest, 'runtime_manifest_sha256': manifest,
              'source_pins': pins, 'pairs': args.pairs, 'repetitions': args.repetitions,
              'payload_bytes': args.payload_bytes, 'profile_name': args.profile,
              'physical_placement': 'Owner reports all three units together; distance and antenna orientation not measured.',
              'phase': 'prepared', 'runs': [], 'started_epoch': time.time()}
    write(directory / 'MATRIX.json', status)
    runner = Runner(getpass.getpass('Sudo password for A/B (memory only): '))
    try:
        for repetition in range(1, args.repetitions + 1):
            for pair in args.pairs:
                for name, pin in pins.items():
                    if hashlib.sha256((HERE / name).read_bytes()).hexdigest() != pin:
                        raise RuntimeError('Frozen operator source changed')
                if json.loads((WORK / 'RUNTIME_BUNDLE.json').read_text())['sha256'] != digest:
                    raise RuntimeError('Current runtime changed')
                sender, receiver = PAIRS[pair]
                row = {'pair': pair, 'repetition': repetition, 'resets': {}, 'phase': 'resetting'}
                status['runs'].append(row); write(directory / 'MATRIX.json', status)
                for unit in (sender, receiver):
                    if unit == 'unit-c': continue
                    ssh = SSH[unit][:]; ssh[1] = '-tt'
                    command = ['sudo', '/root/esp-venv-r3/bin/python', '-I', '-B', '-c',
                               (HERE / 'reset_serial_radio.py').read_text(), unit, digest, manifest]
                    answer = runner.commands([ssh + [shlex.join(command)]], timeout=35)[0]
                    receipt = records(answer['output'])
                    if len(receipt) != 1 or receipt[0]['phase'] not in ('verified_disarmed', 'verified_disarmed_no_reset'):
                        raise RuntimeError('MCU reset not independently verified')
                    path = directory / f'{pair}-{repetition:02d}-{unit}-reset.json'
                    write(path, receipt[0]); row['resets'][unit] = str(path.relative_to(WORK))
                prepared = runner.commands([[sys.executable, str(HERE / 'prepare_run.py'), sender, receiver,
                                            '--country', 'MY', '--antennas-confirmed', '--profile', args.profile,
                                            '--synthetic-bytes', str(args.payload_bytes)]], timeout=70)[0]
                plan = records(prepared['output'])
                if len(plan) != 1 or not re.fullmatch('[0-9a-f]{32}', plan[0]['run_id']):
                    raise RuntimeError('Invalid prepared-run output')
                plan = plan[0]
                operator = WORK / 'sessions' / plan['run_id'] / 'operator.py'
                if str(operator) != plan['operator'] or plan['runtime_archive_sha256'] != digest:
                    raise RuntimeError('Prepared operator/runtime mismatch')
                row.update(run_id=plan['run_id'], phase='provisioning_or_running'); status['phase']='running'; write(directory / 'MATRIX.json', status)
                print(json.dumps({'pair': pair, 'repetition': repetition, 'run_id': plan['run_id'], 'phase': 'starting'}), flush=True)
                outcomes = provision_and_run(runner, operator, sender, receiver)
                row['process_returncodes'] = [v['returncode'] for v in outcomes]
                for unit in (receiver, sender):
                    runner.commands([[sys.executable, str(operator), 'collect', unit]], timeout=35)
                output = directory / f'{pair}-{repetition:02d}.json'
                runner.commands([[sys.executable, str(HERE / 'analyze_runs.py'), str(operator.parent), '--output', str(output)]], timeout=10)
                analysis = json.loads(output.read_text())
                row.update(phase='collected', completed=analysis['completed_runs'] == 1, analysis=str(output.relative_to(WORK)))
                write(directory / 'MATRIX.json', status)
                for unit in (sender, receiver):
                    collection = json.loads((operator.parent / (unit + '-collected.json')).read_text())
                    observed = collection['result']
                    if not observed or observed.get('disarmed_on_exit') is not True or observed.get('attempt_journal', {}).get('fenced'):
                        raise RuntimeError('Uncontained/fenced run; matrix stopped without retry or reset')
                sample = analysis['runs'][0]
                print(json.dumps({'pair': pair, 'repetition': repetition, 'phase': 'collected',
                                  'completed': row['completed'], 'seconds': sample['sender_start_to_flow_ack_seconds'],
                                  'retransmits': sample.get('flow_retransmits', {}).get(sender)}), flush=True)
        status.update(phase='complete', ended_epoch=time.time())
    except BaseException as error:
        status.update(phase='stopped', ended_epoch=time.time(), error=type(error).__name__ + ':' + str(error))
        raise
    finally:
        runner.password = None
        write(directory / 'MATRIX.json', status)
    print(json.dumps({'series': args.series, 'phase': status['phase'], 'recorded': len(status['runs']),
                      'completed': sum(r.get('completed', False) for r in status['runs'])}), flush=True)


if __name__ == '__main__': main()
