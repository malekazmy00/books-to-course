"""Offline revalidation of retained original translation evidence; never invokes a model."""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
from collections import Counter
from b2c_translation.generation import validate_manual_translation, translation_completeness_issue

def original_numeric_restore(source, protected, translated):
    markers=list(re.finditer(r'\[\[B2C_NUM_(\d+)\]\]',protected))
    if not markers:
        if protected!=source:
            raise ValueError('request_source_not_exact')
        return translated
    chunks=[];cursor=0
    for marker in markers:
        chunks.append(re.escape(protected[cursor:marker.start()]))
        chunks.append(r'(?P<n'+marker.group(1)+r'>[-+]?\d+(?:[,.]\d+)*(?:%)?)')
        cursor=marker.end()
    chunks.append(re.escape(protected[cursor:]))
    match=re.fullmatch(''.join(chunks),source)
    if match is None:
        raise ValueError('request_source_not_exact')
    values={int(marker.group(1)):match.group('n'+marker.group(1)) for marker in markers}
    def restore(marker):
        index=int(marker.group(1))
        if index not in values:raise ValueError('historical_marker_unknown')
        return values[index]
    return re.sub(r'\[\[B2C_NUM_(\d+)\]\]',restore,translated)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('ledger_directory',type=Path);ap.add_argument('--output',required=True,type=Path)
    args=ap.parse_args()
    units=[]
    for path in sorted((args.ledger_directory/'root-personal-review-batches').glob('batch-*.json')):
        units.extend(json.loads(path.read_text())['units'])
    assert len(units)==154 and len({u['segment_id'] for u in units})==154
    rows=[]
    for unit in units:
        full=unit.get('latest_known_full_source_response')
        row={'review_index':unit['review_index'],'segment_id':unit['segment_id'],'cache_key':full.get('cache_key') if full else None}
        if not full:
            row.update(verdict='unknown_historical_request',reason='original_request_not_retained')
        else:
            try:
                entries=json.loads(full['request_messages'][1]['content'])['segments']
                protected=next(e['text'] for e in entries if e['segment_id']==unit['segment_id'])
                target=original_numeric_restore(unit['source'],protected,full['translated'])
                target=validate_manual_translation(unit['source'],target)
                issue=translation_completeness_issue(unit['source'],target)
                if issue:raise ValueError(issue['reason'])
                row.update(verdict='domain_valid',reason=None)
            except (ValueError,KeyError,StopIteration) as exc:
                row.update(verdict='rejected',reason=str(exc).split(':',1)[0])
        rows.append(row)
    payload={'units':rows,'counts':dict(Counter(r['verdict'] for r in rows)),'external_inference':False,'artifact_store_writes':False}
    args.output.write_text(json.dumps(payload,indent=2));args.output.chmod(0o600)
    print(json.dumps({'counts':payload['counts'],'reasons':dict(Counter(r['reason'] for r in rows if r['reason']))}))

if __name__=='__main__':main()
