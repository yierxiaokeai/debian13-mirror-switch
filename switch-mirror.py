import argparse
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

SOURCE = Path('/etc/apt/sources.list')
PARTS = Path('/etc/apt/sources.list.d')
MANAGED = PARTS / 'wsl-mirrors.sources'
MAIN = Path('/etc/apt/mirrors/wsl-debian.list')
SECURITY = Path('/etc/apt/mirrors/wsl-debian-security.list')
BASES = {'huawei': 'https://repo.huaweicloud.com', 'ustc': 'https://mirrors.ustc.edu.cn'}
KEY = '[signed-by=/usr/share/keyrings/debian-archive-keyring.gpg]'


@contextmanager
def locks(paths):
    handles = []
    try:
        for path in paths:
            handle = open(path, 'a+b')
            handles.append(handle)
            try:
                fcntl.lockf(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError(f'正在进行软件包操作或另一次切换，请等待完成：{path}')
        yield
    finally:
        for handle in reversed(handles):
            handle.close()


def package_locks():
    return locks(['/var/lib/dpkg/lock-frontend', '/var/lib/dpkg/lock', '/var/lib/apt/lists/lock'])


def source_text(main, security):
    return '\n'.join([
        f'deb {KEY} {main} trixie main contrib non-free non-free-firmware',
        f'deb {KEY} {main} trixie-updates main contrib non-free non-free-firmware',
        f'deb {KEY} {security} trixie-security main contrib non-free non-free-firmware',
        f'deb {KEY} {main} trixie-backports main contrib non-free non-free-firmware',
        '', '',
    ])


def mirror_text(primary, repo):
    secondary = 'ustc' if primary == 'huawei' else 'huawei'
    return ('# Managed by WSL mirror switch; lower priority number is preferred.\n'
            f'{BASES[primary]}/{repo}/\tpriority:1\n'
            f'{BASES[secondary]}/{repo}/\tpriority:2\n')


def managed_text(types='deb'):
    return (f'# Managed by WSL mirror switch for Debian 13.\nTypes: {types}\n'
            f'URIs: mirror+file:{MAIN}\nSuites: trixie trixie-updates trixie-backports\n'
            'Components: main contrib non-free non-free-firmware\n'
            'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n\n'
            f'Types: {types}\nURIs: mirror+file:{SECURITY}\nSuites: trixie-security\n'
            'Components: main contrib non-free non-free-firmware\n'
            'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n')


def config_paths():
    return ([SOURCE] if SOURCE.exists() else []) + sorted(PARTS.glob('*.list')) + sorted(PARTS.glob('*.sources'))


def check_paths(paths):
    for path in paths:
        for candidate in [path, *path.parents]:
            if candidate.is_symlink():
                raise RuntimeError(f'发现软链接，需人工核查真实源后处理：{candidate}')
    for path, repo in [(MAIN, 'debian'), (SECURITY, 'debian-security')]:
        if path.exists() and path.read_text(encoding='utf-8') not in [mirror_text(x, repo) for x in BASES]:
            raise RuntimeError(f'镜像列表包含外部修改，已停止：{path}')
    if MANAGED.exists() and MANAGED.read_text(encoding='utf-8') not in [managed_text(), managed_text('deb deb-src')]:
        raise RuntimeError(f'本脚本的系统源文件包含外部修改，已停止：{MANAGED}')


def ensure_parser():
    try:
        import aptsources.sourceslist
        import apt_pkg
        if shutil.which('patch') and shutil.which('diff'):
            return
    except ImportError:
        pass
    print('安装 Debian 官方 APT 解析库及 patch 工具。', flush=True)
    bootstrap = Path(tempfile.mkdtemp(prefix='apt-mirror-bootstrap-'))
    lists = bootstrap / 'lists'
    lists.mkdir(mode=0o755)
    bootstrap.chmod(0o755)
    main = bootstrap / 'debian.list'
    security = bootstrap / 'security.list'
    main.write_text(mirror_text('huawei', 'debian'), encoding='utf-8')
    security.write_text(mirror_text('huawei', 'debian-security'), encoding='utf-8')
    sources = bootstrap / 'bootstrap.list'
    sources.write_text(source_text(f'mirror+file:{main}', f'mirror+file:{security}'), encoding='utf-8')
    options = ['-o', f'Dir::Etc::sourcelist={sources}', '-o', 'Dir::Etc::sourceparts=-',
               '-o', f'Dir::State::lists={lists}', '-o', 'Acquire::Retries=0',
               '-o', 'Acquire::http::Timeout=20', '-o', 'Acquire::https::Timeout=20']
    subprocess.run(['apt-get', *options, 'update', '-o', 'APT::Update::Error-Mode=any'], check=True)
    subprocess.run(['apt-get', *options, 'install', '-y', '--no-install-recommends',
                    '--no-upgrade', 'python3-apt', 'patch', 'diffutils'], check=True)
    import aptsources.sourceslist
    import apt_pkg


def is_system_uri(uri):
    if uri in [f'mirror+file:{MAIN}', f'mirror+file:{SECURITY}']:
        return True
    parsed = urlsplit(uri)
    return parsed.scheme in ['http', 'https', 'ftp', 'cdrom'] and (
        parsed.path.rstrip('/').endswith('/debian') or
        parsed.path.rstrip('/').endswith('/debian-security') or parsed.scheme == 'cdrom')


def disable_system_entries(path):
    import apt_pkg
    from aptsources.sourceslist import SourceEntry, Deb822SourceEntry
    original = path.read_bytes().decode('utf-8')
    fragments = []
    if path.suffix == '.sources':
        with path.open('rb') as handle:
            parser = apt_pkg.TagFile(handle)
            raw = original.encode('utf-8')
            start = 0
            while parser.step():
                end = parser.offset()
                content = raw[start:end].decode('utf-8')
                fragments.append((content, Deb822SourceEntry(content, str(path))))
                start = end
            if start < len(raw):
                fragments.append((raw[start:].decode('utf-8'), None))
    else:
        fragments = [(line, SourceEntry(line, str(path))) for line in original.splitlines(keepends=True)]
    changed = []
    source_code = False
    for content, entry in fragments:
        managed_legacy = entry is not None and all(uri in [f'mirror+file:{MAIN}', f'mirror+file:{SECURITY}']
                                                  for uri in entry.uris) and entry.types in [['deb'], ['deb-src']]
        if entry is None or (entry.invalid and not managed_legacy) or entry.disabled:
            changed.append(content)
            continue
        suites = set(entry.suites)
        components = set(entry.comps)
        debian = [is_system_uri(uri) for uri in entry.uris]
        system = (suites and suites <= {'trixie', 'trixie-updates', 'trixie-security',
                                       'trixie-backports', 'stable', 'stable-updates',
                                       'stable-security', 'stable-backports'} and
                  components <= {'main', 'contrib', 'non-free', 'non-free-firmware'} and any(debian))
        if not system:
            changed.append(content)
            continue
        if not all(debian):
            raise RuntimeError(f'同一源段落混有系统与第三方地址，需先分开：{path}')
        source_code = source_code or 'deb-src' in entry.types
        entry.set_enabled(False)
        changed.append(entry.str().rstrip('\n') + '\n\n' if path.suffix == '.sources' else entry.line)
    return ''.join(changed), source_code


def patch_text(path, current, wanted):
    if current == wanted:
        return
    directory = Path(tempfile.mkdtemp(prefix='apt-source-patch-'))
    old = directory / 'before'
    new = directory / 'after'
    old.write_bytes(current.encode('utf-8'))
    new.write_bytes(wanted.encode('utf-8'))
    result = subprocess.run(['diff', '-u', '--label', str(path), '--label', str(path), str(old), str(new)],
                            capture_output=True, text=True)
    if result.returncode != 1:
        raise RuntimeError(f'生成精确补丁失败：{result.stderr}')
    patch = result.stdout
    subprocess.run(['patch', '--dry-run', '--fuzz=0', str(path)], input=patch, text=True, check=True)
    subprocess.run(['patch', '--fuzz=0', str(path)], input=patch, text=True, check=True)


def write_data(path, content, stage):
    temporary = stage / path.name
    temporary.write_text(content, encoding='utf-8')
    metadata = path.stat() if path.exists() else None
    os.chmod(temporary, metadata.st_mode & 0o7777 if metadata else 0o644)
    os.chown(temporary, metadata.st_uid if metadata else 0, metadata.st_gid if metadata else 0)
    # /tmp and /etc may reside on different filesystems, so use a same-directory staging file.
    descriptor, local_name = tempfile.mkstemp(prefix='.wsl-mirror-', dir=path.parent)
    os.close(descriptor)
    local = Path(local_name)
    subprocess.run(['cp', '-a', str(temporary), str(local)], check=True)
    os.replace(local, path)


def restore(backup, before, expected, saved):
    with package_locks():
        for path in before:
            actual = path.read_bytes() if path.exists() else None
            if actual != expected[path]:
                raise RuntimeError(f'检测到并发配置修改，自动回滚暂停：{path}；备份：{backup}')
        for path in before:
            if before[path] is None:
                if path.exists():
                    shutil.move(str(path), str(backup / ('rollback-new-' + saved[path].name)))
            else:
                subprocess.run(['cp', '-a', str(saved[path]), str(path)], check=True)
    print(f'已回滚软件源配置；诊断和备份保留于：{backup}', flush=True)


def switch(primary):
    backup = Path(tempfile.mkdtemp(prefix='apt-mirror-switch-', dir='/var/backups'))
    stage = Path(tempfile.mkdtemp(prefix='apt-mirror-stage-'))
    before = {}
    expected = {}
    saved = {}
    with package_locks():
        paths = config_paths()
        files = list(dict.fromkeys([*paths, MANAGED, MAIN, SECURITY]))
        check_paths(files)
        changes = {}
        source_code = MANAGED.exists() and 'deb-src' in MANAGED.read_text(encoding='utf-8')
        for path in paths:
            if path == MANAGED:
                continue
            updated, has_source_code = disable_system_entries(path)
            source_code = source_code or has_source_code
            if updated != path.read_bytes().decode('utf-8'):
                changes[path] = updated
        for index, path in enumerate(files):
            before[path] = path.read_bytes() if path.exists() else None
            saved[path] = backup / f'{index}-{path.name}'
            if path.exists():
                subprocess.run(['cp', '-a', str(path), str(saved[path])], check=True)
        (backup / 'restore-map.txt').write_text('\n'.join(f'{saved[p].name}\t{p}' for p in files), encoding='utf-8')
        print(f'备份：{backup}', flush=True)
        MAIN.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        PARTS.mkdir(mode=0o755, parents=True, exist_ok=True)
        try:
            write_data(MAIN, mirror_text(primary, 'debian'), stage)
            write_data(SECURITY, mirror_text(primary, 'debian-security'), stage)
            write_data(MANAGED, managed_text('deb deb-src' if source_code else 'deb'), stage)
            for path, wanted in changes.items():
                patch_text(path, before[path].decode('utf-8'), wanted)
                if path.read_bytes().decode('utf-8') != wanted:
                    raise RuntimeError(f'软件源配置读回不一致：{path}')
            for path in files:
                expected[path] = path.read_bytes() if path.exists() else None
        except BaseException:
            for path in files:
                if before[path] is not None:
                    subprocess.run(['cp', '-a', str(saved[path]), str(path)], check=True)
                elif path.exists():
                    shutil.move(str(path), str(backup / ('rollback-new-' + saved[path].name)))
            raise
    print(f'正在验证：{primary} 优先，另一家自动备用。', flush=True)
    command = ['apt-get', 'update', '-o', 'APT::Update::Error-Mode=any',
               '-o', 'Acquire::Retries=0', '-o', 'Acquire::http::Timeout=20',
               '-o', 'Acquire::https::Timeout=20']
    try:
        subprocess.run(command, check=True)
    except BaseException:
        restore(backup, before, expected, saved)
        raise
    print(f'切换完成：{primary} 优先；索引刷新和签名校验通过。', flush=True)


def main():
    parser = argparse.ArgumentParser(description='Debian 13 华为云/中科大软件源切换，自动保留另一家备用。')
    parser.add_argument('mirror', choices=['huawei', 'ustc', 'status'])
    args = parser.parse_args()
    if args.mirror == 'status':
        for path in [*config_paths(), MAIN, SECURITY]:
            print(f'\n{path}\n' + (path.read_text(encoding='utf-8') if path.exists() else '尚未配置'))
        return
    if os.geteuid() != 0:
        raise RuntimeError('请使用 sudo python3 运行此脚本。')
    with locks(['/run/lock/wsl-apt-mirror-switch.lock']):
        ensure_parser()
        switch(args.mirror)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print(f'操作未完成：{error}', file=sys.stderr)
        sys.exit(1)
