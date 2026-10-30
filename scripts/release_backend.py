"""Release backend; the trusted controller must serialize preparation and draining."""
import hashlib
import json
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from check_release_assets import ASSETS, check

REPO = '/repos/KarlSideProjects/hwmonitor-mqtt-releases'
PREFIX = 'sender-release-v1:'


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHub:
    def __init__(self, token=None, test_api=None):
        if test_api is not None:
            url = urlsplit(test_api)
            if url.scheme != 'http' or url.hostname != '127.0.0.1' or url.username or url.path or url.query or url.fragment:
                raise ValueError('test API must be loopback')
            self.api = self.upload = test_api
        else:
            if not token:
                raise ValueError('target-scoped credential required')
            self.api, self.upload = 'https://api.github.com', 'https://uploads.github.com'
        self.token = token

    def request(self, method, path, data=None, upload=False):
        headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if isinstance(data, dict):
            data = json.dumps(data, sort_keys=True, separators=(',', ':')).encode()
            headers['Content-Type'] = 'application/json'
        elif data is not None:
            headers['Content-Type'] = 'application/octet-stream'
        req = Request((self.upload if upload else self.api) + REPO + path, data=data, headers=headers, method=method)
        try:
            with build_opener(NoRedirect()).open(req, timeout=30) as response:
                content = response.read()
                return response.status, json.loads(content) if content else None
        except HTTPError as error:
            return error.code, None

    def pages(self, path):
        result = []
        for page in range(1, 10000):
            status, rows = self.request('GET', f'{path}?per_page=100&page={page}')
            if status != 200 or not isinstance(rows, list):
                raise ValueError('cannot enumerate ' + path)
            result.extend(rows)
            if len(rows) < 100:
                return result
        raise ValueError('pagination limit exceeded')


def identity(receipt):
    if not isinstance(receipt, dict) or set(receipt) != {'source', 'version', 'assets'}:
        raise ValueError('invalid trusted receipt')
    source, version, assets = receipt['source'], receipt['version'], receipt['assets']
    if not isinstance(source, str) or not re.fullmatch(r'[0-9a-f]{40}', source):
        raise ValueError('invalid source')
    if not isinstance(version, str) or not re.fullmatch(r'0\.1\.[1-9][0-9]*', version):
        raise ValueError('invalid version')
    required = {'version.json'} | set(ASSETS) | {name + '.sha256' for name in ASSETS}
    if not isinstance(assets, dict) or set(assets) != required or any(not isinstance(v, dict) or set(v) != {'size', 'digest'} or
            type(v['size']) is not int or v['size'] < 0 or not isinstance(v['digest'], str) or
            not re.fullmatch(r'sha256:[0-9a-f]{64}', v['digest']) for v in assets.values()):
        raise ValueError('invalid receipt assets')
    return int(version.split('.')[2])


def body(state, receipt):
    return PREFIX + json.dumps({'state': state, 'receipt': receipt}, sort_keys=True, separators=(',', ':'))


def parse(release):
    try:
        value = json.loads(release['body'][len(PREFIX):]) if release['body'].startswith(PREFIX) else None
        if set(value) != {'state', 'receipt'} or value['state'] not in ('STAGING', 'READY'):
            raise ValueError('invalid release state')
        ordinal = identity(value['receipt'])
        if release['tag_name'] != 'v' + value['receipt']['version'] or type(release['id']) is not int or type(release['draft']) is not bool:
            raise ValueError('release identity mismatch')
        return value['state'], value['receipt'], ordinal
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as error:
        raise ValueError('unrecognized release identity') from error


def releases(api):
    rows = api.pages('/releases')
    tags, sources = set(), set()
    for row in rows:
        state, receipt, _ = parse(row)
        if row['tag_name'] in tags or receipt['source'] in sources:
            raise ValueError('duplicate release source or tag')
        if not row['draft'] and state != 'READY':
            raise ValueError('published release is not ready')
        tags.add(row['tag_name'])
        sources.add(receipt['source'])
    return rows


def remote_assets(api, release, receipt, recover=False):
    expected = receipt['assets']
    rows = api.pages(f"/releases/{release['id']}/assets")
    seen = set()
    for asset in rows:
        name = asset.get('name')
        if name not in expected or name in seen or type(asset.get('id')) is not int:
            raise ValueError('unexpected or duplicate asset')
        seen.add(name)
        if asset.get('state') == 'starter' and recover:
            status, fresh = api.request('GET', f"/releases/{release['id']}")
            if (status != 200 or not isinstance(fresh, dict) or fresh.get('id') != release['id']
                    or fresh.get('tag_name') != release['tag_name'] or fresh.get('body') != release['body']
                    or fresh.get('draft') is not True or parse(fresh)[0] != 'STAGING'):
                raise ValueError('draft changed before starter recovery')
            status, _ = api.request('DELETE', f"/releases/assets/{asset['id']}")
            if status != 204:
                raise ValueError('starter deletion failed')
            seen.remove(name)
        elif asset.get('state') != 'uploaded' or asset.get('size') != expected[name]['size'] or asset.get('digest') != expected[name]['digest']:
            raise ValueError('asset mismatch: ' + str(name))
    return seen


