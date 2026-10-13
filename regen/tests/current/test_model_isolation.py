"""Provider-free isolation checks: no credentials and no model request."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from regen.infrastructure.model_isolation import destination, Gateway, ModelIsolation
from regen.infrastructure.audited_llm import model_settings


class IsolationTests(unittest.TestCase):
    def test_only_named_code_mode_companion_is_mounted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            host = root/'codex-code-mode-host'
            host.touch()
            isolation = object.__new__(ModelIsolation)
            isolation.client = root/'codex'
            isolation.root = isolation.cwd = isolation.home = root
            isolation.name = 'test'
            isolation.image = 'sha256:'+'0'*64
            args = isolation.command([str(isolation.client)])
            self.assertIn(f'type=bind,src={host},dst={host},readonly',args)
            host.unlink()
            self.assertNotIn(f'type=bind,src={host},dst={host},readonly',isolation.command(['test']))

    def test_only_provider_tls_destinations(self):
        for host in ('chatgpt.com','api.openai.com','auth.openai.com'):
            self.assertEqual(destination(('CONNECT '+host+':443 HTTP/1.1\r\n\r\n').encode()),host)
        for line in ('CONNECT localhost:443 HTTP/1.1','CONNECT github.com:443 HTTP/1.1',
                     'CONNECT chatgpt.com.evil:443 HTTP/1.1','CONNECT chatgpt.com:80 HTTP/1.1',
                     'GET https://chatgpt.com HTTP/1.1','CONNECT user@chatgpt.com:443 HTTP/1.1',
                     'CONNECT auth.openai.com.evil:443 HTTP/1.1',
                     'CONNECT auth.openai.com:80 HTTP/1.1'):
            with self.subTest(line=line), self.assertRaises(ValueError):
                destination(line.encode())

    def test_live_gateway_rejects_without_upstream(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'gateway'
            server=Gateway(path)
            thread=threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                with patch('regen.infrastructure.model_isolation.socket.create_connection') as connect:
                    with socket.socket(socket.AF_UNIX) as client:
                        client.settimeout(2)
                        client.connect(str(path))
                        client.sendall(b'CONNECT github.com:443 HTTP/1.1\r\n\r\n')
                        self.assertIn(b'403',client.recv(512))
                    connect.assert_not_called()
            finally:
                server.shutdown();server.server_close();thread.join()


@unittest.skipUnless(os.environ.get('REGEN_WORKER_DOCKER_TESTS')=='1','opt-in live Docker')
class ModelContainerTests(unittest.TestCase):
    def test_real_binary_and_host_files_are_isolated(self):
        settings=model_settings()
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'cwd').mkdir();(root/'home').mkdir()
            secret=root/'host-only-sentinel';secret.write_text('must not be visible')
            isolation=ModelIsolation(base=root,client=settings['codex_bin'],cwd=root/'cwd',
                home=root/'home',image=settings['model_isolation']['image'])
            try:
                code=('import os,sys;'
                      'assert not os.path.exists(sys.argv[1]);'
                      'assert not os.path.exists("/var/run/docker.sock");'
                      'assert "REGEN_SECRET_SENTINEL" not in os.environ;'
                      'print("isolation_checks_passed")')
                with patch.dict(os.environ,{'REGEN_SECRET_SENTINEL':'must not inherit'}):
                    result=subprocess.run(isolation.command(['python3','-c',code,str(secret)]),
                                          capture_output=True,text=True,timeout=20)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('isolation_checks_passed',result.stdout)
                result=subprocess.run(isolation.command([settings['codex_bin'],'--version']),
                                      capture_output=True,text=True,timeout=20)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('codex',result.stdout.lower())
            finally:
                isolation.close()
