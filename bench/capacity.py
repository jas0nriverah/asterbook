#!/usr/bin/env python3
"""Bounded-book mTLS load with phased independent clients and raw outcomes."""
import argparse
import collections
import concurrent.futures
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import threading
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from client import request
from source_digest import source_digest

def percentile(values,q):
    if not values:return None
    values=sorted(values)
    return values[int(q*(len(values)-1))]

def shard_hash(text):
    h=14695981039346656037
    for b in text.encode('ascii'):h=((h^b)*1099511628211)&((1<<64)-1)
    return h

def symbol_for(client,shards,hot=False):
    shard=0 if hot else client%shards
    for i in range(10000):
        value=f'C{client}S{i}'
        if shard_hash(value)%shards==shard:return value
    raise RuntimeError('symbol routing search exhausted')

def command_plan(prefix,principal,symbol,cycle,step,profile='mixed'):
    ids={k:f'{prefix}{k}{cycle}' for k in 'BRATX'}
    common=f'V1 NEW {principal} {prefix}q{cycle}x{step} {symbol}'
    def event(kind,oid,contra,price,qty):
        return dict(type=kind,symbol=symbol,order_id=oid,contra_id=contra,price=price,qty=qty)
    if profile=='ioc':
        return f'{common} {ids["X"]} BUY IOC 100 3',[event('EXPIRE',ids['X'],'',100,3)]
    commands=[
        (f'{common} {ids["B"]} BUY GTC 99 10',[event('REST',ids['B'],'',99,10)]),
        (f'V1 REPLACE {principal} {prefix}q{cycle}x{step} {symbol} {ids["B"]} {ids["R"]} 98 8',
         [event('CANCEL',ids['B'],'',99,10),event('REST',ids['R'],'',98,8)]),
        (f'V1 CANCEL {principal} {prefix}q{cycle}x{step} {symbol} {ids["R"]}',
         [event('CANCEL',ids['R'],'',98,8)]),
        (f'{common} {ids["A"]} SELL GTC 100 5',[event('REST',ids['A'],'',100,5)]),
        (f'{common} {ids["T"]} BUY IOC 100 5',[event('TRADE',ids['T'],ids['A'],100,5)]),
        (f'{common} {ids["X"]} SELL IOC 200 3',[event('EXPIRE',ids['X'],'',200,3)]),
    ]
    return commands[step]

def terminal_orders(prefix,principal,symbol,cycle,next_step,profile='mixed'):
    if profile=='ioc' or next_step in (0,3,5):return []
    key,side,price,qty={1:('B','BUY',99,10),2:('R','BUY',98,8),4:('A','SELL',100,5)}[next_step]
    return [dict(id=f'{prefix}{key}{cycle}',owner=principal,symbol=symbol,side=side,price=price,qty=qty)]

def phase_at(offset,schedule):
    name='normal'
    for start,phase in schedule:
        if offset>=start:name=phase
    return name

