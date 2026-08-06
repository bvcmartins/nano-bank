# Build from the REPO ROOT so the shared csuite package is in context:
#   docker build -f coo/Dockerfile -t nano-coo:dev .
FROM python:3.12-slim
WORKDIR /app
COPY coo/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY csuite /app/csuite
COPY coo /app/coo
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "coo.api_main"]
