#!/usr/bin/env python3
import concurrent.futures, json, signal, socket, struct, subprocess, sys, tempfile, time
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
def start(d,port):
    p=subprocess.Popen([binary,'server','--data-dir',d,'--port',str(port),'--shards','4'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    for _ in range(100):
        try: s=socket.create_connection(('127.0.0.1',port),timeout=2); s.close(); return p
        except OSError: time.sleep(.02)
    raise RuntimeError('server startup')
def stop(p):
    p.send_signal(signal.SIGTERM); p.wait(timeout=10); assert p.returncode==0,p.stderr.read().decode()
with tempfile.TemporaryDirectory() as d:
    probe=socket.socket(); probe.bind(('127.0.0.1',0)); port=probe.getsockname()[1]; probe.close()
    p=start(d,port)
    def worker(i):
        symbol=['A','B','C','D'][i%4]; rows=[]
        for j in range(20):
            cmd=f'V1 NEW c{i} r{j} {symbol} id{i}_{j} BUY GTC 100 1'
            with socket.create_connection(('127.0.0.1',port),timeout=5) as s:
                first=req(s,cmd); assert first['result']['accepted']; rows.append((cmd,first['shard'],first['result']['sequence']))
            with socket.create_connection(('127.0.0.1',port),timeout=5) as s:
                retry=req(s,cmd); assert retry['result']['duplicate'] and retry['result']['sequence']==rows[-1][2]
        return rows
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool: rows=sum(pool.map(worker,range(8)),[])
    finally: stop(p)
    p=start(d,port)
    try:
        with socket.create_connection(('127.0.0.1',port),timeout=5) as s:
            for cmd,shard,sequence in rows:
                time.sleep(.021)
                r=req(s,cmd); assert r['shard']==shard and r['result']['duplicate'] and r['result']['sequence']==sequence
            state=req(s,'V1 STATE'); assert sum(len(x['result']['orders']) for x in state['shards'])==len(rows)
    finally: stop(p)
