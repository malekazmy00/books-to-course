FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
COPY --from=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH
WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
COPY packages ./packages
RUN uv sync --frozen --no-dev --package b2c-api

RUN useradd --uid 10001 --no-create-home b2c
USER b2c
EXPOSE 8000
CMD ["uvicorn", "b2c_api.app:app", "--host", "0.0.0.0", "--port", "8000"]
