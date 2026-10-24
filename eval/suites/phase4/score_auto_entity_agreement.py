"""Score independent exact-span entity labels against a frozen LightRAG entity store.

This measures model-to-model agreement for research only, not true precision/recall.
"""
from __future__ import annotations
import argparse, hashlib, json, unicodedata
from collections import defaultdict
from pathlib import Path

def norm(s:str)->str:
    s=unicodedata.normalize('NFKC',s).casefold()
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in s).split())

def sha(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def main()->None:
    ap=argparse.ArgumentParser()
    ap.add_argument('--labels',type=Path,required=True)
    ap.add_argument('--lightrag-store',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args()
    labels=json.loads(a.labels.read_text(encoding='utf-8'))
    store=a.lightrag_store
    doc_status=json.loads((store/'kv_store_doc_status.json').read_text(encoding='utf-8'))
    full_entities=json.loads((store/'kv_store_full_entities.json').read_text(encoding='utf-8'))
    source_chunk_to_doc={v.get('file_path'):k for k,v in doc_status.items() if v.get('status')=='processed' and v.get('file_path')}
    label_by_chunk=defaultdict(set)
    label_counts=defaultdict(int)
    for e in labels['entity_labels']:
        label_by_chunk[e['chunk_id']].add(norm(e['name']))
        label_counts[e['book_id']]+=1
    candidate_by_chunk=defaultdict(set)
    for cid,docid in source_chunk_to_doc.items():
        for name in full_entities.get(docid,{}).get('entity_names',[]):
            candidate_by_chunk[cid].add(norm(name))
    evaluated=sorted(set(label_by_chunk)&set(candidate_by_chunk))
    tp=fp=fn=0;by_book=defaultdict(lambda:{'tp':0,'fp':0,'fn':0,'chunks':0})
    for cid in evaluated:
        ref=label_by_chunk[cid];cand=candidate_by_chunk[cid]
        tp+=len(ref&cand);fp+=len(cand-ref);fn+=len(ref-cand)
        book=cid.split(':',1)[0]; row=by_book[book];row['tp']+=len(ref&cand);row['fp']+=len(cand-ref);row['fn']+=len(ref-cand);row['chunks']+=1
    def scores(t,p,f):
        precision=t/(t+p) if t+p else 0.0;recall=t/(t+f) if t+f else 0.0
        return {'precision':precision,'recall':recall,'f1':2*precision*recall/(precision+recall) if precision+recall else 0.0,'tp':t,'fp':p,'fn':f}
    result={'schema':'b2c-phase4-independent-entity-agreement/1.0','status':'diagnostic_only','gold_eligible':False,
      'reference_labels':'DeepSeek V4 Flash source-grounded automatic labels; exact source quotes validated; not human gold',
      'candidate_labels':'LightRAG full_entities by source document','comparison':'normalized exact-name agreement within the same source chunk; normalization=NFKC, casefold, punctuation-to-space',
      'labels_sha256':sha(a.labels),'lightrag_store_file_sha256':{name:sha(store/name) for name in ('kv_store_doc_status.json','kv_store_full_entities.json')},
      'reference_label_count':sum(label_counts.values()),'reference_counts_by_book':dict(label_counts),
      'graph_candidate_doc_count':len(source_chunk_to_doc),'evaluated_chunk_count':len(evaluated),
      'unmatched_reference_chunk_count':len(set(label_by_chunk)-set(candidate_by_chunk)),
      'overall':scores(tp,fp,fn),'by_book':{book:scores(v['tp'],v['fp'],v['fn'])|{'chunks':v['chunks']} for book,v in by_book.items()},
      'limits':['Both reference and graph entities are model outputs; the reference is independent but not human gold.','Exact normalized name matching does not credit semantic aliases.','Only chunks present in the saved LightRAG store are scored; nonoverlapping third-book chunks are reported as unavailable.','This diagnostic cannot support commercial quality claims.']}
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':result['status'],'evaluated_chunks':len(evaluated),'unmatched_reference_chunks':result['unmatched_reference_chunk_count'],'reference_entity_count':result['reference_label_count'],'overall':result['overall'],'by_book':result['by_book'],'output':str(a.output)}))
if __name__=='__main__':main()
