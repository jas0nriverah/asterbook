#!/usr/bin/env python3
import json, os, signal, socket, struct, subprocess, sys, tempfile, time
binary=sys.argv[1]
def recvexact(s,n):
    out=b''
    while len(out)<n:
        b=s.recv(n-len(out))
        if not b: raise EOFError()
        out+=b
    return out
def frame(s):
    b=s.encode(); return struct.pack('!I',len(b))+b
def read(s): return json.loads(recvexact(s,struct.unpack('!I',recvexact(s,4))[0]))
def request(s,x): s.sendall(frame(x)); return read(s)
with tempfile.TemporaryDirectory() as d:
    probe=socket.socket(); probe.bind(('127.0.0.1',0)); port=probe.getsockname()[1]; probe.close()
    p=subprocess.Popen([binary,'server','--data-dir',d,'--port',str(port)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            try: s=socket.create_connection(('127.0.0.1',port),timeout=1); break
            except OSError: time.sleep(.05)
        else: raise AssertionError('server did not start')
        s.settimeout(5)
        data=frame('V1 NEW c r1 XYZ a SELL GTC 100 5'); s.sendall(data[:2]); s.sendall(data[2:]); first=read(s); assert first['accepted']
        s.sendall(frame('V1 NEW c r2 XYZ b BUY IOC 100 3')+frame('V1 STATE')); assert read(s)['accepted']; assert isinstance(read(s),dict)
        assert not request(s,'V1 NEW bad')['ok']
        retry=request(s,'V1 NEW c r1 XYZ a SELL GTC 100 5'); assert retry['duplicate'] and retry['sequence']==first['sequence']
        t=socket.create_connection(('127.0.0.1',port)); t.sendall(struct.pack('!I',4097)); assert t.recv(1)==b''; t.close()
        t=socket.create_connection(('127.0.0.1',port)); t.sendall(frame('V1 NEW c r3 XYZ z BUY GTC 99 1')); t.close()
        assert 'malformed' in request(s,'V1 METRICS')
        assert request(s,'V1 CHECKPOINT')['ok']
        s.close()
    finally:
        p.send_signal(signal.SIGTERM); p.wait(timeout=10)
        if p.returncode: raise RuntimeError(p.stderr.read().decode())
