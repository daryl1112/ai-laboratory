# ailab-base: prebuilt CPU-only ML stack for experiments.
# Built once by the orchestrator (or manually: docker build -t ailab-base:py312 .)
# Experiments using environment.type == "base" run on this image and pip-install
# their extra requirements at container start.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    numpy pandas scikit-learn scipy matplotlib seaborn \
    requests beautifulsoup4 lxml httpx \
    duckdb pyarrow polars \
    tqdm joblib statsmodels \
    psycopg2-binary redis sqlalchemy \
    && pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

WORKDIR /workspace
CMD ["python", "main.py"]
