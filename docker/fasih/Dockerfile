FROM python:3.11-slim@sha256:bab1b7ef4b450c81002278d035eff85ebe394ae94df904f7a3ba14f7e16e487b
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MODEL_DIR=/app/model CATT_CKPT=/app/models/catt/best_ed_mlm_ns_epoch_178.pt TTS_HOST=0.0.0.0 TTS_PORT=3006 TTS_DEVICE=cpu OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
ENV MPLCONFIGDIR=/tmp/matplotlib XDG_CACHE_HOME=/tmp/cache
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libsndfile1 ffmpeg curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir torch==2.3.1 torchaudio==2.3.1 --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY src/tts ./tts
COPY server.py ./server.py
COPY catt_bounds.py ./catt_bounds.py
RUN useradd --uid 10001 --no-create-home fasih
USER fasih
EXPOSE 3006
CMD ["python", "server.py"]
