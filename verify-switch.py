import importlib.util
from pathlib import Path
import subprocess
import tempfile

spec = importlib.util.spec_from_file_location('mirror', Path(__file__).with_name('switch-mirror.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
root = Path(tempfile.mkdtemp(prefix='verify-wsl-mirror-'))
real_run = subprocess.run
real_mkdtemp = tempfile.mkdtemp


def sandbox_temp(*args, **kwargs):
    if kwargs.get('dir') == '/var/backups':
        kwargs['dir'] = root
    return real_mkdtemp(*args, **kwargs)


def fail_update(command, *args, **kwargs):
    if command[0] == 'apt-get':
        raise subprocess.CalledProcessError(100, command)
    return real_run(command, *args, **kwargs)


module.tempfile.mkdtemp = sandbox_temp
module.subprocess.run = fail_update
module.package_locks = lambda: module.locks([root / 'package.lock'])
for managed in [False, True]:
    case = root / str(managed)
    case.mkdir()
    module.SOURCE = case / 'sources.list'
    module.PARTS = case / 'sources.list.d'
    module.MANAGED = module.PARTS / 'wsl-mirrors.sources'
    module.MAIN = case / 'mirrors' / 'wsl-debian.list'
    module.SECURITY = case / 'mirrors' / 'wsl-debian-security.list'
    files = [module.SOURCE, module.MANAGED, module.MAIN, module.SECURITY]
    if managed:
        module.MAIN.parent.mkdir()
        module.MAIN.write_text(module.mirror_text('huawei', 'debian'), encoding='utf-8')
        module.SECURITY.write_text(module.mirror_text('huawei', 'debian-security'), encoding='utf-8')
        source = module.source_text(f'mirror+file:{module.MAIN}', f'mirror+file:{module.SECURITY}')
    else:
        base = module.BASES['huawei']
        source = module.source_text(base + '/debian', base + '/debian-security')
    module.SOURCE.write_text(source, encoding='utf-8')
    module.SOURCE.chmod(0o640)
    before = {path: path.read_bytes() if path.exists() else None for path in files}
    try:
        module.switch('ustc')
    except subprocess.CalledProcessError as error:
        assert error.returncode == 100
    else:
        raise AssertionError('Injected refresh failure was not reported')
    for path, content in before.items():
        assert (path.read_bytes() if path.exists() else None) == content
    assert module.SOURCE.stat().st_mode & 0o777 == 0o640
    print(f'PASS failed-update rollback, initially managed={managed}', flush=True)

lock_path = root / 'concurrency.lock'
with module.locks([lock_path]):
    code = ('import fcntl,sys\n'
            'with open(sys.argv[1], "a+b") as handle:\n'
            ' try: fcntl.lockf(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)\n'
            ' except BlockingIOError: sys.exit(23)\n')
    result = real_run(['python3', '-c', code, str(lock_path)], check=False)
    assert result.returncode == 23
print('PASS concurrent switch lock rejection', flush=True)

module.subprocess.run = real_run
case = root / 'mixed-formats'
case.mkdir()
module.SOURCE = case / 'sources.list'
module.PARTS = case / 'sources.list.d'
module.PARTS.mkdir()
module.MANAGED = module.PARTS / 'wsl-mirrors.sources'
module.MAIN = case / 'mirrors' / 'wsl-debian.list'
module.SECURITY = case / 'mirrors' / 'wsl-debian-security.list'
third_party = 'deb [signed-by=/example/vendor.gpg] https://vendor.example/repo trixie main\n'
classic = ('# user comment\n' + third_party +
           'deb [signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://deb.debian.org/debian trixie main\n' +
           'deb-src http://mirrors.aliyun.com/debian trixie main contrib')
module.SOURCE.write_text(classic, encoding='utf-8')
third_section = ('# vendor comment\nTypes: deb\nURIs: https://vendor.example/repo\n'
                 'Suites: trixie\nComponents: main\nSigned-By: /example/vendor.gpg\n\n')
official_section = ('# official comment\nTypes: deb\nURIs: https://deb.debian.org/debian\n'
                    'Suites: trixie trixie-updates\nComponents: main non-free-firmware\n'
                    'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n\n')
deb822 = module.PARTS / 'mixed.sources'
deb822.write_text(third_section + official_section, encoding='utf-8')
for path in [module.SOURCE, deb822]:
    original = path.read_bytes().decode('utf-8')
    updated, has_source_code = module.disable_system_entries(path)
    assert updated != original
    assert (third_party if path == module.SOURCE else third_section) in updated
    assert has_source_code == (path == module.SOURCE)
    module.patch_text(path, original, updated)
    assert path.read_bytes().decode('utf-8') == updated
    repeated, _ = module.disable_system_entries(path)
    assert repeated == updated
print('PASS official/third-party mixed .list/.sources preservation and repeat execution', flush=True)

crlf = module.PARTS / 'third-party.list'
crlf.write_bytes(third_party.replace('\n', '\r\n').encode('utf-8'))
assert module.disable_system_entries(crlf)[0].encode('utf-8') == crlf.read_bytes()
print('PASS untouched third-party CRLF preservation', flush=True)
legacy_mirror = module.PARTS / 'legacy-mirror.list'
legacy_mirror.write_text(module.source_text(f'mirror+file:{module.MAIN}', f'mirror+file:{module.SECURITY}'), encoding='utf-8')
updated, _ = module.disable_system_entries(legacy_mirror)
assert updated.count('#deb ') == 4
print('PASS legacy mirror+file source migration', flush=True)
print(f'Diagnostics retained: {root}', flush=True)
