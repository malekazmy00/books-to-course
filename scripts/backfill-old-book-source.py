#!/usr/bin/env python3
"""Prepare the single explicitly verified old-book alias; --apply opts into writes."""
import argparse
import json
from b2c_core import ArtifactStore, Settings
from b2c_core.legacy_source_backfill import OLD_BOOK_MAPPING, prepare_backfill, apply_backfill

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--apply",action="store_true")
args=parser.parse_args()
store=ArtifactStore.from_settings(Settings())
prepared=prepare_backfill(store,OLD_BOOK_MAPPING)
print(json.dumps({"mode":"apply" if args.apply else "dry-run",
    "external_book_id":prepared["progress"]["book_id"],
    "source_file_sha256":prepared["identity"]["source_file_sha256"],
    "source_parsed_sha256":prepared["identity"]["source_parsed_sha256"],
    "stages":list(prepared["progress"]["stages"]),
    "writes":[prepared["identity_key"],prepared["parsed_identity_key"],prepared["progress_key"],prepared["library_key"]]},indent=2))
if args.apply: print(json.dumps(apply_backfill(store,prepared),indent=2))
