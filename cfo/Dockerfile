# Build from the REPO ROOT so the shared csuite package is in context:
#   docker build -f cfo/Dockerfile -t nano-cfo:dev .
FROM python:3.12-slim
WORKDIR /app
COPY cfo/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY csuite /app/csuite
COPY cfo /app/cfo
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "cfo.api_main"]
