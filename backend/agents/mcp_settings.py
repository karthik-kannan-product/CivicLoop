"""Dedicated MCP process: no browser, admin, session or application routes."""

from civicloop.settings import *  # noqa: F403

ROOT_URLCONF = "agents.mcp_urls"
MIDDLEWARE = []
ALLOWED_HOSTS = ["mcp", "localhost", "127.0.0.1"]
DEBUG = False
DATA_UPLOAD_MAX_MEMORY_SIZE = 65536
