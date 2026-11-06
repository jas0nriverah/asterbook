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
def port():
    s=socket.socket(); s.bind(('127.0.0.1',0)); n=s.getsockname()[1]; s.close(); return n
def launch(d,n,shards=2):
    p=subprocess.Popen([binary,'server','--data-dir',d,'--port',str(n),'--shards',str(shards),'--idle-timeout-ms','1000','--frame-timeout-ms','300'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    for _ in range(100):
        try: return p,socket.create_connection(('127.0.0.1',n),timeout=1)
        except OSError:
            if p.poll() is not None: raise RuntimeError(p.stderr.read().decode())
            time.sleep(.02)
    raise RuntimeError('startup timeout')
def stop(p): p.send_signal(signal.SIGTERM); p.wait(timeout=10); assert p.returncode==0,p.stderr.read().decode()
with tempfile.TemporaryDirectory() as d:
    n=port(); p,s=launch(d,n)
    a=req(s,'V1 NEW c r X a SELL GTC 100 1'); b=req(s,'V1 NEW c r Y a SELL GTC 100 1')
    assert a['shard']!=b['shard'] and a['result']['accepted'] and b['result']['accepted']
    assert req(s,'V1 STATE')['atomic'] is False
    assert len(req(s,'V1 CHECKPOINT')['shards'])==2
    stalled=socket.create_connection(('127.0.0.1',n)); stalled.sendall(b'\x00'); time.sleep(.15); stalled.sendall(b'\x00'); time.sleep(.15); stalled.sendall(b'\x00'); time.sleep(.15)
    try: assert stalled.recv(1)==b''
    except ConnectionResetError: pass
    stalled.close(); s.close(); stop(p)
    p,s=launch(d,n); r=req(s,'V1 NEW c r X a SELL GTC 100 1'); assert r['result']['duplicate'] and r['shard']==a['shard']; s.close(); stop(p)
    bad=subprocess.run([binary,'server','--data-dir',d,'--port',str(n),'--shards','3'],capture_output=True,timeout=10); assert bad.returncode!=0 and b'manifest mismatch' in bad.stderr