def client_worker(config,index,start_ns,count,path):
    context=ssl.create_default_context(cafile=config['ca'])
    context.load_cert_chain(config['cert'],config['key'])
    symbol=symbol_for(index,config['shards'],config['hot'])
    prefix=config['namespace']+'c'+str(index)
    cycles=steps=0
    sock=None
    last_sequence=0
    codes=collections.Counter()
    outcomes=collections.Counter()
    latency=[];lag=[];sampled=[];tail=collections.deque(maxlen=8)
    accepted=duplicates=connections=0
    pending=None
    raw=Path(path)
    with raw.open('w',buffering=1024*1024) as out:
        for i in range(count):
            scheduled=start_ns+int((index+i*config['clients'])*1e9/config['rate'])
            left=(scheduled-time.monotonic_ns())/1e9
            if left>0:time.sleep(left)
            begin=time.monotonic_ns()
            phase=phase_at((scheduled-start_ns)/1e9,config['phases'])
            if begin-scheduled>config['max_lag_ms']*1e6:
                row=dict(kind='request',client=prefix,seq=i,scheduled_ns=scheduled,start_ns=None,end_ns=None,noticed_ns=begin,
                         outcome='generator_drop',code='GENERATOR_DROP',phase=phase,expected=True,rate=config['rate'])
                outcomes['generator_drop']+=1
                out.write(json.dumps(row,separators=(',',':'))+'\n')
                continue
            if pending is None:pending=command_plan(prefix,config['principal'],symbol,cycles,steps,config['profile'])
            command,events=pending
            outcome='transport_error';code='TRANSPORT_ERROR';reply=None;error=None
            try:
                if sock is None:
                    raw_sock=socket.create_connection((config['host'],config['port']),timeout=config['timeout'])
                    raw_sock.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
                    try:sock=context.wrap_socket(raw_sock,server_hostname=config['server_name'])
                    except BaseException:raw_sock.close();raise
                    sock.settimeout(config['timeout']);connections+=1
                reply=request(sock,command)
                if not isinstance(reply,dict):raise ValueError('response must be an object')
                value=reply.get('result',reply)
                if not isinstance(value,dict):raise ValueError('result must be an object')
                code=value.get('code',value.get('error','INVALID_RESPONSE'))
                if value.get('accepted') is not True:outcome='unexpected_rejection'
                elif value.get('events')!=events:outcome='mismatch'
                elif type(value.get('sequence')) is not int or value['sequence']<=last_sequence:outcome='mismatch'
                elif config['shards']>1 and reply.get('shard')!=shard_hash(symbol)%config['shards']:outcome='mismatch'
                else:
                    outcome='ok';accepted+=1;duplicates+=bool(value.get('duplicate'))
                    last_sequence=value['sequence']
                    saved=dict(command=command,reply=reply)
                    if accepted<=8 or accepted%1000==0:sampled.append(saved)
                    tail.append(saved)
                    pending=None
                    if config['profile']=='ioc':cycles+=1
                    else:
                        steps+=1
                        if steps==6:steps=0;cycles+=1
            except (OSError,EOFError,ValueError,KeyError,TypeError) as exc:
                error=repr(exc)
                if sock:
                    try:sock.close()
                    except OSError:pass
                sock=None
            end=time.monotonic_ns()
            duration=(end-scheduled)/1e6
            latency.append(duration);lag.append((begin-scheduled)/1e6)
            outcomes[outcome]+=1;codes[str(code)]+=1
            row=dict(kind='request',client=prefix,seq=i,scheduled_ns=scheduled,start_ns=begin,end_ns=end,
                     outcome=outcome,code=code,phase=phase,expected=True,rate=config['rate'],
                     duplicate=bool((reply or {}).get('result',reply or {}).get('duplicate',False)))
            if error:row['error']=error
            out.write(json.dumps(row,separators=(',',':'))+'\n')
    if sock:sock.close()
    samples=sampled+list(tail)
    unique={s['command']:s for s in samples}
    result=dict(client=index,symbol=symbol,scheduled=count,accepted=accepted,duplicate_replies=duplicates,
                outcomes=dict(outcomes),codes=dict(codes),connections=connections,
                latency_ms={f'p{int(q*100)}':percentile(latency,q) for q in (.5,.95,.99)},
                generator_lag_ms={f'p{int(q*100)}':percentile(lag,q) for q in (.5,.95,.99)},
                cycles=cycles,step=steps,ambiguous_pending=pending is not None,samples=list(unique.values()),raw=str(raw),
                expected_orders=terminal_orders(prefix,config['principal'],symbol,cycles,steps,config['profile']))
    raw.with_suffix('.summary.json').write_text(json.dumps(result))

def free_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def admin_context(certs):
    ctx=ssl.create_default_context(cafile=str(certs/'ca.crt'))
    ctx.load_cert_chain(certs/'admin.crt',certs/'admin.key')
    return ctx

def query(ctx,port,text,timeout=3):
    with socket.create_connection(('127.0.0.1',port),timeout=timeout) as raw:
        raw.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        with ctx.wrap_socket(raw,server_hostname='localhost') as sock:
            sock.settimeout(timeout)
            return request(sock,'V1 '+text)

def ready(value):
    return value.get('ok') is True or bool(value.get('shards')) and all(x['result'].get('ok') for x in value['shards'])

