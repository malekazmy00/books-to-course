FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
WORKDIR /app
COPY docker/embeddinggemma/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY packages/indexing/src/b2c_indexing/embeddinggemma_api.py /app/embeddinggemma_api.py
RUN useradd --uid 10001 --no-create-home b2c
USER b2c
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "embeddinggemma_api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
