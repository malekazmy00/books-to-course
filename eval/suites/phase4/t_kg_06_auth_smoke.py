"""Non-destructive T-KG-06 auth smoke for an explicitly isolated LightRAG eval service.

Run inside the LightRAG container. The negative delete probe uses a nonexistent ID.
No credential value or response body is printed or persisted.
"""
from __future__ import annotations
import argparse
import json
import os
import urllib.error
import urllib.request

DENIED={401,403}

def call(base: str, method: str, path: str, *, body=None, key: str|None=None) -> tuple[int,object|None]:
    headers={'Content-Type':'application/json'}
    if key: headers['X-API-Key']=key
    raw=None if body is None else json.dumps(body).encode()
    req=urllib.request.Request(base.rstrip('/')+path,data=raw,headers=headers,method=method)
    try:
        with urllib.request.urlopen(req,timeout=15) as response:
            data=response.read(2048)
            try: parsed=json.loads(data) if data else None
            except Exception: parsed=None
            return response.status,parsed
    except urllib.error.HTTPError as exc:
        exc.read(2048)
        return exc.code,None

def main() -> None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base-url',default='http://127.0.0.1:9621')
    ap.add_argument('--allow-isolated-eval-probes',action='store_true',help='required because this performs an unauthenticated delete of one nonexistent ID')
    args=ap.parse_args()
    if not args.allow_isolated_eval_probes or os.environ.get('B2C_PHASE4_ISOLATED_EVAL')!='1':
        raise SystemExit('refusing auth probes: pass the flag and set B2C_PHASE4_ISOLATED_EVAL=1 only inside a disposable/evaluation service')
    key=os.environ.get('LIGHTRAG_API_KEY','')
    if not key:raise SystemExit('LIGHTRAG_API_KEY is required for the authenticated control; its value is never printed')
    before_status,before=call(args.base_url,'GET','/documents/status_counts',key=key)
    if before_status!=200:raise SystemExit(f'authenticated control failed: HTTP {before_status}')
    cases=[
      ('read','GET','/documents/status_counts',None),
      ('query','POST','/query',{'query':'T-KG-06 auth smoke','mode':'mix'}),
      ('insert','POST','/documents/text',{}),
      ('delete_nonexistent','DELETE','/documents/delete_document',{'doc_ids':['b2c-tkg06-nonexistent-probe'],'delete_file':False}),
    ]
    statuses={}
    for name,method,path,body in cases:
        status,_=call(args.base_url,method,path,body=body)
        statuses[name]=status
        if status not in DENIED:raise SystemExit(f'unauthenticated {name} was not rejected as 401/403: HTTP {status}')
    after_status,after=call(args.base_url,'GET','/documents/status_counts',key=key)
    if after_status!=200 or before!=after:raise SystemExit('authenticated data counts changed during auth probes')
    print(json.dumps({'test_id':'T-KG-06','result':'PASS','authenticated_status':before_status,'unauthenticated_statuses':statuses,'data_counts_unchanged':True,'secret_printed':False},sort_keys=True))

if __name__=='__main__':main()
