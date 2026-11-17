"""Access configuration for the shared, synthetic hosted demonstration."""

import os

from dotenv import load_dotenv


def demo_password() -> str:
    load_dotenv(override=False)
    password = os.getenv("DEMO_ACCESS_PASSWORD", "")
    required = os.getenv("REQUIRE_DEMO_LOGIN", "").lower() in {"true", "1", "yes"}
    required = required or os.getenv("RENDER", "").lower() == "true"
    if (required or password) and len(password) < 16:
        raise ValueError("Set DEMO_ACCESS_PASSWORD to at least 16 characters before starting the hosted demo.")
    return password
