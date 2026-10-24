#!/usr/bin/env python3
"""Page-level OCR evaluation with NFC grapheme CER, whitespace-token WER and title splits."""
from __future__ import annotations
import argparse,json,math,statistics,unicodedata
from collections import defaultdict
from pathlib import Path

def normalize(text): return unicodedata.normalize("NFC",text.replace("\r\n","\n").replace("\r","\n"))
def graphemes(text):
    units=[]; join_next=False
    for char in normalize(text):
        category,code=unicodedata.category(char),ord(char)
        variation=0xFE00<=code<=0xFE0F or 0xE0100<=code<=0xE01EF
        if units and (unicodedata.combining(char) or category in {"Mc","Me"} or variation or join_next or char=="\u200d"): units[-1]+=char
        else: units.append(char)
        join_next=char=="\u200d"
    return units
def edit_distance(left,right):
    a,b=list(left),list(right)
    if len(a)<len(b): a,b=b,a
    previous=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        current=[i]
        for j,y in enumerate(b,1): current.append(min(current[-1]+1,previous[j]+1,previous[j-1]+(x!=y)))
        previous=current
    return previous[-1]
def score_page(reference,hypothesis):
    ref,hyp=normalize(reference),normalize(hypothesis); rc,hc=graphemes(ref),graphemes(hyp); rw,hw=ref.split(),hyp.split()
    cd,wd=edit_distance(rc,hc),edit_distance(rw,hw)
    return {"reference_graphemes":len(rc),"character_edits":cd,"cer":cd/max(1,len(rc)),"reference_words":len(rw),"word_edits":wd,"wer":wd/max(1,len(rw)),"exact_match":int(rc==hc)}
def percentile(values,q):
    if not values:return None
    ordered=sorted(values); pos=(len(ordered)-1)*q; lo=math.floor(pos); hi=math.ceil(pos)
    return ordered[lo] if lo==hi else ordered[lo]+(ordered[hi]-ordered[lo])*(pos-lo)
def evaluate(manifest_path):
    records=[json.loads(x) for x in manifest_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not records: raise ValueError("manifest has no page records")
    split_by_title=defaultdict(set); groups=defaultdict(list); pages=[]; times=[]; rss=[]; human=True
    required=("title_id","split","language","stratum","reference_path","hypothesis_path","reference_source")
    for item in records:
        for field in required:
            if not item.get(field): raise ValueError(f"record {item.get('page_id','?')} is missing {field}")
        split_by_title[item["title_id"]].add(item["split"])
    leaked={k:sorted(v) for k,v in split_by_title.items() if len(v)>1}
    if leaked: raise ValueError(f"title-level split leakage: {leaked}")
    for item in records:
        ref=(manifest_path.parent/item["reference_path"]).resolve().read_text(encoding="utf-8")
        hyp=(manifest_path.parent/item["hypothesis_path"]).resolve().read_text(encoding="utf-8")
        row={**{k:item.get(k) for k in ("page_id","title_id","split","language","stratum","reference_source")},**score_page(ref,hyp)}
        pages.append(row); groups[(item["language"],item["stratum"])].append(row)
        if isinstance(item.get("elapsed_seconds"),(int,float)): times.append(float(item["elapsed_seconds"]))
        if isinstance(item.get("peak_rss_mib"),(int,float)): rss.append(float(item["peak_rss_mib"]))
        human &= item["reference_source"]=="human_adjudicated"
    def agg(rows):
        chars=sum(r["reference_graphemes"] for r in rows); words=sum(r["reference_words"] for r in rows)
        edits=sum(r["character_edits"] for r in rows); werrors=sum(r["word_edits"] for r in rows)
        return {"pages":len(rows),"micro_cer":edits/max(1,chars),"macro_cer":statistics.mean(r["cer"] for r in rows),
                "micro_wer":werrors/max(1,words),"macro_wer":statistics.mean(r["wer"] for r in rows),
                "exact_page_rate":statistics.mean(r["exact_match"] for r in rows)}
    return {"schema_version":"1.0.0","reference_quality":"human_adjudicated" if human else "diagnostic_only",
        "release_gate_eligible":human,"page_count":len(pages),"overall":agg(pages),
        "by_language_and_stratum":{f"{l}/{s}":agg(rs) for (l,s),rs in sorted(groups.items())},
        "throughput":{"p50_page_seconds":statistics.median(times) if times else None,"p95_page_seconds":percentile(times,.95),"peak_rss_mib":max(rss) if rss else None},
        "pages":pages}
def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("manifest",type=Path); p.add_argument("--output",type=Path); a=p.parse_args()
    text=json.dumps(evaluate(a.manifest),ensure_ascii=False,indent=2)+"\n"
    if a.output:a.output.write_text(text,encoding="utf-8")
    else:print(text,end="")
if __name__=="__main__":main()