def resource(pid,data):
    stats=[p.stat() for p in data.rglob('*') if p.is_file()]
    common=dict(disk_bytes=sum(x.st_size for x in stats),
                disk_allocated_bytes=sum(x.st_blocks*512 for x in stats),host_load=os.getloadavg())
    if sys.platform == 'darwin':
        # ps reports RSS/VSZ in KiB. Unavailable Linux-only counters stay null.
        values=subprocess.check_output(['ps','-p',str(pid),'-o','time=,rss=,vsz='],text=True).split()
        if len(values)!=3:raise ValueError('process resource sample unavailable')
        elapsed=values[0].split(':');cpu=0.0
        for part in elapsed:cpu=cpu*60+float(part)
        return dict(cpu_seconds=cpu,memory={'VmRSS':int(values[1])*1024,'VmSize':int(values[2])*1024,'VmHWM':None},
                    io=None,fd_count=None,**common)
    base=Path('/proc')/str(pid)
    fields=(base/'stat').read_text().rsplit(')',1)[1].split()
    mem={x.split(':')[0]:int(x.split()[1])*1024 for x in (base/'status').read_text().splitlines() if x.startswith(('VmRSS:','VmHWM:','VmSize:'))}
    io={k:int(v) for k,v in (x.split(':') for x in (base/'io').read_text().splitlines())}
    return dict(cpu_seconds=(int(fields[11])+int(fields[12]))/os.sysconf('SC_CLK_TCK'),memory=mem,io=io,
                fd_count=len(list((base/'fd').iterdir())),**common)

