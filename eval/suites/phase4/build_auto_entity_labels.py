"""Generate exact-source, system-labeled entity diagnostics on a pinned free route.

Every accepted mention retains source chunk/page metadata and a verbatim quote.
The output is diagnostic, not human gold. Provider calls fail closed, checkpoint
before execution, and never use a fallback route.
"""
from __future__ import annotations
import argparse, hashlib, json, math, re, time, urllib.error, urllib.request
from pathlib import Path

API_URL='https://api.inceptionlabs.ai/v1/chat/completions'
MODEL='mercury-2'
SYSTEM=("Extract explicit statistical entities from the supplied source excerpt. Only include statistical concepts, definitions, laws/theorems, formulas, methods, examples, people, technologies, or misconceptions explicitly stated in the excerpt. For each, choose one allowed type, use a concise canonical name that appears literally inside evidence_quote, and copy an exact contiguous quote. Preserve every character. Do not infer, expand abbreviations, normalize text, or correct OCR. Omit uncertain items. Return only JSON: {\"entities\":[{\"name\":string,\"entity_type\":string,\"evidence_quote\":string}]}.")
ALLOWED={'concept','definition','theorem_or_law','formula','method_or_algorithm','example','person','tool_or_technology','misconception'}

def digest(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def load_chunks(paths:list[Path])->list[tuple[Path,dict]]:
    rows=[]
    for path in paths:
        for n,line in enumerate(path.read_text(encoding='utf-8').splitlines(),1):
            if not line.strip():continue
            row=json.loads(line)
            if any(k not in row for k in ('chunk_id','book_id','role','pages','text')):raise ValueError(f'{path}:{n} missing required provenance')
            rows.append((path,row))
    ids=[r['chunk_id'] for _,r in rows]
    if len(ids)!=len(set(ids)):raise ValueError('duplicate chunk IDs')
    return rows

def parse_json(text:str)->dict:
    text=text.strip()
    if text.startswith('```'):text=re.sub(r'^```(?:json)?\s*|\s*```$','',text,flags=re.I)
    value=json.loads(text)
    if not isinstance(value,dict) or not isinstance(value.get('entities'),list):raise ValueError('invalid entity output schema')
    return value

def split_exact(text:str)->list[str]:
    words=list(re.finditer(r'\S+',text))
    if len(words)<2:return [text]
    mid=len(words)//2
    return [text[:words[mid].start()].strip(),text[words[mid].start():].strip()]

def main()->None:
    ap=argparse.ArgumentParser()
    ap.add_argument('--chunks',nargs='+',type=Path,required=True)
    ap.add_argument('--key-file',type=Path,default=Path('/root/.config/book2course/inception-labs-api-key'))
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--checkpoint',type=Path,required=True)
    ap.add_argument('--max-tokens',type=int,default=4096)
    ap.add_argument('--limit',type=int,default=0,help='maximum provider requests in this invocation')
    ap.add_argument('--prior-conservative-tokens',type=int,default=2533767)
    ap.add_argument('--cumulative-limit',type=int,default=10000000)
    ap.add_argument('--resume',action='store_true')
    args=ap.parse_args()
    if not 1<=args.max_tokens<=4096:raise SystemExit('max_tokens must be 1..4096')
    chunks=load_chunks(args.chunks);key=args.key_file.read_text(encoding='utf-8').strip()
    if not key:raise SystemExit('empty provider key')
    hashes={str(p):digest(p) for p in args.chunks}
    state={'schema':'b2c-phase4-auto-entities/1.1','model':MODEL,'label_provenance':'system_machine_extracted','gold_eligible':False,
      'prompt_sha256':hashlib.sha256(SYSTEM.encode()).hexdigest(),'source_files':hashes,'prior_conservative_tokens':args.prior_conservative_tokens,
      'cumulative_token_limit':args.cumulative_limit,'actual_input_tokens':0,'actual_output_tokens':0,'reserved_for_calls_without_usage':0,
      'attempt_count':0,'rows':{},'failed_attempts':[],'route':'inception_mercury','fallback_policy':'none'}
    if args.resume and args.checkpoint.exists():
        old=json.loads(args.checkpoint.read_text(encoding='utf-8'))
        if old.get('model')!=MODEL or old.get('source_files')!=hashes:raise SystemExit('checkpoint identity mismatch')
        state.update(old)
        state['prior_conservative_tokens']=max(args.prior_conservative_tokens,int(old.get('prior_conservative_tokens',0)))
        state.setdefault('failed_attempts',[])
        if state.get('pending_call'):
            # An ambiguous interrupted request consumes its full reservation and is not replayed automatically.
            state['reserved_for_calls_without_usage']+=int(state['pending_call'].get('reserve_tokens',0))
            state['failed_attempts'].append({'chunk_id':state['pending_call'].get('chunk_id'),'reason_code':'interrupted_call_reservation_committed'})
            state.pop('pending_call',None)
    args.checkpoint.parent.mkdir(parents=True,exist_ok=True)
    def save():args.checkpoint.write_text(json.dumps(state,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    def current_total():return state['prior_conservative_tokens']+state['actual_input_tokens']+state['actual_output_tokens']+state['reserved_for_calls_without_usage']
    request_count=0
    def request_piece(source_path:Path,row:dict,part_text:str,part_no:int,split_count:int)->tuple[list[dict],bool]:
        nonlocal request_count
        cid=row['chunk_id'];approx=math.ceil((len(SYSTEM)+len(part_text)+500)/3.0);reserve=approx+args.max_tokens+512
        if current_total()+reserve>args.cumulative_limit:
            state['budget_stopped_before_chunk']=cid;return [],False
        pending={'chunk_id':cid,'part':part_no,'reserve_tokens':reserve,'started_at_unix':time.time()}
        state['pending_call']=pending;save()
        user={'chunk_id':cid,'part':part_no,'part_count':split_count,'book_id':row['book_id'],'role':row['role'],'pages':row['pages'],'source_excerpt':part_text}
        body={'model':MODEL,'temperature':0,'max_tokens':args.max_tokens,'messages':[{'role':'system','content':SYSTEM},{'role':'user','content':json.dumps(user,ensure_ascii=False)}]}
        req=urllib.request.Request(API_URL,data=json.dumps(body,ensure_ascii=False).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
        began=time.monotonic()
        try:
            with urllib.request.urlopen(req,timeout=180) as response:status=response.status;payload=json.loads(response.read())
        except Exception as exc:
            state['reserved_for_calls_without_usage']+=reserve;state['attempt_count']+=1;request_count+=1
            code=f'provider_http_{exc.code}' if isinstance(exc,urllib.error.HTTPError) else type(exc).__name__
            state['failed_attempts'].append({'chunk_id':cid,'part':part_no,'reason_code':code,'latency_seconds':round(time.monotonic()-began,3)})
            state.pop('pending_call',None);save();raise SystemExit(f'provider request failed on {cid} part {part_no}: {code}; no fallback')
        request_count+=1;state['attempt_count']+=1
        if status!=200 or not isinstance(payload,dict):
            state['reserved_for_calls_without_usage']+=reserve;state['failed_attempts'].append({'chunk_id':cid,'part':part_no,'reason_code':'provider_response_invalid'});state.pop('pending_call',None);save();raise SystemExit('invalid provider response; no fallback')
        returned=payload.get('model');usage=payload.get('usage') or {}
        it=int(usage.get('prompt_tokens') or usage.get('input_tokens') or 0);ot=int(usage.get('completion_tokens') or usage.get('output_tokens') or 0)
        state['actual_input_tokens']+=it;state['actual_output_tokens']+=ot
        if not it and not ot:state['reserved_for_calls_without_usage']+=reserve
        choice=(payload.get('choices') or [{}])[0];message=choice.get('message') or {};content=message.get('content')
        shape={'model':returned,'finish_reason':choice.get('finish_reason'),'content_type':type(content).__name__,'usage':{'input_tokens':it,'output_tokens':ot,'cost':usage.get('cost',usage.get('cost_usd'))}}
        state['last_response_shape']=shape
        if returned!=MODEL:
            state['failed_attempts'].append({'chunk_id':cid,'part':part_no,'reason_code':'route_model_mismatch'});state.pop('pending_call',None);save();raise SystemExit('route model mismatch; no fallback')
        if choice.get('finish_reason')=='length' or not isinstance(content,str):
            state['failed_attempts'].append({'chunk_id':cid,'part':part_no,'reason_code':'output_truncated_or_missing','latency_seconds':round(time.monotonic()-began,3)})
            state.pop('pending_call',None);save();return [],False
        try:parsed=parse_json(content)
        except (json.JSONDecodeError,ValueError):
            state['failed_attempts'].append({'chunk_id':cid,'part':part_no,'reason_code':'invalid_json','latency_seconds':round(time.monotonic()-began,3)})
            state.pop('pending_call',None);save();return [],False
        accepted=[];rejects=[]
        for index,item in enumerate(parsed['entities']):
            if not isinstance(item,dict) or set(item)!={'name','entity_type','evidence_quote'}:rejects.append({'item_index':index,'reason':'schema'});continue
            name=item['name'].strip() if isinstance(item['name'],str) else '';typ=item['entity_type'];quote=item['evidence_quote']
            if not name or typ not in ALLOWED or not isinstance(quote,str) or quote not in row['text'] or name.casefold() not in quote.casefold():rejects.append({'item_index':index,'reason':'not_exact_source_or_type'});continue
            if quote not in part_text:rejects.append({'item_index':index,'reason':'quote_outside_input_part'});continue
            accepted.append({'name':name,'entity_type':typ,'evidence_quote':quote,'chunk_id':cid,'book_id':row['book_id'],'role':row['role'],'pages':row['pages'],'source_file':str(source_path)})
        state['pending_call']['completed_usage']=shape['usage'];save()
        return accepted,True
    for source_path,row in chunks:
        cid=row['chunk_id'];existing=state['rows'].get(cid)
        if existing and existing.get('status')=='complete':continue
        # A previous truncated full-chunk response is retried as exact text halves.
        was_truncated=bool(existing and existing.get('status') in {'truncated','failed'} and (state.get('last_response_shape') or {}).get('finish_reason')=='length')
        parts=split_exact(row['text']) if was_truncated else [row['text']]
        # If an unseen chunk is large, keep its request within the canaried size.
        if len(parts)==1 and len(row['text'])>3200:parts=split_exact(row['text'])
        found=[];all_ok=True
        for part_no,part_text in enumerate(parts,1):
            accepted,ok=request_piece(source_path,row,part_text,part_no,len(parts));found.extend(accepted)
            if not ok:
                if len(parts)==1 and len(part_text)>400:
                    # Retry only the truncated item as two exact source segments; account for the failed call above.
                    halves=split_exact(part_text);found=[];all_ok=True
                    for retry_no,half in enumerate(halves,1):
                        sub_ok,sub_done=request_piece(source_path,row,half,retry_no,len(halves));found.extend(sub_ok)
                        if not sub_done:all_ok=False;break
                    if not all_ok:
                        state['rows'][cid]={'status':'failed','part_count':len(halves),'entity_count':0};save();raise SystemExit(f'truncated even after exact-span split: {cid}; no fallback')
                    break
                all_ok=False;break
        if all_ok:
            # Stable, local deduplication across split boundaries; preserve the first source mention.
            seen=set();unique=[]
            for entity in found:
                key_id=(entity['name'].casefold(),entity['entity_type'],entity['evidence_quote'])
                if key_id not in seen:seen.add(key_id);unique.append(entity)
            state['rows'][cid]={'status':'complete','part_count':len(parts),'entity_count':len(unique),'entities':unique}
        else:
            state['rows'][cid]={'status':'truncated','entity_count':0}
        state.pop('pending_call',None);save()
        if args.limit and request_count>=args.limit:break
    labels=[entity for row in state['rows'].values() if row.get('status')=='complete' for entity in row.get('entities',[])]
    state['entity_labels']=labels
    state['completed_chunks']=sum(row.get('status')=='complete' for row in state['rows'].values())
    state['failed_chunks']=[cid for cid,row in state['rows'].items() if row.get('status')!='complete']
    state['conservative_cumulative_tokens']=current_total()
    args.output.parent.mkdir(parents=True,exist_ok=True);save();args.output.write_text(json.dumps(state,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'model':MODEL,'completed_chunks':state['completed_chunks'],'entity_count':len(labels),'attempt_count':state['attempt_count'],'actual_input_tokens':state['actual_input_tokens'],'actual_output_tokens':state['actual_output_tokens'],'reserved_for_calls_without_usage':state['reserved_for_calls_without_usage'],'conservative_cumulative_tokens':state['conservative_cumulative_tokens'],'cumulative_limit':args.cumulative_limit,'failed_chunks':state['failed_chunks'],'output':str(args.output)},ensure_ascii=False))
if __name__=='__main__':main()
