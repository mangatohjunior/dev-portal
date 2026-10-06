"""Startup: load the GitLab token and open the release ledger."""

import os
import sys
from contextlib import asynccontextmanager

from config import Config, assert_runtime_config
from releases_store import init_db

gitlab_token = ""


@asynccontextmanager
async def lifespan(app):
    global gitlab_token
    try:
        assert_runtime_config()
        if os.path.exists(Config.TOKEN_PATH):
            with open(Config.TOKEN_PATH, "r") as file:
                gitlab_token = file.read().strip()
            print("GitLab token loaded from the mounted secret.")
        elif Config.LOCAL_GITLAB_TOKEN:
            gitlab_token = Config.LOCAL_GITLAB_TOKEN
            print("Local mode: GitLab token loaded from the environment.")
        else:
            raise FileNotFoundError(f"Missing {Config.TOKEN_PATH} and GITLAB_TOKEN env var.")
        init_db()
        print("Release ledger ready.")
    except Exception as exc:
        print(f"FATAL STARTUP ERROR: {exc}")
        sys.exit(1)

    yield