def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--server',type=Path,required=True)
    p.add_argument('--data-dir',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--cert-dir',type=Path,required=True)
    p.add_argument('--rate',type=float,required=True)
    p.add_argument('--seconds',type=int,required=True)
    p.add_argument('--clients',type=int,default=120)
    p.add_argument('--shards',type=int,default=16)
    p.add_argument('--history',type=int,default=1000000)
    p.add_argument('--queue',type=int,default=256)
    p.add_argument('--min-free-disk-bytes',type=int,default=10*1024**3)
    p.add_argument('--profile',choices=['mixed','ioc'],default='mixed')
    p.add_argument('--hot',action='store_true')
    p.add_argument('--probe-seconds',type=float,default=5)
    p.add_argument('--max-lag-ms',type=float,default=250)
    p.add_argument('--timeout',type=float,default=2)
    p.add_argument('--start-delay',type=float,default=8)
    p.add_argument('--p99-ms',type=float,default=10)
    p.add_argument('--good-ms',type=float,default=50)
    p.add_argument('--min-good-fraction',type=float,default=.999)
    p.add_argument('--min-rate-fraction',type=float,default=.99)
    p.add_argument('--fault',choices=['none','term','kill'],default='none')
    p.add_argument('--fault-at',type=float,default=60)
    p.add_argument('--phase',default='normal')
    p.add_argument('--target-file',type=Path,help='publish current disposable endpoint for an independent observer')
    a=p.parse_args(argv)
    if (not math.isfinite(a.rate) or a.rate<=0 or a.seconds<1 or not 1<=a.clients<=120 or not 1<=a.shards<=16
        or not 1<=a.history<=1000000 or not 1<=a.queue<=65536):
        p.error('invalid rate, duration, clients, shard, history or queue bound')
    if a.min_free_disk_bytes<1:p.error('min-free-disk-bytes must be positive')
    if int(a.rate*a.seconds)<1:p.error('at least one scheduled command is required')
    if any(not math.isfinite(v) or v<=0 for v in (a.probe_seconds,a.max_lag_ms,a.timeout,a.start_delay,a.p99_ms,a.good_ms)):
        p.error('timing values must be positive and finite')
    if not 0<=a.min_good_fraction<=1 or not 0<=a.min_rate_fraction<=1:p.error('fractions must be within0..1')
    if a.output.exists() or a.data_dir.exists():p.error('output and data directories must be new')
    if a.fault!='none' and not 0<a.fault_at<a.seconds:p.error('fault-at must lie within run')
    return a

def main(argv=None):
    a=parse_args(argv);a.output.mkdir(parents=True);a.data_dir.mkdir(parents=True)
    certs=a.cert_dir;out=a.output;data=a.data_dir;port=free_port();ctx=admin_context(certs)
    args=[str(a.server.resolve()),'server','--bind','127.0.0.1','--port',str(port),'--data-dir',str(data),
          '--shards',str(a.shards),'--max-history',str(a.history),'--queue-capacity',str(a.queue),
          '--tls-cert',str(certs/'server.crt'),'--tls-key',str(certs/'server.key'),'--tls-ca',str(certs/'ca.crt'),
          '--admin-policy',str(certs/'admins.txt')]
    publish_lock=threading.Lock()
    def publish_target(stage):
        if a.target_file:
            with publish_lock:
                a.target_file.parent.mkdir(parents=True,exist_ok=True)
                temp=a.target_file.with_suffix('.new')
                temp.write_text(json.dumps(dict(port=port,cert_dir=str(certs),case=str(out),phase=a.phase,stage=stage,updated_ns=time.monotonic_ns())))
                os.replace(temp,a.target_file)
    publish_target('starting')
    log=(out/'server.log').open('w')
    proc=subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT)
    processes=[];thread=None;stop=threading.Event();faults=[];monitor_errors=[]
    observed=[]
    try:
        began=time.monotonic()
        while time.monotonic()-began<120:
            if proc.poll() is not None:raise RuntimeError('startup failed, see server.log')
            try:
                if ready(query(ctx,port,'READY')):break
            except (OSError,EOFError,ValueError):time.sleep(.05)
        else:raise TimeoutError('startup exceeded120s')
        start=time.monotonic_ns()+int(a.start_delay*1e9)
        total=int(a.rate*a.seconds);namespace=uuid.uuid4().hex[:12]
        phases=[(0,a.phase)]
        if a.fault!='none':phases.extend([(a.fault_at,'injected_'+a.fault),(a.fault_at+30,'post_fault')])
        cfg=dict(rate=a.rate,clients=a.clients,shards=a.shards,hot=a.hot,profile=a.profile,namespace=namespace,
                 ca=str(certs/'ca.crt'),cert=str(certs/'alice.crt'),key=str(certs/'alice.key'),
                 host='127.0.0.1',port=port,server_name='localhost',timeout=a.timeout,principal='alice',
                 max_lag_ms=a.max_lag_ms,phases=phases)
        plan=dict(source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                  source_tree_sha256=source_digest(),binary=str(a.server.resolve()),
                  binary_sha256=hashlib.sha256(a.server.read_bytes()).hexdigest(),command=sys.argv,
                  config=vars(a).copy(),server_command=args,start_ns=start,scheduled=total,client_config=cfg,
                  affinity=sorted(os.sched_getaffinity(0)) if hasattr(os,"sched_getaffinity") else None,warmup='no requests; start delay only prepares processes',
                  tcp_nodelay=True,
                  load_boundary='one scheduled protocol command per slot, scheduled arrival to validated decoded result')
        plan['config']={k:str(v) if isinstance(v,Path) else v for k,v in plan['config'].items()}
        (out/'plan.json').write_text(json.dumps(plan,indent=2))
        probe_index=0
        def sample_probe():
            nonlocal probe_index
            scheduled=time.monotonic_ns();begin=scheduled;ok=False;code='UNAVAILABLE'
            try:
                value=query(ctx,port,'READY',2);ok=bool(ready(value));code='OK' if ok else 'NOT_READY'
            except Exception as exc:code=type(exc).__name__
            row=dict(kind='probe',scheduled_ns=scheduled,start_ns=begin,end_ns=time.monotonic_ns(),ok=ok,code=code,
                     phase=phase_at((scheduled-start)/1e9,phases),check='authenticated_ready')
            with (out/'probes.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
            recovery=faults[-1] if faults and 'stable_ns' not in faults[-1] else None
            if probe_index%6==0 or recovery:
                begin=time.monotonic_ns();tx_ok=False;tx_code='UNAVAILABLE'
                command=f'NEW admin {namespace}p{probe_index} PCANARY {namespace}o{probe_index} BUY IOC 1 1'
                try:
                    value=query(ctx,port,command,2);value=value.get('result',value)
                    tx_ok=value.get('accepted') is True and value.get('code')=='OK'
                    tx_code=value.get('code',value.get('error','INVALID_RESPONSE'))
                except Exception as exc:tx_code=type(exc).__name__
                tx=dict(kind='probe',scheduled_ns=scheduled,start_ns=begin,end_ns=time.monotonic_ns(),ok=tx_ok,code=tx_code,
                        phase=phase_at((scheduled-start)/1e9,phases),check='transaction')
                with (out/'probes.jsonl').open('a') as f:f.write(json.dumps(tx)+'\n')
                if recovery:
                    if ok and tx_ok:
                        recovery.setdefault('first_healthy_ns',tx['end_ns'])
                        recovery['healthy_streak']=recovery.get('healthy_streak',0)+1
                        if recovery['healthy_streak']>=3:recovery['stable_ns']=tx['end_ns']
                    else:recovery['healthy_streak']=0
            probe_index+=1
        def sample_resource():
            row=dict(monotonic_ns=time.monotonic_ns(),pid=proc.pid,**resource(proc.pid,data))
            row['metrics']=query(ctx,port,'METRICS')
            row['max_history_fraction']=max(row['metrics']['history_usage'])/row['metrics']['history_capacity']
            row['disk_available_bytes']=os.statvfs(data).f_bavail*os.statvfs(data).f_frsize
            observed.append(row)
            with (out/'resources.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
            if row['max_history_fraction']>=.8 or row['memory']['VmRSS']>=16*1024**3 or row['disk_available_bytes']<a.min_free_disk_bytes:
                stop.set();monitor_errors.append('resource guardrail breached')
        def monitor():
            next_sample=time.monotonic()
            while not stop.is_set():
                publish_target('load')
                sample_probe()
                try:sample_resource()
                except Exception as exc:monitor_errors.append(repr(exc))
                next_sample+=a.probe_seconds
                stop.wait(max(0,next_sample-time.monotonic()))
        thread=threading.Thread(target=monitor,daemon=True);thread.start()
        mp=multiprocessing.get_context('spawn')
        for i in range(a.clients):
            count=max(0,(total-1-i)//a.clients+1)
            p=mp.Process(target=client_worker,args=(cfg,i,start,count,str(out/f'client{i:03}.jsonl')))
            p.start();processes.append(p)
        fault_done=False
        deadline=start/1e9+a.seconds+15
        while any(p.is_alive() for p in processes):
            now=time.monotonic_ns()
            if stop.is_set():
                for p in processes:
                    if p.is_alive():p.terminate()
                break
            if a.fault!='none' and not fault_done and now>=start+int(a.fault_at*1e9):
                fault_done=True;cut=time.monotonic_ns();oldpid=proc.pid
                if a.fault=='kill':proc.kill()
                else:proc.terminate()
                proc.wait(timeout=30)
                proc=subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT)
                faults.append(dict(kind=a.fault,cut_ns=cut,old_pid=oldpid,new_pid=proc.pid,spawn_ns=time.monotonic_ns()))
            if time.monotonic()>deadline:
                monitor_errors.append('shared client deadline exceeded')
                for p in processes:
                    if p.is_alive():p.terminate()
                break
            time.sleep(.05)
        for p in processes:p.join(timeout=5)
        publish_target('analysis')
        stop.set();thread.join(timeout=5);sample_probe()
        try:sample_resource()
        except Exception as exc:monitor_errors.append(repr(exc))
        latency=[];lag=[];outcomes=collections.Counter();codes=collections.Counter();good=0;completed=0;last=start
        summaries=[];missing=0
        for i,p in enumerate(processes):
            summary_path=out/f'client{i:03}.summary.json'
            if p.exitcode!=0 or not summary_path.exists():
                missing+=max(0,(total-1-i)//a.clients+1);continue
            summaries.append(json.loads(summary_path.read_text()))
            with (out/f'client{i:03}.jsonl').open() as f:
                for line in f:
                    row=json.loads(line);outcomes[row['outcome']]+=1;codes[row['code']]+=1
                    if row['outcome']!='generator_drop':
                        completed+=1;last=max(last,row['end_ns']);d=(row['end_ns']-row['scheduled_ns'])/1e6;latency.append(d)
                        lag.append((row['start_ns']-row['scheduled_ns'])/1e6)
                        good+=row['outcome']=='ok' and d<=a.good_ms
        last_completion_elapsed=(last-start)/1e9
        elapsed=max(float(a.seconds),last_completion_elapsed)
        before=query(ctx,port,'STATE',10);hist_before=query(ctx,port,'METRICS')['history_usage']
        states=[x['result'] for x in before['shards']] if 'shards' in before else [before]
        actual_orders=[{k:v for k,v in order.items() if k!='priority'} for state in states for order in state['orders']]
        expected_orders=[order for summary in summaries for order in summary['expected_orders']]
        model_applicable=not any(x['ambiguous_pending'] for x in summaries)
        terminal_model_ok=sorted(actual_orders,key=lambda x:x['id'])==sorted(expected_orders,key=lambda x:x['id'])
        (out/'state-before.json').write_text(json.dumps(before,sort_keys=True))
        publish_target('quiescent_restart')
        recovery_start=time.monotonic_ns();proc.terminate();proc.wait(timeout=30)
        proc=subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT);ready_ns=None
        while time.monotonic_ns()-recovery_start<120e9:
            if proc.poll() is not None:raise RuntimeError('restart failed, see server.log')
            try:
                if ready(query(ctx,port,'READY',3)):ready_ns=time.monotonic_ns();break
            except (OSError,EOFError,ValueError):time.sleep(.1)
        if ready_ns is None:raise TimeoutError('recovery exceeded120s')
        after=query(ctx,port,'STATE',10);hist_after=query(ctx,port,'METRICS')['history_usage']
        publish_target('duplicate_verification')
        (out/'state-after.json').write_text(json.dumps(after,sort_keys=True))
        retry_start=time.monotonic();retry_rows=[]
        retry_context=ssl.create_default_context(cafile=str(certs/'ca.crt'))
        retry_context.load_cert_chain(certs/'alice.crt',certs/'alice.key')
        def verify_duplicates(summary):
            result=[];connection=socket.create_connection(('127.0.0.1',port),timeout=5)
            connection.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
            with retry_context.wrap_socket(connection,server_hostname='localhost') as sock:
                for sample in summary['samples']:
                    value=request(sock,sample['command'])
                    expected=json.loads(json.dumps(sample['reply']))
                    expected.get('result',expected)['duplicate']=True
                    row=dict(client=summary['client'],command=sample['command'],passed=value==expected)
                    if value!=expected:row.update(actual=value,expected=expected)
                    result.append(row)
                    time.sleep(.05)
            return result
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            for result in pool.map(verify_duplicates,summaries):retry_rows.extend(result)
        with (out/'duplicate-retries.jsonl').open('w') as f:
            for row in retry_rows:f.write(json.dumps(row)+'\n')
        state_after_retries=query(ctx,port,'STATE',10)
        checks=dict(no_missing_results=missing==0,counts=completed+outcomes['generator_drop']+missing==total,
                    no_errors=sum(v for k,v in outcomes.items() if k not in ('ok','generator_drop'))==0,
                    no_generator_drops=outcomes['generator_drop']==0,
                    p99=bool(latency) and percentile(latency,.99)<=a.p99_ms,
                    good_fraction=good/total>=a.min_good_fraction,
                    accepted_rate=outcomes['ok']/elapsed>=a.rate*a.min_rate_fraction,
                    history_headroom=all(x['max_history_fraction']<.8 for x in observed),
                    monitor_data_complete=not monitor_errors,state_recovered_exact=before==after,history_counts_recovered=hist_before==hist_after,
                    sampled_duplicate_payloads=all(x['passed'] for x in retry_rows),duplicate_state_unchanged=state_after_retries==after,
                    recovery_under30s=(ready_ns-recovery_start)<=30e9,
                    terminal_book_model=not model_applicable or terminal_model_ok)
        if a.fault!='none':
            checks['fault_recovery_under30s']=bool(faults) and all(f.get('first_healthy_ns',float('inf'))-f['spawn_ns']<=30e9 for f in faults)
            checks['fault_three_healthy_probes']=bool(faults) and all('stable_ns' in f for f in faults)
        result=dict(config=plan['config'],scheduled=total,completed=completed,accepted=outcomes['ok'],
                    good_within_deadline=good,good_fraction=good/total,missing_results=missing,
                    outcomes=dict(outcomes),codes=dict(codes),elapsed_s=elapsed,
                    last_completion_elapsed_s=last_completion_elapsed,accepted_per_s=outcomes['ok']/elapsed,
                    latency_ms={f'p{q}':percentile(latency,q/100) for q in (50,95,99)},
                    p99_9_ms=percentile(latency,.999),generator_lag_ms={f'p{q}':percentile(lag,q/100) for q in (50,95,99)},
                    connections=sum(x['connections'] for x in summaries),history_usage=hist_before,
                    max_history_fraction=max(x['max_history_fraction'] for x in observed),
                    max_rss_bytes=max(x['memory']['VmRSS'] for x in observed),
                    disk_growth_bytes=observed[-1]['disk_bytes']-observed[0]['disk_bytes'],
                    recovery_s=(ready_ns-recovery_start)/1e9,duplicate_samples=len(retry_rows),
                    terminal_model_applicable=model_applicable,terminal_model_matches=terminal_model_ok,
                    duplicate_verification_s=time.monotonic()-retry_start,checks=checks,monitor_errors=monitor_errors,faults=faults,
                    source_tree_sha256=plan['source_tree_sha256'],binary_sha256=plan['binary_sha256'])
        (out/'summary.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result),flush=True)
        return 0 if all(checks.values()) else 1
    finally:
        stop.set()
        if thread:thread.join(timeout=5)
        for p in processes:
            if p.is_alive():p.terminate()
            p.join(timeout=5)
        if proc.poll() is None:proc.terminate();proc.wait(timeout=30)
        publish_target('stopped')
        log.close()

if __name__=='__main__':sys.exit(main())
