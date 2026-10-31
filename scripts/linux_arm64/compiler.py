#!/usr/bin/env python3
"""Private GCC driver for the two ARM sender executable links."""
import hashlib
import os
import re
import shlex
from pathlib import Path


ARM_STARTUP = {'commit': '0784374d561435f7c787a555aeab8ede699ed298',
               'entry_sha256': '14dcdc316f356e3d51217c1a2d73e7942d852d7f42be3308afce19e927a97999',
               'license_sha256': 'f9bc4423732350eb0b3f7ed7e91d530298476f8fec0c6c427a1c04ade22655af'}

PINNED_STARTFILE = '%{shared:;      pg|p|profile:%{static-pie:grcrt1.o%s;:gcrt1.o%s};      static:crt1.o%s;      static-pie:rcrt1.o%s;      !no-pie:Scrt1.o%s;      :crt1.o%s} crti.o%s    %{static:crtbeginT.o%s;      shared|static-pie|!no-pie:crtbeginS.o%s;      :crtbegin.o%s}    %{fvtable-verify=none:%s;      fvtable-verify=preinit:vtv_start_preinit.o%s;      fvtable-verify=std:vtv_start.o%s} '


def startfile_spec(source, entry):
    matches = re.findall(r'(?m)^\*startfile:\n([^\n]+)\n', source)
    if len(matches) != 1 or matches[0] != PINNED_STARTFILE:
        raise ValueError('unexpected GCC startfile spec')
    if any(c.isspace() or c in '%{}' for c in entry):
        raise ValueError('unsupported entry path')
    return '*startfile:\n' + matches[0].replace('Scrt1.o%s', entry) + '\n'


def classify(args, cwd, responses=None, build_dir=None):
    if args and all(arg.startswith(('--version', '-v', '-dump', '-print-')) for arg in args):
        return None
    expanded = []
    response_index = 0
    for arg in args:
        if arg.startswith('@'):
            path = cwd / arg[1:]
            try:
                data = path.read_bytes() if responses is None else responses[response_index]
                response_index += 1
                if b'\\' in data or b"'" in data or b'\x00' in data:
                    raise ValueError('unsupported response file quoting')
                values = shlex.split(data.decode('utf-8'), posix=True)
            except (OSError, ValueError, IndexError, UnicodeError) as error:
                raise ValueError('invalid response file') from error
            if any(value.startswith('@') for value in values):
                raise ValueError('nested response file')
            expanded.extend(values)
        else:
            expanded.append(arg)
    if any(arg in ('-c', '-E', '-S', '-shared') for arg in expanded):
        return None
    outputs = [expanded[index + 1] for index, value in enumerate(expanded[:-1]) if value == '-o']
    if len(outputs) != 1:
        raise ValueError('unknown executable link')
    output = Path(outputs[0])
    if output.name.startswith('conftest') and any(Path(arg).name.startswith('conftest.') for arg in expanded):
        return None
    if output.name == 'sender.bin' and output.parent.name == 'sender_entrypoint.dist':
        phase = 'backend'
    elif output.name == 'sender':
        phase = 'onefile'
    else:
        raise ValueError('unknown executable link')
    if build_dir is not None:
        expected = Path(build_dir) / ('sender_entrypoint.dist/sender.bin' if phase == 'backend' else 'sender')
        if os.path.abspath(os.path.join(cwd, output)) != os.path.abspath(expected):
            raise ValueError('executable output does not match build')
    forbidden = {'-nostartfiles', '-nostdlib', '-nodefaultlibs', '-static', '-static-pie', '-no-pie', '-r', '-pg', '-p', '-profile', '-fno-pie', '-fno-PIE', '-Xlinker', '-specs', '--specs', '-wrapper'}
    for value in expanded:
        allowed_linker = bool(re.fullmatch(r'-Wl,(?:-rpath=[^,]+|-R,[^,]+|--disable-new-dtags)', value))
        if value in forbidden or Path(value).name in ('crt1.o', 'Scrt1.o', 'rcrt1.o', 'gcrt1.o') or value.startswith(('-specs=', '--specs=', '-B', '-fuse-ld=', '-fplugin=')) or (value.startswith('-Wl,') and not allowed_linker):
            raise ValueError('unsupported executable link flag')
    return phase


def verified_inputs(startup):
    result = dict(ARM_STARTUP)
    for name, key in (('crt_arch.h', 'entry_sha256'), ('LICENSE.musl', 'license_sha256')):
        if hashlib.sha256((startup / name).read_bytes()).hexdigest() != result[key]:
            raise ValueError('ARM startup input differs from pinned upstream')
    return result


