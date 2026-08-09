# Build from the REPO ROOT so the shared csuite package is in context:
#   docker build -f cto/Dockerfile -t nano-cto:dev .
FROM python:3.12-slim
WORKDIR /app
COPY cto/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY csuite /app/csuite
COPY cto /app/cto
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "cto.api_main"]
