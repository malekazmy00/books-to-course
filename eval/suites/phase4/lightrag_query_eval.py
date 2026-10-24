"""Run authenticated, retrieval-only LightRAG queries and save ranked source chunk IDs."""
from __future__ import annotations
import argparse
import json
import os
import pathlib
import time
import urllib.error
import urllib.request

def main() -> None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input",type=pathlib.Path,required=True)
    ap.add_argument("--output",type=pathlib.Path,required=True)
    ap.add_argument("--base-url",default="http://127.0.0.1:9621")
    ap.add_argument("--mode",choices=("local","mix","hybrid","naive"),default="mix")
    ap.add_argument("--top-k",type=int,default=10)
    ap.add_argument("--enable-rerank",action="store_true",help="Enable LightRAG reranking only when a reranker is configured")
    ap.add_argument("--spacing-seconds",type=float,default=1.0)
    args=ap.parse_args()
    key=os.environ.get("LIGHTRAG_API_KEY")
    if not key:
        raise SystemExit("LIGHTRAG_API_KEY is required in the LightRAG container")
    rows=json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(rows,list) or not rows:
        raise SystemExit("input must be a non-empty query list")
    results={"mode":args.mode,"top_k":args.top_k,"queries":{},"records":[]}
    for i,row in enumerate(rows):
        qid=row["query_id"]
        body={"query":row["query"],"mode":args.mode,"top_k":args.top_k,"only_need_context":True,"enable_rerank":args.enable_rerank}
        request=urllib.request.Request(args.base_url+"/query/data",data=json.dumps(body).encode(),
            headers={"Content-Type":"application/json","X-API-Key":key},method="POST")
        started=time.perf_counter()
        try:
            with urllib.request.urlopen(request,timeout=900) as response:
                payload=json.load(response)
                http_status=response.status
            if payload.get("status")!="success":
                raise RuntimeError("lightRAG_non_success_status")
            data=payload.get("data") or {}
            chunks=data.get("chunks") or []
            refs=data.get("references") or []
            ordered=[]
            for item in chunks:
                source=item.get("file_path")
                if source and source not in ordered:
                    ordered.append(source)
            if not ordered:
                for item in refs:
                    source=item.get("file_path")
                    if source and source not in ordered:
                        ordered.append(source)
            elapsed=time.perf_counter()-started
            results["queries"][qid]=ordered[:args.top_k]
            results["records"].append({"query_id":qid,"http_status":http_status,
                "elapsed_seconds":round(elapsed,3),"entities_found":len(data.get("entities") or []),
                "relations_found":len(data.get("relationships") or []),
                "returned_chunks":len(chunks),"citation_ids":ordered,
                "citation_count":len(ordered)})
        except urllib.error.HTTPError as exc:
            elapsed=time.perf_counter()-started
            results["queries"][qid]=[]
            results["records"].append({"query_id":qid,"http_status":exc.code,
                "elapsed_seconds":round(elapsed,3),"error_code":"http_error"})
        except (urllib.error.URLError,TimeoutError) as exc:
            elapsed=time.perf_counter()-started
            results["queries"][qid]=[]
            results["records"].append({"query_id":qid,"http_status":None,
                "elapsed_seconds":round(elapsed,3),"error_code":type(exc).__name__})
        except Exception as exc:
            elapsed=time.perf_counter()-started
            results["queries"][qid]=[]
            results["records"].append({"query_id":qid,"http_status":None,
                "elapsed_seconds":round(elapsed,3),"error_code":type(exc).__name__})
        if (i+1)%5==0 or i+1==len(rows):
            ok=sum(1 for r in results["records"] if r.get("http_status")==200)
            print(f"progress={i+1}/{len(rows)} success={ok}")
        if i+1<len(rows) and args.spacing_seconds>0:
            time.sleep(args.spacing_seconds)
    tmp=args.output.with_suffix(args.output.suffix+".tmp")
    tmp.write_text(json.dumps(results,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    tmp.replace(args.output)

if __name__=="__main__":
    main()
