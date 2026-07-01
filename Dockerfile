FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/llleeeqi/rpg-world-engine" \
      org.opencontainers.image.description="A lightweight single-player, multi-agent text RPG world engine."

ENV PYTHONUNBUFFERED=1 \
    RPG_WORLD_HOST=0.0.0.0 \
    RPG_WORLD_PORT=54925 \
    RPG_WORLD_DATA=/data

WORKDIR /app

COPY rpg_world_engine ./rpg_world_engine
COPY web ./web
COPY server.py config.example.json README.md DESIGN.md MVP_TASKS.md ./

VOLUME ["/data"]
EXPOSE 54925

CMD ["python", "server.py"]
