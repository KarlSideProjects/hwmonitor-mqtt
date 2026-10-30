import hashlib
import json
import subprocess
import sys
import tarfile
import io
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/release-tests/release_endpoint.py'
SOURCE = '1' * 40


def archive(path):
    payload = json.dumps({'platform': 'linux-x64', 'source': SOURCE, 'version': '1.2.3'}).encode()
    with tarfile.open(path, 'w:gz') as bundle:
        entry = tarfile.TarInfo('manifest.json')
        entry.size = len(payload)
        bundle.addfile(entry, io.BytesIO(payload))


def test_release_endpoint_serves_only_exact_bound_assets(tmp_path):
    package = tmp_path / 'sender-linux-x64.tar.gz'
    archive(package)
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    process = subprocess.Popen([sys.executable, str(SCRIPT), '--archive', str(package), '--sha256', digest, '--source', SOURCE, '--version', '1.2.3', '--platform', 'linux-x64'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        base = process.stdout.readline().strip()
        assert base.startswith('http://127.0.0.1:')
        release = json.load(urllib.request.urlopen(base + '/releases/latest', timeout=3))
        assert release['tag_name'] == 'v1.2.3'
        assets = {item['name']: item['browser_download_url'] for item in release['assets']}
        assert json.load(urllib.request.urlopen(assets['version.json'], timeout=3)) == {'source': SOURCE, 'version': '1.2.3'}
        assert urllib.request.urlopen(assets[package.name], timeout=3).read() == package.read_bytes()
        assert urllib.request.urlopen(assets[package.name + '.sha256'], timeout=3).read().decode() == f'{digest}  {package.name}\n'
        try:
            urllib.request.urlopen(base + '/wrong/version.json', timeout=3)
            assert False, 'unexpected route accepted'
        except urllib.error.HTTPError as error:
            assert error.code == 404
    finally:
        process.terminate()
        process.wait(timeout=3)


def test_release_endpoint_rejects_wrong_source_before_listening(tmp_path):
    package = tmp_path / 'sender-linux-x64.tar.gz'
    archive(package)
    result = subprocess.run([sys.executable, str(SCRIPT), '--archive', str(package), '--sha256', hashlib.sha256(package.read_bytes()).hexdigest(), '--source', '2' * 40, '--version', '1.2.3', '--platform', 'linux-x64'], capture_output=True, text=True, timeout=3)
    assert result.returncode != 0
    assert not result.stdout


def test_acceptance_runner_rejects_wrong_host_architecture_before_docker(tmp_path):
    runner = SCRIPT.with_name('docker_acceptance.py')
    result = subprocess.run([sys.executable, str(runner), '--bootstrap', str(tmp_path / 'bootstrap'), '--archive', str(tmp_path / 'sender'), '--sha256', '0' * 64, '--source', SOURCE, '--version', '1.2.3', '--platform', 'linux-arm64', '--output', str(tmp_path / 'out')], capture_output=True, text=True, timeout=5)
    if __import__('platform').machine() in ('x86_64', 'amd64'):
        assert result.returncode != 0
        assert 'architecture' in result.stderr.lower()
        assert not (tmp_path / 'out').exists()
