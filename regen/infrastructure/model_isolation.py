"""OS-isolated model process; host-side CONNECT gateway permits provider TLS only.

The model never receives a host network, Docker socket, project, heaps or logs.
No requests, credentials or tunneled bytes are logged by the gateway.
"""
import base64
import json
import os
import re
from pathlib import Path
import select
import socket
import socketserver
import subprocess
import threading
import time
import urllib.parse
import uuid

PROVIDER_HOSTS = frozenset({'chatgpt.com', 'api.openai.com', 'auth.openai.com'})


def destination(header):
    lines = header.decode('ascii').split('\r\n')
    parts = lines[0].split(' ')
    if len(parts)!=3 or parts[0]!='CONNECT' or parts[2] not in ('HTTP/1.0','HTTP/1.1'):
        raise ValueError('only provider TLS CONNECT is allowed')
    host, sep, port = parts[1].rpartition(':')
    if not sep or host not in PROVIDER_HOSTS or port!='443':
        raise ValueError('destination not allowed')
    return host


def headers(sock):
    data=b''
    while b'\r\n\r\n' not in data:
        if len(data)>=8192: raise ValueError('oversize proxy header')
        part=sock.recv(1)
        if not part: raise EOFError()
        data+=part
    return data


def relay(left,right,stopped):
    while not stopped.is_set():
        ready,_,_=select.select([left,right],[],[],.5)
        for src in ready:
            data=src.recv(65536)
            if not data:return
            (right if src is left else left).sendall(data)


class Gateway(socketserver.ThreadingUnixStreamServer):
    daemon_threads=True
    def __init__(self,path,proxy=None):
        self.stopped=threading.Event(); self.proxy=urllib.parse.urlparse(proxy) if proxy else None
        if self.proxy and (self.proxy.scheme!='http' or self.proxy.username or self.proxy.password or self.proxy.path not in ('','/')):
            raise ValueError('transport proxy must be an unauthenticated trusted HTTP proxy')
        super().__init__(str(path),Handler)


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        peer=None
        try:
            self.request.settimeout(15);host=destination(headers(self.request));proxy=self.server.proxy
            peer=socket.create_connection((proxy.hostname,proxy.port or 80) if proxy else (host,443),timeout=15)
            if proxy:
                peer.sendall(('CONNECT '+host+':443 HTTP/1.1\r\nHost: '+host+':443\r\n\r\n').encode())
                response=headers(peer)
                if response.split(b'\r\n',1)[0].split()[1]!=b'200':raise ValueError('transport proxy rejected provider')
            self.request.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            self.request.settimeout(None);peer.settimeout(None);relay(self.request,peer,self.server.stopped)
        except (ValueError,UnicodeError,EOFError,OSError):
            try:self.request.sendall(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n')
            except OSError:pass
        finally:
            if peer:peer.close()


class ModelIsolation:
    def __init__(self, *, base, client, cwd, home, image, proxy=None):
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', image):
            raise ValueError('model sandbox needs a pinned minimal image digest')
        self.root=Path(base)/'transport';self.root.mkdir(mode=0o700)
        self.name='regen-model-'+uuid.uuid4().hex
        self.server=Gateway(self.root/'gateway.sock',proxy);self.server_thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.server_thread.start()
        self.stop=threading.Event();self.lease=self.root/'heartbeat';self.lease.touch()
        def heartbeat():
            while not self.stop.wait(1):
                try:self.lease.touch()
                except OSError:return
        self.heartbeat=threading.Thread(target=heartbeat,daemon=True);self.heartbeat.start()
        self.client=Path(client).resolve();self.cwd=Path(cwd).resolve();self.home=Path(home).resolve();self.image=image

    def command(self,logical):
        entry=Path(__file__).with_name('model_isolation_entry.py').resolve()
        uid,gid=os.getuid(),os.getgid()
        args=['docker','run','--rm','-i','--name',self.name,'--network','none','--read-only',
              '--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=128',
              '--memory=2g','--memory-swap=2g','--cpus=2','--user',str(uid)+':'+str(gid),
              '--tmpfs','/tmp:rw,nosuid,nodev,size=268435456',
              '--mount',f'type=bind,src={self.client},dst={self.client},readonly',
              '--mount',f'type=bind,src={self.cwd},dst={self.cwd}',
              '--mount',f'type=bind,src={self.home},dst={self.home}',
              '--mount',f'type=bind,src={self.root},dst=/transport,readonly',
              '--mount',f'type=bind,src={entry},dst=/entry.py,readonly',
              '--workdir',str(self.cwd),'-e','CODEX_HOME='+str(self.home),'-e','HOME='+str(self.home),
              '-e','HTTP_PROXY=http://127.0.0.1:17891','-e','HTTPS_PROXY=http://127.0.0.1:17891',
              '-e','ALL_PROXY=http://127.0.0.1:17891','-e','NO_PROXY=','-e','LANG=C.UTF-8',
              self.image,'python3','/entry.py',base64.b64encode(json.dumps(logical).encode()).decode()]
        host = self.client.with_name('codex-code-mode-host')
        if host.is_file() and not host.is_symlink():
            index = args.index(self.image)
            args[index:index] = ['--mount', f'type=bind,src={host},dst={host},readonly']
        return args

    def evidence(self):
        return {'version':'model-os-isolation-v1','container':self.name,'image':self.image,
                'network':'none','egress_hosts':sorted(PROVIDER_HOSTS),'egress_port':443,
                'filesystem':'minimal-image-client-clean-home-empty-cwd','exclusive_os_isolation':True}

    def close(self):
        self.stop.set()
        from regen.execution.native_container import remove_owned
        try:remove_owned(self.name)
        finally:
            self.server.stopped.set();self.server.shutdown();self.server.server_close();self.heartbeat.join(timeout=2);self.server_thread.join(timeout=2)
