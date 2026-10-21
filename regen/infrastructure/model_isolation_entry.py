"""Private container bootstrap: loopback proxy bridges to restricted host gateway."""
import base64,json,os,select,signal,socket,socketserver,subprocess,sys,threading,time
from pathlib import Path

class Bridge(socketserver.BaseRequestHandler):
    def handle(self):
        peer=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            peer.connect('/transport/gateway.sock')
            while True:
                ready,_,_=select.select([self.request,peer],[],[],1)
                for src in ready:
                    data=src.recv(65536)
                    if not data:return
                    (peer if src is self.request else self.request).sendall(data)
        except OSError:pass
        finally:peer.close()

server=socketserver.ThreadingTCPServer(('127.0.0.1',17891),Bridge);server.daemon_threads=True
threading.Thread(target=server.serve_forever,daemon=True).start()
command=json.loads(base64.b64decode(sys.argv[1]));child=subprocess.Popen(command,start_new_session=True)
def kill(*_):
    try:os.killpg(child.pid,signal.SIGKILL)
    except ProcessLookupError:pass
signal.signal(signal.SIGTERM,kill);signal.signal(signal.SIGINT,kill)
try:
    while child.poll() is None:
        if time.time()-Path('/transport/heartbeat').stat().st_mtime>10:
            kill();break
        time.sleep(.2)
    result=child.wait()
finally:kill();server.shutdown()
sys.exit(result if result>=0 else 128-result)