def run_link(gcc, args, state, cwd):
    import json
    import subprocess
    import sys
    inputs = json.loads((state / 'inputs.json').read_text(encoding='utf-8'))
    phase = classify(args, cwd, build_dir=inputs['build_dir'])
    if phase is None:
        return subprocess.run([gcc, *args], cwd=cwd).returncode
    if hashlib.sha256((state / 'entry.o').read_bytes()).hexdigest() != inputs['object_sha256']:
        raise ValueError('ARM entry object changed')
    marker = state / (phase + '.argv.json')
    try:
        with marker.open('x', encoding='utf-8') as output:
            json.dump({'cwd': str(cwd), 'args': args}, output)
    except FileExistsError as error:
        raise ValueError('duplicate executable link') from error
    response_args = [arg for arg in args if arg.startswith('@')]
    for index, arg in enumerate(response_args):
        (state / f'{phase}.response.{index}').write_bytes((cwd / arg[1:]).read_bytes())
    map_file = state / (phase + '.map')
    result = subprocess.run([gcc, *args, '-specs=' + str(state / 'startup.specs'),
                             '-Wl,-Map,' + str(map_file), '-Wl,--trace'], cwd=cwd, capture_output=True)
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    (state / (phase + '.trace')).write_bytes(result.stdout + result.stderr)
    if result.returncode:
        return result.returncode
    if hashlib.sha256((state / 'entry.o').read_bytes()).hexdigest() != inputs['object_sha256']:
        raise ValueError('ARM entry object changed')
    if any((cwd / arg[1:]).read_bytes() != (state / f'{phase}.response.{index}').read_bytes() for index, arg in enumerate(response_args)):
        raise ValueError('response file changed during link')
    entry = str(state / 'entry.o')
    trace = result.stdout.decode(errors='replace').splitlines()
    if trace.count(entry) != 1 or not map_file.is_file() or entry not in map_file.read_text(errors='replace'):
        raise ValueError('missing startup link evidence')
    if any(re.search(r'(^|/)(?:S|r|g)?crt1\.o(?:\s|$)', line) for line in trace):
        raise ValueError('forbidden GNU startup object')
    return 0


