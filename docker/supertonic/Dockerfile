FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MODEL_DIR=/app/model
WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.115.12 uvicorn[standard]==0.34.2 supertonic==1.3.1
COPY server.py /app/server.py
RUN useradd --uid 10002 --no-create-home tts
USER tts
EXPOSE 3007
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "3007"]
