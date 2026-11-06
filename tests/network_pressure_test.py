#!/usr/bin/env python3
import json, socket, struct, subprocess, sys, tempfile, time, signal
binary=sys.argv[1]
def exact(s,n):
    b=b''
    while len(b)<n:
        p=s.recv(n-len(b))
        if not p: raise EOFError()
        b+=p
    return b
def req(s,x):
    b=x.encode(); s.sendall(struct.pack('!I',len(b))+b); return json.loads(exact(s,struct.unpack('!I',exact(s,4))[0]))
with tempfile.TemporaryDirectory() as d:
    probe=socket.socket(); probe.bind(('127.0.0.1',0)); port=probe.getsockname()[1]; probe.close()
    p=subprocess.Popen([binary,'server','--data-dir',d,'--port',str(port)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    sockets=[]
    try:
        for _ in range(100):
            try: control=socket.create_connection(('127.0.0.1',port),timeout=1); break
            except OSError: time.sleep(.02)
        else: raise RuntimeError('startup timeout')
        sockets.append(control)
        for _ in range(127): sockets.append(socket.create_connection(('127.0.0.1',port),timeout=2))
        time.sleep(.3)
        extra=socket.create_connection(('127.0.0.1',port),timeout=2); extra.settimeout(2); assert extra.recv(1)==b''; extra.close()
        assert req(control,'V1 METRICS')['overload']>=1
        slow=sockets[1]; slow.sendall((struct.pack('!I',10)+b'V1 METRICS')*200)
        assert 'processed' in req(control,'V1 METRICS')
    finally:
        for s in sockets: s.close()
        p.send_signal(signal.SIGTERM); p.wait(timeout=10)
        assert p.returncode==0,p.stderr.read().decode()