def prepare(gcc, startup, state, env, build_dir):
    import json
    import subprocess
    inputs = verified_inputs(startup)
    state.mkdir(mode=0o700)
    header = (startup / 'crt_arch.h').read_text(encoding='utf-8')
    original = '"\tmov x0, sp\\n"'
    if header.count(original) != 1:
        raise ValueError('unexpected ARM startup source')
    (state / 'entry.h').write_text(header.replace(original, '"\tmov x2, x0\\n"\n' + original), encoding='utf-8')
    crt = {}
    for name in ('crti.o', 'crtbeginS.o', 'crtendS.o', 'crtn.o'):
        path = Path(subprocess.check_output([gcc, '-print-file-name=' + name], env=env, text=True).strip())
        if not path.is_absolute() or not path.is_file():
            raise ValueError('missing CRT: ' + name)
        crt[name] = {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    spec = subprocess.check_output([gcc, '-dumpspecs'], env=env, text=True)
    (state / 'startup.specs').write_text(startfile_spec(spec, str(state / 'entry.o')), encoding='utf-8')
    subprocess.run([gcc, '-fPIE', '-fno-stack-protector', '-fno-stack-clash-protection', '-fno-profile-arcs',
                    '-fno-instrument-functions', '-I' + str(state), '-c', str(startup / 'entry.c'),
                    '-o', str(state / 'entry.o')], env=env, check=True)
    (state / 'inputs.json').write_text(json.dumps({'startup': inputs, 'crt': crt, 'build_dir': str(build_dir), 'object_sha256': hashlib.sha256((state / 'entry.o').read_bytes()).hexdigest(), 'glue_sha256': hashlib.sha256((startup / 'entry.c').read_bytes()).hexdigest()}, sort_keys=True) + '\n', encoding='utf-8')


def verify_local_crt(state):
    import json
    try:
        crt = json.loads((state / 'inputs.json').read_text(encoding='utf-8'))['crt']
        for value in crt.values():
            if hashlib.sha256(Path(value['path']).read_bytes()).hexdigest() != value['sha256']:
                raise ValueError('CRT changed during build')
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('invalid CRT evidence') from error


def link_evidence(state):
    import json
    try:
        paths = list(state.iterdir())
        if state.is_symlink() or len(paths) > 30 or any(path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size > 128 * 1024 * 1024 for path in paths):
            raise ValueError('invalid ARM evidence path')
        expected_files = {'inputs.json', 'entry.h', 'entry.o', 'startup.specs'}
        inputs = json.loads((state / 'inputs.json').read_text(encoding='utf-8'))
        if set(inputs) != {'startup', 'crt', 'glue_sha256', 'build_dir', 'object_sha256'} or not re.fullmatch(r'[a-f0-9]{64}', inputs['glue_sha256']):
            raise ValueError('invalid ARM input evidence')
        if hashlib.sha256((state / 'entry.h').read_bytes()).hexdigest() != 'fc2fe4b6503dbfb4d4c95731609236c744c32400fbb63eb7002fce2975ca8a1c':
            raise ValueError('invalid generated ARM entry')
        if inputs['startup'] != ARM_STARTUP:
            raise ValueError('ARM startup pin mismatch')
        crt = inputs['crt']
        if set(crt) != {'crti.o', 'crtbeginS.o', 'crtendS.o', 'crtn.o'}:
            raise ValueError('missing CRT evidence')
        for name, value in crt.items():
            if set(value) != {'path', 'sha256'} or Path(value['path']).name != name or not re.fullmatch('[a-f0-9]{64}', value['sha256']):
                raise ValueError('invalid CRT evidence')
        spec = (state / 'startup.specs').read_text(encoding='utf-8')
        prefix, suffix = PINNED_STARTFILE.split('Scrt1.o%s')
        match = re.fullmatch(re.escape('*startfile:\n' + prefix) + r'([^\s%{}]+)' + re.escape(suffix + '\n'), spec)
        if not match:
            raise ValueError('invalid startup spec evidence')
        entry = match.group(1)
        for phase in ('backend', 'onefile'):
            invocation = json.loads((state / (phase + '.argv.json')).read_text(encoding='utf-8'))
            if set(invocation) != {'cwd', 'args'} or not isinstance(invocation['args'], list) or not all(isinstance(arg, str) for arg in invocation['args']):
                raise ValueError('invalid compiler invocation')
            response_names = [f'{phase}.response.{i}' for i in range(sum(arg.startswith('@') for arg in invocation['args']))]
            expected_files.update(response_names)
            expected_files.update({phase + '.argv.json', phase + '.trace', phase + '.map'})
            responses = [(state / name).read_bytes() for name in response_names]
            if classify(invocation['args'], Path(invocation['cwd']), responses, inputs['build_dir']) != phase:
                raise ValueError('compiler phase mismatch')
            trace = (state / (phase + '.trace')).read_text(encoding='utf-8').splitlines()
            map_text = (state / (phase + '.map')).read_text(encoding='utf-8')
            selected = [entry, *(crt[name]['path'] for name in ('crti.o', 'crtbeginS.o', 'crtendS.o', 'crtn.o'))]
            if any(trace.count(path) != 1 or path not in map_text for path in selected) or [trace.index(path) for path in selected] != sorted(trace.index(path) for path in selected):
                raise ValueError('missing or unordered startup link evidence')
            if any(re.search(r'(^|/)(?:S|r|g)?crt1\.o(?:\s|$)', line) for line in trace + map_text.splitlines()):
                raise ValueError('forbidden GNU startup object')
        if hashlib.sha256((state / 'entry.o').read_bytes()).hexdigest() != inputs['object_sha256']:
            raise ValueError('ARM entry object mismatch')
        if {path.name for path in paths} != expected_files:
            raise ValueError('unexpected ARM evidence files')
        files = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}
        return {'startup': inputs['startup'], 'files': files}
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('missing or invalid ARM link evidence') from error


def verify_link_evidence(state, evidence):
    if link_evidence(state) != evidence:
        raise ValueError('ARM link evidence mismatch')


def verify_scons_log(log):
    lines = log.read_text(encoding='utf-8', errors='replace').splitlines()
    commands = [line for line in lines if 'Scons command:' in line]
    if any(sum(name in line for line in commands) != 1 for name in ('Backend.scons', 'Onefile.scons')):
        raise ValueError('missing SCons invocation evidence')


if __name__ == '__main__':
    import os
    import sys
    try:
        sys.exit(run_link('/usr/bin/gcc', sys.argv[1:], Path(os.environ['HWMONITOR_ARM_STATE']), Path.cwd()))
    except (OSError, ValueError, KeyError) as error:
        print('ARM compiler seam:', error, file=sys.stderr)
        sys.exit(1)
