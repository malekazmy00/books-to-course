import hashlib
import importlib.util
import json
import sys
from pathlib import Path
ROOT=Path('/root/projects/book2course')
DATA=Path('/mnt/wsl/b2cdata/phase1-sources')
OUTPUT=Path('/mnt/wsl/b2cdata/phase6-assets/runtime-state/results/phase6-baseline-lexical-50.json')
sys.path.insert(0,str(ROOT/'packages/contracts/src'))
sys.path.insert(0,str(ROOT/'packages/indexing/src'))
spec=importlib.util.spec_from_file_location('phase3_eval',ROOT/'eval/suites/phase3/retrieval_eval.py')
ev=importlib.util.module_from_spec(spec); spec.loader.exec_module(ev)
chunk_paths=[DATA/'lane-primary-30-chunks.jsonl',DATA/'thinkstats-supplement-7-chunks.jsonl']
raw=[row for p in chunk_paths for row in ev.load_chunks(p)]
queries=ev.validate_dataset(DATA/'phase3-qa-50.jsonl',raw,require_adjudicated=False,allow_auto_labeled=True)
selected=[row for row in queries if row['split']=='development']
from b2c_contracts.models import StructuredChunk
from b2c_indexing import lexical_search
chunks=[StructuredChunk(chunk_id=r['chunk_id'],book_id=r['book_id'],role=r['role'],section_path=r.get('section_path',[]),block_ids=r.get('block_ids') or [r['chunk_id']],pages=r['pages'],text=r['text'],token_count=max(1,int(r.get('token_count',r.get('approx_word_count',len(r['text'].split()))))),embedding_model=r.get('embedding_model','phase1-source-chunk/no-embedding')) for r in raw]
rankings={row['query_id']:[c.chunk_id for c,_ in lexical_search(row['query'],chunks,top_k=10)] for row in selected}
h=hashlib.sha256()
for p in sorted(chunk_paths,key=lambda p:p.name): h.update(p.name.encode()); h.update(b'\0'); h.update(p.read_bytes()); h.update(b'\0')
result={'schema_version':'b2c-phase6-ranking/1','retriever':'lexical_bm25_style','corpus_sha256':h.hexdigest(),'dataset_sha256':hashlib.sha256((DATA/'phase3-qa-50.jsonl').read_bytes()).hexdigest(),'split':'development','query_count':len(selected),'top_k':10,'rankings':rankings,'label_provenance':'model_auto_validated_pseudo_qrels','gold_eligible':False}
if OUTPUT.exists(): raise SystemExit(f'refusing to overwrite {OUTPUT}')
OUTPUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:result[k] for k in ('retriever','corpus_sha256','dataset_sha256','query_count','gold_eligible')},indent=2))
