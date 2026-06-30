import os

from rpg_world_engine.server import run


if __name__ == "__main__":
    run(
        host=os.environ.get("RPG_WORLD_HOST", "127.0.0.1"),
        port=int(os.environ.get("RPG_WORLD_PORT", "54925")),
    )
