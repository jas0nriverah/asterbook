"""Check the workload against the independent model and exercise real TLS."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

root=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
bench=load('capacity',root/'bench/capacity.py')
model=load('differential',root/'reference/differential.py').Model()
for cycle in range(10):
    for step in range(6):
        command,events=bench.command_plan('n','alice','X',cycle,step)
        actual=model.run(command)
        assert actual['accepted'] and actual['events']==events,(command,actual,events)
        actual_orders=[{k:v for k,v in o.items() if k!='priority'} for o in model.state()['orders']]
        expected=bench.terminal_orders('n','alice','X',cycle+(step==5),(step+1)%6)
        assert sorted(actual_orders,key=lambda x:x['id'])==sorted(expected,key=lambda x:x['id'])
    assert model.state()['orders']==[]
for shards in (1,2,4,16):
    symbols=[bench.symbol_for(i,shards) for i in range(120)]
    assert len(set(symbols))==120
    assert [bench.shard_hash(s)%shards for s in symbols]==[i%shards for i in range(120)]
    assert all(bench.shard_hash(bench.symbol_for(i,shards,True))%shards==0 for i in range(120))

binary=Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory() as temp:
    base=Path(temp);certs=base/'certs'
    subprocess.run([sys.executable,str(root/'tools/dev_certs.py'),str(certs)],check=True,stdout=subprocess.DEVNULL)
    command=[sys.executable,str(root/'bench/capacity.py'),'--server',str(binary),
             '--data-dir',str(base/'data'),'--output',str(base/'output'),'--cert-dir',str(certs),
             '--rate','40','--seconds','1','--clients','2','--shards','2','--start-delay','2']
    result=subprocess.run(command,capture_output=True,text=True,timeout=45)
    assert result.returncode==0,(result.stdout,result.stderr)
    summary=json.loads((base/'output/summary.json').read_text())
    assert summary['scheduled']==summary['accepted']==40
    assert summary['connections']==2
    assert summary['duplicate_samples']>=16
    assert all(summary['checks'].values()),summary
    # Exhaust bounded request history without filling a filesystem. Readiness
    # alone stays healthy, but new mutations must be counted as unavailable.
    exhausted=command.copy()
    exhausted[exhausted.index('--data-dir')+1]=str(base/'full-data')
    exhausted[exhausted.index('--output')+1]=str(base/'full-output')
    exhausted[exhausted.index('--seconds')+1]='2'
    exhausted.extend(['--history','8','--profile','ioc'])
    failed=subprocess.run(exhausted,capture_output=True,text=True,timeout=45)
    assert failed.returncode==1,(failed.stdout,failed.stderr)
    full=json.loads((base/'full-output/summary.json').read_text())
    assert full['codes']['HISTORY_CAPACITY']>0 and max(full['history_usage'])==8
    assert full['checks']['sampled_duplicate_payloads'] and full['checks']['state_recovered_exact']
    probes=[json.loads(s) for s in (base/'full-output/probes.jsonl').read_text().splitlines()]
    assert any(s['check']=='authenticated_ready' and s['ok'] for s in probes)
    for bad in ('nan','inf','-1','0'):
        invalid=command.copy();invalid[invalid.index('--rate')+1]=bad
        rejected=subprocess.run(invalid,capture_output=True,text=True,timeout=10)
        assert rejected.returncode==2,(bad,rejected.stdout,rejected.stderr)
