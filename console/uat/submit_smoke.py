#!/usr/bin/env python3
"""Browser UAT for submit.html and its contract mock."""
from __future__ import annotations
import argparse, json, os, socket, subprocess, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[2]; STATIC=ROOT/'console/static'; MOCK=ROOT/'console/uat/mock_submissions.py'
def port():
 with socket.socket() as s: s.bind(('127.0.0.1',0)); return s.getsockname()[1]
def start(argv,env,port_arg="--port"):
 p=port(); q=subprocess.Popen(argv+([port_arg,str(p)] if port_arg else [str(p)]),cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 for _ in range(100):
  try:
   with socket.create_connection(('127.0.0.1',p),.15): return p,q
  except OSError: time.sleep(.05)
 out,err=q.communicate(timeout=1); raise RuntimeError(f'server failed: {out[-300:]} {err[-300:]}')
def file(name,data=b'mock archive'): return {'name':name,'mimeType':'application/gzip','buffer':data}
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--out',type=Path,default=Path('/tmp/hg-submit-ui-browser-evidence.json')); ap.add_argument('--evidence-dir',type=Path,default=Path('/tmp/hg-submit-ui-browser-evidence')); a=ap.parse_args()
 env=os.environ.copy(); env['HG_MOCK_SUBMISSION_STEP_SECONDS']='0.16'; static=mock=None; checks=[]; shots=[]; errors=[]
 try:
  sp,static=start([sys.executable,'-m','http.server','--bind','127.0.0.1','--directory',str(STATIC)],env,port_arg=''); mp,mock=start([sys.executable,str(MOCK)],env); base=f'http://127.0.0.1:{sp}'; api=f'http://127.0.0.1:{mp}'; a.evidence_dir.mkdir(parents=True,exist_ok=True)
  with sync_playwright() as pw:
   b=pw.chromium.launch(headless=True)
   def page(width=1440):
    pg=b.new_page(viewport={'width':width,'height':900}); pg.on('pageerror',lambda e:errors.append(str(e))); pg.on('console',lambda m:errors.append(m.text) if m.type=='error' and 'Failed to load resource' not in m.text else None); pg.goto(f'{base}/submit.html?api={api}',wait_until='networkidle'); pg.wait_for_function("document.querySelectorAll('#scenario option').length >= 2"); return pg
   pg=page(390); pg.wait_for_timeout(100); assert pg.evaluate('()=>document.documentElement.scrollWidth<=innerWidth'); pg.screenshot(path=str(a.evidence_dir/'submit-390.png'),full_page=True); shots.append(str(a.evidence_dir/'submit-390.png')); checks.append('initial-390')
   pg.set_viewport_size({'width':1440,'height':900}); assert pg.evaluate('()=>document.documentElement.scrollWidth<=innerWidth'); pg.screenshot(path=str(a.evidence_dir/'submit-1440.png'),full_page=True); shots.append(str(a.evidence_dir/'submit-1440.png')); checks.append('initial-1440')
   pg=page(390); pg.select_option('#scenario','demo-valid'); pg.set_input_files('#archive',file('tab.tar.gz',b'tab-focus')); active=[]
   for _ in range(30):
    pg.keyboard.press('Tab'); ident=pg.evaluate('()=>document.activeElement&&document.activeElement.id');
    if ident and ident not in active: active.append(ident)
   assert 'dropzone' in active and 'scenario' in active and 'submit' in active, active; pg.locator('#dropzone').focus()
   pg.set_input_files('#archive',file('keyboard.tar.gz')); assert pg.locator('#file-row').is_visible(); checks.append('keyboard-upload')
   pg=page(); pg.select_option('#scenario','demo-valid'); pg.set_input_files('#archive',file('cancel.tar.gz',b'cancel')); methods=[]; pg.on('request',lambda r:methods.append(r.method) if '/api/submissions' in r.url else None); dialog_action={'accept':False}; pg.on('dialog',lambda d:d.accept() if dialog_action['accept'] else d.dismiss()); pg.click('#submit'); pg.wait_for_timeout(150); assert 'POST' not in methods; checks.append('confirm-cancel')
   dialog_action['accept']=True; pg.click('#submit'); pg.wait_for_function("()=>document.querySelector('#state-title')?.textContent==='已出分'",timeout=12000); assert methods.count('POST')==1; checks.append('normal-lifecycle')
   pg=page(390); pg.select_option('#scenario','demo-invalid'); pg.set_input_files('#archive',file('demo-invalid.tar.gz',b'invalid')); pg.on('dialog',lambda d:d.accept()); pg.click('#submit'); pg.wait_for_function("()=>document.querySelector('#state-title')?.textContent==='已出分'",timeout=12000)
   # The title flips before the tables finish rendering, so counting straight after it is a
   # race: measured 2026-11-02, the first read saw (3, 1, 0) and the same page showed 9 rows
   # microseconds later. Wait for the evidence table, then assert on it.
   pg.wait_for_function("()=>document.querySelectorAll('.submit-tasks tbody tr').length===9",timeout=12000)
   assert (pg.locator('.submit-pool').count(), pg.locator('.submit-warning').count(), pg.locator('.submit-tasks tbody tr').count()) == (3, 1, 9), (pg.locator('.submit-pool').count(), pg.locator('.submit-warning').count(), pg.locator('.submit-tasks tbody tr').count(), pg.locator('#result-root').inner_text()); checks.append('invalid-enrichment')
   pg=page(); pg.select_option('#scenario','demo-valid'); pg.set_input_files('#archive',file('demo-failed.tar.gz',b'failed')); pg.on('dialog',lambda d:d.accept()); pg.click('#submit'); pg.wait_for_function("()=>document.querySelector('#state-title')?.textContent==='运行失败'",timeout=12000); assert '沙箱' in pg.locator('#state-error').inner_text(), pg.locator('#state-error').inner_text(); checks.append('failed-lifecycle')
   pg=page(390); pg.select_option('#scenario','demo-unavailable'); pg.set_input_files('#archive',file('unavailable.tar.gz')); assert pg.locator('#submit').is_disabled() and '数据集' in pg.locator('#scenario-note').inner_text(); checks.append('unavailable')
   for name,data,msg in [('bad.txt',b'bad','格式不支持'),('too-big.tar.gz',b'x'*(25*1024*1024),'20 MiB')]:
    pg=page(390); pg.select_option('#scenario','demo-valid'); methods=[]; pg.on('request',lambda r:methods.append(r.method) if '/api/submissions' in r.url else None); pg.set_input_files('#archive',file(name,data)); pg.wait_for_timeout(100); assert msg in pg.locator('#submit-global-error').inner_text() and 'POST' not in methods; checks.append('client-'+name)
   for i,code in enumerate(('unsafe-path','unsafe-entry','payload-too-large','manifest-invalid','smoke-failed')):
    pg=page(); pg.select_option('#scenario','demo-valid'); pg.set_input_files('#archive',file('reject-'+code+'.tar.gz',f'{code}-{i}'.encode())); pg.on('dialog',lambda d:d.accept()); pg.click('#submit'); pg.wait_for_function("()=>document.querySelector('#state-title')?.textContent==='已拒绝'",timeout=5000); assert pg.locator('#state-error').inner_text(); checks.append('reject-'+code)
   b.close()
  result={'status':'pass','checks':checks,'screenshots':shots,'errors':errors,'static_port':sp,'mock_port':mp}
 except Exception as exc:
  result={'status':'failed','error':f'{type(exc).__name__}: {exc}','checks':checks,'screenshots':shots,'errors':errors}
 finally:
  for p in (mock,static):
   if p and p.poll() is None: p.terminate();
  for p in (mock,static):
   if p:
    try:p.wait(3)
    except subprocess.TimeoutExpired:p.kill();p.wait()
 a.out.parent.mkdir(parents=True,exist_ok=True); a.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n'); print(json.dumps(result,ensure_ascii=False,indent=2)); return 0 if result['status']=='pass' else 1
if __name__=='__main__': raise SystemExit(main())
