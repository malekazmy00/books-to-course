import os
import tempfile
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from supertonic import TTS

app = FastAPI(title="B2C English TTS")
model = None
class Request(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    voice: str = Field(default="M1", pattern=r"^[MF][1-5]$")
@app.on_event("startup")
def load():
    global model
    model = TTS(auto_download=False, model_dir=os.environ.get("MODEL_DIR", "/app/model"))
@app.get("/health")
def health():
    return {"status": "ok" if model else "loading", "model": "Supertonic3"}
@app.post("/v1/tts")
def tts(req: Request):
    if model is None: raise HTTPException(503, "model loading")
    style = model.get_voice_style(voice_name=req.voice)
    wav, duration = model.synthesize(req.text, voice_style=style, lang="en")
    with tempfile.NamedTemporaryFile(suffix=".wav") as out:
        model.save_audio(wav, out.name)
        out.seek(0)
        data = out.read()
    return Response(data, media_type="audio/wav", headers={"X-Audio-Duration": str(duration)})
