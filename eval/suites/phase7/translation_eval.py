#!/usr/bin/env python3
"""Bounded local Hy-MT2 evaluation against a licensed FLORES-200 devtest sample."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import sacrebleu
import torch
import transformers
from transformers import AutoModelForCausalLM, AutoTokenizer

PROMPT = (
    "Translate the following text into Modern Standard Arabic. "
    "Return only the translation. Preserve names and numbers.\n\n"
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=30)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--threads", type=int, default=16)
    parser.add_argument("--max-seconds-per-sentence", type=float, default=90.0)
    args = parser.parse_args()

    source = args.source.read_text(encoding="utf-8").splitlines()
    reference = args.reference.read_text(encoding="utf-8").splitlines()
    if len(source) != len(reference) or not source:
        raise SystemExit("source/reference row count mismatch or empty dataset")
    if args.sample_size < 1 or args.sample_size > len(source):
        raise SystemExit("sample-size out of range")
    torch.set_num_threads(args.threads)
    torch.manual_seed(0)
    tokenizer = AutoTokenizer.from_pretrained(
        args.model, local_files_only=True, trust_remote_code=True
    )
    model = AutoModelForCausalLM.from_pretrained(
        args.model, local_files_only=True, trust_remote_code=True,
        dtype=torch.bfloat16, device_map="cpu",
    )
    model.eval()

    if args.sample_size == 1:
        indices = [len(source) // 2]
    else:
        indices = sorted(set(round(i * (len(source) - 1) / (args.sample_size - 1))
                             for i in range(args.sample_size)))
    hypotheses: list[str] = []
    rows: list[dict[str, object]] = []
    partial_path = args.output.with_suffix(".partial.jsonl")
    partial_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path.write_text("", encoding="utf-8")
    started = time.monotonic()
    for index in indices:
        item_started = time.monotonic()
        messages = [{"role": "user", "content": PROMPT + source[index]}]
        inputs = tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, return_tensors="pt"
        )
        with torch.inference_mode():
            outputs = model.generate(
                **inputs, max_new_tokens=args.max_new_tokens,
                max_time=args.max_seconds_per_sentence, do_sample=False, use_cache=True,
            )
        answer = tokenizer.decode(
            outputs[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True
        ).strip()
        row = {"index": index, "source": source[index],
               "reference": reference[index], "hypothesis": answer,
               "elapsed_s": time.monotonic() - item_started}
        rows.append(row)
        hypotheses.append(answer)
        with partial_path.open("a", encoding="utf-8") as partial:
            partial.write(json.dumps(row, ensure_ascii=False) + "\n")
            partial.flush()
        print(json.dumps({"completed": len(rows), "total": len(indices),
                          "index": index, "elapsed_s": round(row["elapsed_s"], 2)},
                         ensure_ascii=False), flush=True)
    elapsed = time.monotonic() - started
    chrf = sacrebleu.metrics.CHRF(word_order=2).corpus_score(
        hypotheses, [[reference[i] for i in indices]]
    )
    bleu = sacrebleu.metrics.BLEU().corpus_score(
        hypotheses, [[reference[i] for i in indices]]
    )
    numeric_match = sum(
        [re.findall(r"\d+(?:[.,]\d+)?", row["source"])
         == re.findall(r"\d+(?:[.,]\d+)?", row["hypothesis"]) for row in rows]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({
        "model": str(args.model), "sample_size": len(rows),
        "dataset_rows": len(source), "indices": indices,
        "elapsed_s": elapsed, "seconds_per_sentence": elapsed / len(rows),
        "chrfpp": chrf.score, "bleu": bleu.score,
        "numeric_token_exact_rows": numeric_match,
        "python_torch_transformers": {
            "torch": torch.__version__, "transformers": transformers.__version__,
        },
        "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "complete", "sample_size": len(rows), "elapsed_s": round(elapsed, 3),
        "seconds_per_sentence": round(elapsed / len(rows), 3),
        "chrfpp": round(chrf.score, 3), "bleu": round(bleu.score, 3),
        "numeric_token_exact_rows": f"{numeric_match}/{len(rows)}",
        "output": str(args.output),
    }, ensure_ascii=False))


if __name__ == "__main__":
    import re
    main()
