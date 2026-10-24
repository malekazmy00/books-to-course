import os, re, tempfile, subprocess, urllib.request, json, hashlib, threading
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

app=FastAPI(title="B2C MP3 Renderer")
AR=os.getenv("FASIH_URL","http://fasih-tts:3006")
EN=os.getenv("SUPERTONIC_URL","http://supertonic-tts:3007")
RENDER_POLICY_VERSION = "sentence-aware-boundaries-v4-short-arabic-terminal"
_PROVIDER_TEXT_LIMIT = 1200

_punctuator = None


def speech_texts(texts, language, cache):
    """Persist a derivative without rewriting translation or lesson artifacts."""
    enabled = language == "ar" and os.getenv("B2C_SPEECH_PUNCTUATION", "off") == "naqta"
    if not enabled:
        return texts, {"punctuation": "off"}
    global _punctuator
    if _punctuator is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("b2c_arabic_punctuation", Path(__file__).with_name("arabic_punctuation.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _punctuator = module.NaqtaPunctuator(os.environ.get("B2C_NAQTA_MODEL_DIR", "/app/models/naqta"))
    try:
        identity = _punctuator.identity()
        prepared = []
        for text in texts:
            key = hashlib.sha256(json.dumps({"source": text, "identity": identity},
                ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            path = cache / ("speech-text-" + key + ".json")
            if path.exists():
                state = json.loads(path.read_bytes())
                body = state["text"]
                if state["text_sha256"] != hashlib.sha256(body.encode()).hexdigest():
                    raise ValueError("invalid prepared speech text cache")
            else:
                body = _punctuator.restore(text)
                durable_write(path, json.dumps({"text": body, "identity": identity,
                    "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "text_sha256": hashlib.sha256(body.encode()).hexdigest()}, ensure_ascii=False).encode())
            prepared.append(body)
        return prepared, identity
    except Exception as exc:
        raise HTTPException(503, "Arabic punctuation preparation unavailable") from exc


def _split_provider_text(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    segments = []
    sentence_end = re.compile(r"[.!?؟؛](?:[\"'»)]*)?(?:\s|$)")
    clause_end = re.compile(r"[,،;:](?:\s|$)")
    while len(text) > _PROVIDER_TEXT_LIMIT:
        window = text[:_PROVIDER_TEXT_LIMIT + 1]
        sentences = [m.end() for m in sentence_end.finditer(window)
                     if int(_PROVIDER_TEXT_LIMIT * 0.65) <= m.end() <= _PROVIDER_TEXT_LIMIT]
        clauses = [m.end() for m in clause_end.finditer(window)
                   if int(_PROVIDER_TEXT_LIMIT * 0.7) <= m.end() <= _PROVIDER_TEXT_LIMIT]
        if sentences:
            cut = sentences[-1]
        elif clauses:
            cut = clauses[-1]
        else:
            boundary = window.rfind(" ", int(_PROVIDER_TEXT_LIMIT * 0.75), _PROVIDER_TEXT_LIMIT)
            if boundary < 0:
                raise HTTPException(422, "unbroken token exceeds TTS provider limit")
            cut = boundary + 1
        segments.append(text[:cut].strip())
        text = text[cut:].lstrip()
    if text:
        segments.append(text)
    return segments


def tts_segments(text: str, language: str) -> list[str]:
    if language not in {"ar", "en"}:
        raise HTTPException(422, "unsupported TTS language")
    # Fasih applies its own post-normalization and CATT-aware chunker. Do not
    # pre-split Arabic into 160-character requests before it sees full context.
    return _split_provider_text(text)

def valid_wav(wav: bytes) -> bool:
    return len(wav) >= 44 and wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"


def durable_write(path: Path, body: bytes) -> None:
    temporary = path.with_name(path.name + "." + os.urandom(8).hex() + ".tmp")
    try:
        with temporary.open("wb") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


_render_lock = threading.Lock()


class RenderRequest(BaseModel):
    texts:list[str]=Field(min_length=1,max_length=2000)
    language:str=Field(pattern="^(en|ar)$")
    request_id: str | None = Field(default=None, pattern="^[a-f0-9]{64}$")
    pipeline_version: str = RENDER_POLICY_VERSION
@app.get("/health")
def health(): return {"status":"ok"}
@app.get("/v1/render/{request_id}/progress")
def render_progress(request_id: str):
    if not re.fullmatch(r"[a-f0-9]{64}", request_id):
        raise HTTPException(422, "invalid render identity")
    path = Path(os.getenv("B2C_RENDERER_CACHE_DIR", "/tmp/b2c-renderer-cache")) / (request_id + ".json")
    try:
        return json.loads(path.read_bytes())
    except FileNotFoundError:
        raise HTTPException(404, "render has not started")


@app.post("/v1/render")
def render(req: RenderRequest):
    # One active render per service; segment progress remains readable by GET.
    with _render_lock:
        return render_serial(req)


def render_serial(req: RenderRequest):
    if req.pipeline_version != RENDER_POLICY_VERSION:
        raise HTTPException(409, "unsupported audio rendering policy version")
    endpoint = (AR if req.language == "ar" else EN) + "/v1/tts"
    cache = Path(os.getenv("B2C_RENDERER_CACHE_DIR", "/tmp/b2c-renderer-cache"))
    cache.mkdir(parents=True, exist_ok=True)
    prepared, preparation = speech_texts(req.texts, req.language, cache)
    segments = [part for text in prepared for part in tts_segments(text, req.language) if part.strip()]
    if not segments:
        raise HTTPException(422, "no nonempty audio segments")
    content_id = hashlib.sha256(json.dumps({"texts": req.texts, "language": req.language,
        "pipeline_version": req.pipeline_version, "preparation": preparation}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    request_id = req.request_id or content_id
    checkpoint = cache / (request_id + ".json")
    if checkpoint.exists() and json.loads(checkpoint.read_bytes()).get("content_id") != content_id:
        raise HTTPException(409, "render identity belongs to different text")
    keys = [hashlib.sha256((RENDER_POLICY_VERSION + "\0" + json.dumps(preparation, sort_keys=True) + "\0" + req.language + "\0" + text).encode()).hexdigest() for text in segments]
    completed = {i for i, key in enumerate(keys) if (cache / (key + ".wav")).exists() and valid_wav((cache / (key + ".wav")).read_bytes())}
    failed = set()
    def save(status, current=None, error=None):
        state = {"request_id": request_id, "content_id": content_id, "status": status, "speech_preparation": preparation,
                 "segment_count": len(segments), "completed_segments": len(completed),
                 "remaining_segments": len(segments) - len(completed), "failed_segments": len(failed),
                 "current_segment": current, "completed_segment_indices": sorted(completed),
                 "failed_segment_indices": sorted(failed), "segment_cache_keys": keys,
                 "updated_at": datetime.now(timezone.utc).isoformat(), "error": error}
        durable_write(checkpoint, json.dumps(state).encode())
    save("rendering")
    with tempfile.TemporaryDirectory() as td:
        paths = []
        for i, text in enumerate(segments):
            saved = cache / (keys[i] + ".wav")
            save("rendering", i + 1)
            if i in completed:
                wav = saved.read_bytes()
            else:
                try:
                    request = urllib.request.Request(endpoint, data=json.dumps({"text": text}, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(request, timeout=1800) as response:
                        wav = response.read()
                    if not valid_wav(wav):
                        raise ValueError("invalid WAV")
                    durable_write(saved, wav)
                    completed.add(i)
                    failed.discard(i)
                    save("rendering", i + 1)
                except Exception as exc:
                    failed.add(i)
                    save("failed", i + 1, type(exc).__name__)
                    raise HTTPException(502, f"TTS provider failed on segment {i}") from exc
            path = Path(td) / f"{i:05d}.wav"
            path.write_bytes(wav)
            paths.append(path)
        save("assembling")
        listing = Path(td) / "files.txt"
        listing.write_text("".join("file '" + str(path) + "'\n" for path in paths))
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-vn", "-ac", "1", "-ar", "24000", "-codec:a", "libmp3lame", "-b:a", "96k", "-f", "mp3", "pipe:1"]
        try:
            result = subprocess.run(command, check=True, capture_output=True, timeout=21600)
            if not result.stdout.startswith(b"ID3") and not result.stdout.startswith(b"\xff\xfb"):
                raise ValueError("invalid MP3")
        except Exception as exc:
            save("assembly_failed", error=type(exc).__name__)
            raise HTTPException(500, "MP3 assembly failed") from exc
        save("completed")
        return Response(result.stdout, media_type="audio/mpeg", headers={"X-Audio-Segments": str(len(paths)), "X-Render-Request-ID": request_id})
