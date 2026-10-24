FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
RUN pip install --no-cache-dir torch==2.6.0 --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir transformers==5.6.2 fastapi==0.115.12 uvicorn==0.34.2 safetensors==0.5.3 sentencepiece==0.2.0
WORKDIR /app
COPY docker/hy-translation/server.py /app/server.py
RUN useradd --uid 10001 --no-create-home hy
USER hy
EXPOSE 3007
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "3007"]
