FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY migrations ./migrations

RUN pip install --no-cache-dir ".[cloud,observability]"

ENV PORT=8000
EXPOSE 8000

CMD ["wingman-cloud"]
