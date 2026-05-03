FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    "mcp[cli]>=1.6.0" \
    psycopg2-binary \
    pydantic \
    uvicorn

COPY server.py .

EXPOSE 8000

CMD ["python", "server.py"]
