"""Explicit authored fake-HTTP control; run with a prepared HG_DATASET_RUNTIME_ROOT. No real GAIA source."""
import importlib.util,json,os,pathlib,subprocess,tempfile,threading,http.server
repo=pathlib.Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('prep',repo/'data/datasets/gaia/prepare.py');p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
assert os.environ.get('HG_DATASET_RUNTIME_ROOT'), 'set an explicitly prepared native_qa runtime root'
from harnessgrad import dataset_runtime as d
runtime=d.resolve_execution('native_qa');root=pathlib.Path(tempfile.mkdtemp(prefix='gaia-managed-control-'));src=root/'http';src.mkdir()
row={'task_id':'authored','Question':'Synthetic question','Level':1,'Final answer':'42','file_name':'fixture.txt','file_path':'2023/validation/fixture.txt','Annotator Metadata':{'Steps':'PRIVATE'}}
request={'root':str(src),'row':row}
code="""import sys,json,pathlib,pyarrow as pa,pyarrow.parquet as pq
v=json.load(sys.stdin);r=pathlib.Path(v['root']);pq.write_table(pa.Table.from_pylist([v['row']]),r/'validation');v['row']['Final answer']='?';pq.write_table(pa.Table.from_pylist([v['row']]),r/'test')
"""
subprocess.run([runtime['python'],'-I','-B','-c',code],input=json.dumps(request),text=True,check=True)
body=b'authored attachment';routes={p.METADATA:(src/'validation').read_bytes(),p.METADATA_TEST:(src/'test').read_bytes(),'README.md':b'authored license notice','2023/validation/fixture.txt':body}
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  assert self.headers.get('Authorization')=='Bearer synthetic-fetch-only'
  if self.path.startswith('/api/'): data=json.dumps({'siblings':[{'rfilename':'2023/validation/fixture.txt','size':len(body)}]}).encode()
  else: data=routes[self.path.split('/resolve/'+p.adapter.REVISION+'/')[1]]
  self.send_response(200);self.end_headers();self.wfile.write(data)
 def log_message(self,*a): pass
server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start();p.ENDPOINT='http://127.0.0.1:'+str(server.server_port)
os.environ['HF_TOKEN']='synthetic-fetch-only';os.environ['OPENAI_API_KEY']='never-forward'
p.adapter.DATASET_ROOT=root/'output';p.adapter.ROWS=1
assert p.main([])==0
pool=(p.adapter.DATASET_ROOT/'pool.jsonl').read_bytes();assert b'PRIVATE' not in pool
header,row=json.loads(pool.splitlines()[0]),json.loads(pool.splitlines()[1]);assert header['test_placeholder_golds']==1 and row['attachment_sha256']==p._sha256(body)
assert (p.adapter.DATASET_ROOT/'attachments/fixture.txt').read_bytes()==body
try: p.main([])
except ValueError as e: assert 'already exists' in str(e)
else: raise AssertionError('overwrite')
assert (p.adapter.DATASET_ROOT/'pool.jsonl').read_bytes()==pool
attempt=next(p.adapter.DATASET_ROOT.glob('gaia-preparation-attempt-*'))
try: p._tables(attempt/'_src',{p.METADATA:{'sha256':'0'*64},p.METADATA_TEST:{'sha256':'0'*64}})
except ValueError as e: assert 'worker failed' in str(e)
else: raise AssertionError('corruption accepted')
server.shutdown();print('PASS managed fakeHTTP Parquet+attachments, digest mismatch rejection, preservation',root)
