#!/usr/bin/env python3
"""Offline UI preview. No recorder, camera, SSH or robot dependencies."""
import argparse
import ipaddress
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from pilot_web import asset


class PreviewHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/' and 'demo=' not in urlparse(self.path).query:
            self.send_response(302)
            self.send_header('Location', '/?demo=1')
            self.end_headers()
            return
        resource = asset(path)
        content, content_type = resource or (b'Offline preview: no device API', 'text/plain')
        self.send_response(200 if resource else 404)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        self.send_error(405, 'Offline preview never accepts device commands')

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--tailscale', action='store_true')
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), PreviewHandler)
    remote = None
    if args.tailscale:
        address=subprocess.check_output(['tailscale','ip','-4'],text=True,timeout=5).strip()
        if ipaddress.ip_address(address) not in ipaddress.ip_network('100.64.0.0/10'):raise ValueError('invalid Tailscale IP')
        remote=ThreadingHTTPServer((address,args.port),PreviewHandler)
        threading.Thread(target=remote.serve_forever,daemon=True).start()
        print('Demo only: http://%s:%d/?demo=1' % (address,args.port),flush=True)
    print('TASL FR3 demo: http://127.0.0.1:%d/?demo=1 (no devices)' % server.server_port, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if remote:remote.server_close()


if __name__ == '__main__':
    main()
