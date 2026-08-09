FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app/platform_mcp
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "platform_mcp.mcp_server"]
