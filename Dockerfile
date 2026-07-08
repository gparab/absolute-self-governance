# Stage 1: Build virtual environment
FROM python:3.13-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

COPY pyproject.toml uv.lock ./
RUN uv sync --no-install-project

# Stage 2: Runtime image
FROM python:3.13-slim AS runner

WORKDIR /app

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

COPY --from=builder /app/.venv /app/.venv
COPY src/ ./src
COPY pyproject.toml ./

RUN pip install --no-deps -e .

ENTRYPOINT ["self-governance"]
CMD ["--help"]
