# Wren MCP server（開源版 wrenai 0.18.0），離線安裝：套件來源為 deploy/wheels
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WREN_HOME=/app/.wren \
    TZ=Asia/Taipei

WORKDIR /app
COPY deploy/wheels /tmp/wheels
RUN pip install --no-cache-dir --no-index --find-links /tmp/wheels 'wrenai[postgres,mcp]==0.18.0' \
 && rm -rf /tmp/wheels \
 && useradd -m -u 10001 wren

COPY wren_project /app/wren_project
COPY tests /app/tests
COPY deploy/profile.yml /app/profile.yml
COPY deploy/entrypoint.sh /app/entrypoint.sh
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh \
 && mkdir -p /app/.wren && chown -R wren:wren /app

USER wren
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
