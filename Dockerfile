FROM ghcr.io/foundry-rs/foundry:v1.4.4 AS contracts
USER 0:0
WORKDIR /contracts
COPY --chown=0:0 contracts/ /contracts/
RUN chmod -R u+rwX,go+rX /contracts && forge build --root /contracts

FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY backend/ backend/
RUN pip install --no-cache-dir --no-deps .
COPY config/ config/
COPY scripts/ scripts/
COPY frontend/ frontend/
COPY --from=contracts /contracts/out /artifacts
ENV PYTHONPATH=/app/backend:/app PYTHONUNBUFFERED=1
CMD ["python", "scripts/run.py", "uvicorn", "oracle.app:app", "--host", "0.0.0.0", "--port", "8000"]
