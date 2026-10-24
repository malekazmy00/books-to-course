"""Local insertion-only punctuation; the accepted lesson remains immutable."""
import hashlib
import re
from pathlib import Path

POLICY = "naqta-insertion-only-v2-short-terminal"
_WORD_END = re.compile(r"[\u0621-\u064a\u066e-\u06d3\u064b-\u065f]")
_ALLOWED = {".": 0.78, "،": 0.82, "؟": 0.82, ":": 0.86}


def token_windows(count):
    """Cover every token using bounded overlapping windows, including the tail."""
    for start in range(0, count, 416):
        stop = min(start + 480, count)
        yield start, stop
        if stop == count:
            break


def insert_marks(text, predictions):
    """Only confident marks at Arabic word ends; never replace existing marks."""
    accepted = {}
    for end, label, confidence in predictions:
        if label not in _ALLOWED or confidence < _ALLOWED[label]:
            continue
        if end <= 0 or end > len(text) or not _WORD_END.fullmatch(text[end - 1]):
            continue
        if end < len(text) and not text[end].isspace():
            continue
        accepted[end] = label
    output = []
    previous = 0
    for end, mark in sorted(accepted.items()):
        output.extend((text[previous:end], mark))
        previous = end
    output.append(text[previous:])
    return "".join(output)


def ensure_short_terminal_mark(text, max_chars=240):
    """Insert a final pause for short Arabic speech text if the model abstains."""
    stripped = text.rstrip()
    if not stripped or len(stripped) > max_chars or not re.search(r"[\u0621-\u064a]", stripped):
        return text
    if re.search(r"[.!?\u061f\u2026\u061b]$", stripped):
        return text
    return stripped + "." + text[len(stripped):]


class NaqtaPunctuator:
    def __init__(self, model_dir):
        self.path = Path(model_dir)
        self.tokenizer = self.model = None

    def identity(self):
        required = ("config.json", "tokenizer.json", "model.safetensors", "download-manifest.json")
        if not all((self.path / name).is_file() for name in required):
            raise ValueError("Naqta model files are incomplete")
        digest = hashlib.sha256()
        for name in ("config.json", "download-manifest.json"):
            digest.update((self.path / name).read_bytes())
        return {"policy": POLICY, "model_manifest_sha256": digest.hexdigest(),
                "thresholds": _ALLOWED, "window_tokens": 480, "overlap_tokens": 64}

    def _load(self):
        if self.model is None:
            from transformers import AutoModelForTokenClassification, AutoTokenizer
            self.tokenizer = AutoTokenizer.from_pretrained(self.path, local_files_only=True, use_fast=True)
            self.model = AutoModelForTokenClassification.from_pretrained(
                self.path, local_files_only=True, trust_remote_code=False).eval()

    def restore(self, text):
        self._load()
        import torch
        output = []
        for paragraph in re.split(r"(\n\s*\n)", text):
            if not paragraph.strip() or not re.search(r"[\u0621-\u064a]", paragraph):
                output.append(paragraph)
                continue
            encoded = self.tokenizer(paragraph, add_special_tokens=False,
                                     return_offsets_mapping=True, truncation=False)
            ids, offsets = encoded["input_ids"], encoded["offset_mapping"]
            best = {}
            for start, stop in token_windows(len(ids)):
                inputs = self.tokenizer.prepare_for_model(ids[start:stop], return_tensors="pt")
                # Some tokenizer versions return flat token tensors here even
                # with return_tensors="pt". XLM-R expects a batched shape.
                inputs = {key: (value.unsqueeze(0) if value.ndim == 1 else value)
                          for key, value in inputs.items() if key in {"input_ids", "attention_mask"}}
                with torch.inference_mode():
                    probs = self.model(**inputs).logits[0, 1:stop-start+1].softmax(-1)
                for local in range(stop - start):
                    index = start + local
                    begin, end = offsets[index]
                    if end <= begin:
                        continue
                    centrality = min(local + 1, stop - start - local)
                    if index in best and best[index][0] >= centrality:
                        continue
                    score, label_id = probs[local].max(-1)
                    best[index] = (centrality, end, self.model.config.id2label[int(label_id)], float(score))
                if stop == len(ids):
                    break
            punctuated = insert_marks(paragraph, [(end, label, score)
                for _, end, label, score in best.values()])
            output.append(ensure_short_terminal_mark(punctuated))
        return "".join(output)
