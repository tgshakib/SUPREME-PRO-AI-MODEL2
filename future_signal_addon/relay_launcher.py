"""Launch the supplied Future Signal add-on in same-token relay mode."""
import os
import shutil


def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(
                key.strip(),
                value.strip().strip('"').strip("'"),
            )


_load_dotenv()

token = os.environ.get("BOT_TOKEN", "").strip()
admin_id = os.environ.get("ADMIN_ID", "").strip()
if not token:
    raise RuntimeError("BOT_TOKEN is required for Future Signal relay mode")
if not os.environ.get("SESSION_SECRET"):
    raise RuntimeError("SESSION_SECRET is required for the private update relay")

node = shutil.which("node")
if not node:
    raise RuntimeError("Node.js is required for the Future Signal add-on")

env = os.environ.copy()
env.update({
    "TELEGRAM_BOT_TOKEN": token,
    "BOT_ADMIN_ID": admin_id,
    "PORT": "3001",
    "INTEGRATED_UPDATE_RELAY": "1",
    "NODE_ENV": "production",
})

os.execve(
    node,
    [node, "future_signal_addon/dist/index.mjs"],
    env,
)