FROM python:3.10-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

ARG INSTALL_DEV=false
RUN if [ "$INSTALL_DEV" = "true" ] ; then \
        uv pip install --system --no-cache -e .[dev]; \
    else \
        uv pip install --system --no-cache -e .; \
    fi

COPY . .

CMD ["uvicorn", "src.devops_metrics.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