def prepare_candidate(api, receipt, directory):
    """Caller supplies an independently validated receipt and serializes each source."""
    identity(receipt)
    directory = Path(directory)
    check(directory)
    metadata = json.loads((directory / 'version.json').read_text(encoding='utf-8'))
    if metadata != {'source': receipt['source'], 'version': receipt['version']}:
        raise ValueError('receipt metadata mismatch')
    actual = {p.name: {'size': p.stat().st_size, 'digest': 'sha256:' + hashlib.sha256(p.read_bytes()).hexdigest()} for p in directory.iterdir()}
    if actual != receipt['assets']:
        raise ValueError('receipt asset mismatch')
    rows = releases(api)
    matches = [r for r in rows if r['tag_name'] == 'v' + receipt['version'] or parse(r)[1]['source'] == receipt['source']]
    if matches:
        release = matches[0]
        state, saved, _ = parse(release)
        if saved != receipt or release['tag_name'] != 'v' + receipt['version']:
            raise ValueError('conflicting receipt')
        if state == 'READY':
            if remote_assets(api, release, receipt) != set(actual):
                raise ValueError('ready release incomplete')
            return release
        if not release['draft']:
            raise ValueError('published staging release')
    else:
        status, release = api.request('POST', '/releases', {'tag_name': 'v' + receipt['version'], 'name': 'v' + receipt['version'], 'body': body('STAGING', receipt), 'draft': True, 'prerelease': False})
        if status != 201 or not isinstance(release, dict) or parse(release)[:2] != ('STAGING', receipt) or not release['draft']:
            raise ValueError('draft creation failed')
    seen = remote_assets(api, release, receipt, recover=True)
    for name in sorted(set(actual) - seen):
        status, _ = api.request('POST', f"/releases/{release['id']}/assets?name={quote(name, safe='')}", (directory / name).read_bytes(), upload=True)
        if status != 201:
            raise ValueError('upload failed: ' + name)
    if remote_assets(api, release, receipt) != set(actual):
        raise ValueError('draft incomplete')
    status, ready = api.request('PATCH', f"/releases/{release['id']}", {'body': body('READY', receipt)})
    if status != 200 or parse(ready)[:2] != ('READY', receipt) or not ready['draft']:
        raise ValueError('cannot mark ready')
    return ready


def reconcile_latest(api):
    rows = releases(api)
    published = []
    for release in rows:
        state, receipt, ordinal = parse(release)
        if not release['draft']:
            if state != 'READY' or remote_assets(api, release, receipt) != set(receipt['assets']):
                raise ValueError('published release incomplete')
            published.append((ordinal, release))
    if not published:
        return
    status, latest = api.request('GET', '/releases/latest')
    target = max(published, key=lambda item: item[0])[1]
    if status == 200:
        if latest['id'] not in [r['id'] for _, r in published]:
            raise ValueError('latest is not a confirmed release')
        if latest['id'] == target['id']:
            return
    elif status != 404:
        raise ValueError('cannot read latest')
    status, promoted = api.request('PATCH', f"/releases/{target['id']}", {'make_latest': 'true'})
    if status != 200 or promoted['id'] != target['id']:
        raise ValueError('latest promotion failed')
    status, latest = api.request('GET', '/releases/latest')
    if status != 200 or latest['id'] != target['id']:
        raise ValueError('latest promotion not confirmed')


def drain_ready(api):
    """Caller serializes all drains globally; incomplete drafts remain recoverable."""
    rows = releases(api)
    ready = sorted((parse(r)[2], r) for r in rows if r['draft'] and parse(r)[0] == 'READY')
    for _, release in ready:
        _, receipt, _ = parse(release)
        if remote_assets(api, release, receipt) != set(receipt['assets']):
            raise ValueError('ready release incomplete')
        status, published = api.request('PATCH', f"/releases/{release['id']}", {'draft': False, 'make_latest': 'false'})
        if status != 200 or published['draft'] or parse(published)[:2] != ('READY', receipt):
            raise ValueError('publication failed')
    reconcile_latest(api)
