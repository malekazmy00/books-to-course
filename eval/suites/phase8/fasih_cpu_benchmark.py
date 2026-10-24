#!/usr/bin/env python3
"""Paired Fasih CPU inference benchmark; experimental only, never changes service defaults."""
from __future__ import annotations
import argparse, hashlib, json, os, random, sys, time
from pathlib import Path
import numpy as np
import soundfile as sf
import torch

DEFAULT_TEXT = "يشرح هذا الدرس كيف تساعد المراجعة المتباعدة على تثبيت المفاهيم، وكيف نستخدم الأمثلة للتحقق من الفهم قبل الانتقال إلى موضوع جديد."

def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""): h.update(block)
    return h.hexdigest()

def main() -> None:
    ap=argparse.ArgumentParser()
    ap.add_argument("--model-dir",type=Path,required=True)
    ap.add_argument("--source-dir",type=Path,required=True)
    ap.add_argument("--output-dir",type=Path,required=True)
    ap.add_argument("--catt-checkpoint",type=Path)
    ap.add_argument("--text",default=DEFAULT_TEXT)
    ap.add_argument("--threads",type=int,nargs="+",default=[28,4])
    ap.add_argument("--seeds",type=int,nargs="+",default=[1729,1730,1731])
    ap.add_argument("--load-threads",type=int,default=4)
    ap.add_argument("--interop-threads",type=int,default=4)
    ap.add_argument("--max-chars",type=int,default=160)
    args=ap.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True)
    torch.set_num_threads(args.load_threads)
    torch.set_num_interop_threads(args.interop_threads)
    os.chdir(args.source_dir)
    sys.path.insert(0,str(args.source_dir/"src"))
    from TTS.tts.configs.xtts_config import XttsConfig
    from TTS.tts.models.xtts import Xtts
    cfg=XttsConfig(); cfg.load_json(str(args.model_dir/"config.json"))
    t=time.perf_counter(); model=Xtts.init_from_config(cfg)
    model.load_checkpoint(cfg,checkpoint_path=str(args.model_dir/"model.pth"),vocab_path=str(args.model_dir/"vocab.json"),use_deepspeed=False)
    model.to("cpu").eval()
    lat=torch.load(args.model_dir/"speaker_latents.pt",map_location="cpu",weights_only=False)
    gpt,spk=lat["gpt_cond_latent"],lat["speaker_embedding"]
    xtts_load_s=time.perf_counter()-t
    catt_load_s=0.0; frontend_s=0.0; chunks=[args.text]; catt_sha=None
    if args.catt_checkpoint:
        from tts.text.diacritize import Diacritizer
        from tts.text.pipeline import TextPipeline
        t=time.perf_counter(); diac=Diacritizer(ckpt=args.catt_checkpoint,device="cpu"); catt_load_s=time.perf_counter()-t
        pipe=TextPipeline(diacritizer=diac)
        t=time.perf_counter(); chunks=pipe.prepare_chunks(args.text,max_chars=args.max_chars); frontend_s=time.perf_counter()-t
        catt_sha=sha256(args.catt_checkpoint)
    token_counts=[len(model.tokenizer.encode(chunk,lang="ar")) for chunk in chunks]
    if any(n>=model.args.gpt_max_text_tokens for n in token_counts):
        raise SystemExit(f"A chunk exceeds XTTS max text tokens ({model.args.gpt_max_text_tokens}): {token_counts}")
    rows=[]; sr=24000; gap=np.zeros(int(sr*0.12),dtype=np.float32)
    for seed in args.seeds:
        for threads in args.threads:
            torch.set_num_threads(threads)
            torch.manual_seed(seed); torch.random.default_generator.manual_seed(seed)
            np.random.seed(seed); random.seed(seed)
            pieces=[]; start=time.perf_counter()
            with torch.inference_mode():
                for i,chunk in enumerate(chunks):
                    result=model.inference(chunk,"ar",gpt,spk,temperature=0.65,repetition_penalty=2.0,enable_text_splitting=False)
                    pieces.append(np.asarray(result["wav"],dtype=np.float32).reshape(-1))
                    if i < len(chunks)-1: pieces.append(gap)
            inference_s=time.perf_counter()-start
            wav=np.concatenate(pieces) if pieces else np.zeros(0,dtype=np.float32)
            duration_s=len(wav)/sr
            name=f"seed{seed}_threads{threads}.wav"; out=args.output_dir/name
            sf.write(out,wav,sr,subtype="PCM_16")
            row={"seed":seed,"threads":threads,"interop_threads":args.interop_threads,"chunks":chunks,"token_counts":token_counts,"frontend_s":frontend_s,"inference_s":round(inference_s,3),"end_to_end_s":round(inference_s+frontend_s,3),"audio_duration_s":round(duration_s,3),"inference_rtf":round(inference_s/max(duration_s,1e-9),3),"end_to_end_rtf":round((inference_s+frontend_s)/max(duration_s,1e-9),3),"audio_sha256":sha256(out),"audio_path":str(out)}
            rows.append(row); print(json.dumps(row,ensure_ascii=False),flush=True)
    summary={"model":"NightPrince/Fasih-TTS-V1","model_sha256":sha256(args.model_dir/"model.pth"),"torch":torch.__version__,"python":sys.version.split()[0],"device":"cpu","cpu_count":os.cpu_count(),"load_threads":args.load_threads,"interop_threads":args.interop_threads,"xtts_load_s":round(xtts_load_s,3),"catt_load_s":round(catt_load_s,3),"catt_checkpoint_sha256":catt_sha,"text":args.text,"sampling":{"temperature":0.65,"repetition_penalty":2.0,"enable_text_splitting":False},"threads":args.threads,"seeds":args.seeds,"runs":rows}
    path=args.output_dir/"metrics.json"; path.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"metrics":str(path),"xtts_load_s":round(xtts_load_s,3)},ensure_ascii=False),flush=True)

if __name__=="__main__": main()
