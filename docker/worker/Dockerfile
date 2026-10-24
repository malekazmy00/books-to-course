# b2c worker image: the uv workspace (incl. dev group, so integration tests run inside the b2c network).
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
COPY --from=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 /uv /uvx /bin/

ENV DOCLING_ARTIFACTS_PATH=/opt/docling-models \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH
RUN apt-get update && apt-get install -y --no-install-recommends libxcb1 libglib2.0-0 libgl1 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
COPY packages ./packages
RUN UV_HTTP_TIMEOUT=600 UV_HTTP_RETRIES=10 uv sync --frozen --all-packages --extra docling
RUN docling-tools models download --output-dir /opt/docling-models layout tableformer rapidocr \
    --rapidocr-backend-lang onnxruntime:en \
    --rapidocr-backend-lang onnxruntime:arabic
COPY tests ./tests

RUN useradd --uid 10001 --no-create-home b2c
USER b2c
CMD ["b2c-worker"]
