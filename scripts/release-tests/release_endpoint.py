#!/usr/bin/env python3
"""Serve one explicitly pinned test release on loopback until terminated."""
import argparse
import hashlib
import io
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import re
import tarfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--platform', choices=['linux-x64', 'linux-arm64'], required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9a-f]{64}', args.sha256) or not re.fullmatch(r'[0-9a-f]{40}', args.source) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', args.version):
        parser.error('invalid identity')
    if args.archive.stat().st_size > 134217728:
        parser.error('archive too large')
    blob = args.archive.read_bytes()
    if hashlib.sha256(blob).hexdigest() != args.sha256:
        parser.error('archive checksum mismatch')
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz') as bundle:
        manifests = [entry for entry in bundle if entry.name == 'manifest.json' and entry.isfile()]
        if len(manifests) != 1 or manifests[0].size > 65536:
            parser.error('invalid manifest')
        manifest = json.load(bundle.extractfile(manifests[0]))
    if any(manifest.get(key) != getattr(args, key) for key in ('source', 'version', 'platform')):
        parser.error('archive identity mismatch')
    name = f'sender-{args.platform}.tar.gz'
    prefix = f'/releases/download/v{args.version}/'

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            base = f'http://127.0.0.1:{self.server.server_port}'
            files = {
                prefix + 'version.json': json.dumps({'source': args.source, 'version': args.version}).encode(),
                prefix + name: blob,
                prefix + name + '.sha256': f'{args.sha256}  {name}\n'.encode(),
                '/releases/latest': json.dumps({'tag_name': 'v' + args.version, 'assets': [
                    {'name': item, 'browser_download_url': base + prefix + item}
                    for item in ('version.json', name, name + '.sha256')]}).encode(),
            }
            if self.path not in files:
                self.send_error(404)
                return
            data = files[self.path]
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    with HTTPServer(('127.0.0.1', 0), Handler) as server:
        print(f'http://127.0.0.1:{server.server_port}', flush=True)
        server.serve_forever()


if __name__ == '__main__':
    main()
