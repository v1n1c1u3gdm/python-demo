"""Opt-in HTTP proof on a disposable internal network with no host ports or volumes."""

import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path


@unittest.skipUnless(os.environ.get("CI_PROXY_HTTP_PROOF") == "1", "disposable Docker proof is opt-in")
class ProxyHttpTests(unittest.TestCase):
    @classmethod
    def docker(cls, *arguments):
        return subprocess.run(["docker", *arguments], check=True, capture_output=True, text=True).stdout

    @classmethod
    def setUpClass(cls):
        cls.network = "ci-proxy-proof-" + uuid.uuid4().hex[:12]
        cls.upstream = cls.network + "-upstream"
        cls.proxy = cls.network + "-proxy"
        cls.addClassCleanup(cls.cleanup)
        cls.docker("network", "create", "--internal", cls.network)
        program = """from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200); self.end_headers(); self.wfile.write((self.command+' '+self.path).encode())
 do_POST=do_PATCH=do_DELETE=do_PUT=do_GET
HTTPServer(('0.0.0.0',8000),Handler).serve_forever()
"""
        cls.docker("run", "-d", "--name", cls.upstream, "--network", cls.network,
                   "docker.io/library/python:3.14.7-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d",
                   "python", "-c", program)
        cls.docker("run", "-d", "--name", cls.proxy, "--network", cls.network,
                   "--entrypoint", "sleep", "nginx:1.28.0-alpine@sha256:30f1c0d78e0ad60901648be663a710bdadf19e4c10ac6782c235200619158284", "infinity")
        snippet = Path("ci/proxy/woodpecker.conf").read_text().replace("127.0.0.1:8000", cls.upstream + ":8000")
        configuration = "events {} http { server { listen 80; " + snippet + " } }"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nginx.conf"
            path.write_text(configuration)
            cls.docker("cp", str(path), cls.proxy + ":/etc/nginx/nginx.conf")
        cls.docker("exec", cls.proxy, "nginx", "-t")
        cls.docker("exec", cls.proxy, "nginx")

    @classmethod
    def cleanup(cls):
        for container in (cls.proxy, cls.upstream):
            subprocess.run(["docker", "rm", "-f", container], capture_output=True, check=False)
        subprocess.run(["docker", "network", "rm", cls.network], capture_output=True, check=False)

    def request(self, method, path):
        program = """import json,sys,urllib.request,urllib.error
try:
 response=urllib.request.urlopen(urllib.request.Request(sys.argv[1],method=sys.argv[2]),timeout=5)
except urllib.error.HTTPError as error:
 response=error
print(json.dumps([response.status,response.read().decode()]))
"""
        import json
        return json.loads(self.docker("exec", self.upstream, "python", "-c", program,
                                      "http://" + self.proxy + path, method))

    def test_real_manual_restart_and_cron_writes_never_reach_upstream(self):
        for method, path in (("POST", "/ci/api/repos/42/pipelines"),
                             ("POST", "/ci/api/repos/42/pipelines/7"),
                             ("POST", "/ci/api/repos/42/cron"),
                             ("POST", "/ci/api/repos/42/cron/daily"),
                             ("PATCH", "/ci/api/repos/42/cron/daily")):
            with self.subTest(method=method, path=path):
                # Arrange / Act
                status, _ = self.request(method, path)
                # Assert
                self.assertEqual(status, 403)

    def test_callback_hook_reads_approval_and_terminal_deletion_preserve_prefix(self):
        for method, path in (("GET", "/ci/authorize?code=fake"), ("POST", "/ci/api/hook"),
                             ("GET", "/ci/api/repos/42/pipelines"),
                             ("POST", "/ci/api/repos/42/pipelines/7/approve"),
                             ("POST", "/ci/api/repos/42/pipelines/7/cancel"),
                             ("DELETE", "/ci/api/repos/42/pipelines/7"),
                             ("DELETE", "/ci/api/repos/42/cron/daily")):
            with self.subTest(method=method, path=path):
                # Arrange / Act
                status, body = self.request(method, path)
                # Assert
                self.assertEqual(status, 200)
                self.assertEqual(body, method + " " + path)
