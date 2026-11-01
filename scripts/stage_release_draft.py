#!/usr/bin/env python3
"""Exercise draft staging against a loopback API; production is not enabled."""

import argparse
import hashlib
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from check_release_assets import check


REPO = '/repos/KarlSideProjects/hwmonitor-mqtt-releases'


def request(base, path, data=None, method=None):
    request = Request(base + path, data=data, headers={'Accept': 'application/vnd.github+json',
                      'Content-Type': 'application/octet-stream' if data else 'application/json'}, method=method)
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, json.load(response) if response.status != 204 else None
    except HTTPError as error:
        return error.code, json.load(error)


def stage(base, directory):
    check(directory)
    metadata = json.loads((directory / 'version.json').read_text(encoding='utf-8'))
    tag = 'v' + metadata['version']
    body = 'source: ' + metadata['source']
    def find_release():
        matches = []
        page = 1
        while True:
            status, releases = request(base, REPO + f'/releases?per_page=100&page={page}')
            if status != 200 or not isinstance(releases, list):
                raise ValueError('cannot enumerate releases')
            if any(item.get('body') == body and item.get('tag_name') != tag for item in releases):
                raise ValueError('source identity already assigned another tag')
            matches.extend(item for item in releases if item.get('tag_name') == tag)
            if len(releases) < 100:
                break
            page += 1
        if len(matches) > 1:
            raise ValueError('duplicate release tag')
        return matches[0] if matches else None

    release = find_release()
    if release is None:
        data = json.dumps({'tag_name': tag, 'name': tag, 'body': body, 'draft': True,
                           'prerelease': False}).encode()
        status, release = request(base, REPO + '/releases', data)
        if status == 422:
            release = find_release()
        elif status != 201:
            raise ValueError('cannot create draft')
    if (not isinstance(release, dict) or release.get('tag_name') != tag
            or release.get('body') != body or release.get('draft') is not True
            or not isinstance(release.get('id'), int)):
        raise ValueError('draft identity is missing, conflicting, or already published')
    asset_path = REPO + f"/releases/{release['id']}/assets"
    expected = {p.name: p for p in directory.iterdir()}

    def existing():
        status, assets = request(base, asset_path + '?per_page=100')
        if status != 200 or not isinstance(assets, list) or len(assets) > len(expected):
            raise ValueError('cannot enumerate draft assets')
        names = set()
        for asset in assets:
            name = asset.get('name')
            if name not in expected or name in names:
                raise ValueError('unexpected or duplicate draft asset')
            if asset.get('state') == 'starter':
                status, fresh = request(base, REPO + f"/releases/{release['id']}")
                if (status != 200 or not isinstance(fresh, dict) or fresh.get('draft') is not True
                        or fresh.get('body') != body or fresh.get('tag_name') != tag
                        or fresh.get('id') != release['id']):
                    raise ValueError('draft identity changed before recovery')
                if not isinstance(asset.get('id'), int):
                    raise ValueError('invalid starter asset ID')
                status, _ = request(base, REPO + f"/releases/assets/{asset['id']}", method='DELETE')
                if status != 204:
                    raise ValueError('starter asset deletion failed')
                continue
            names.add(name)
            data = expected[name].read_bytes()
            if (asset.get('state') != 'uploaded' or asset.get('size') != len(data)
                    or asset.get('digest') != 'sha256:' + hashlib.sha256(data).hexdigest()):
                raise ValueError(f'draft asset differs: {name}')
        return names

    names = existing()
    for name in sorted(expected.keys() - names):
        status, _ = request(base, asset_path + '?name=' + name, expected[name].read_bytes())
        if status not in (201, 422):
            raise ValueError(f'upload failed: {name}')
        names = existing()
        if name not in names:
            raise ValueError(f'upload not confirmed: {name}')
    if names != set(expected):
        raise ValueError('draft incomplete')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--test-api', required=True)
    args = parser.parse_args()
    url = urlsplit(args.test_api)
    if url.scheme != 'http' or url.hostname != '127.0.0.1' or url.path or url.query or url.fragment or url.username:
        parser.error('only a loopback test API is allowed')
    try:
        stage(args.test_api, args.directory)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'draft staging failed: {error}', file=sys.stderr)
        return 1
    print('Draft staged on test API; publication is not implemented.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
