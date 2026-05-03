FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    "mcp[cli]>=1.6.0" \
    psycopg2-binary \
    pydantic \
    uvicorn \
    starlette

COPY . .

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:80/health || exit 1

CMD ["python", "server.py"]
