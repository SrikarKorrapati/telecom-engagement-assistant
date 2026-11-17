"""Read-only customer dashboard using the reviewer's hosted database."""

import os

import streamlit as st

# The dashboard needs database access and the existing demo password, not an AI key.
try:
    for key in ("DATABASE_URL", "DEMO_ACCESS_PASSWORD"):
        if key in st.secrets:
            os.environ[key] = str(st.secrets[key])
except FileNotFoundError:
    pass
os.environ["REQUIRE_DEMO_LOGIN"] = "true"
os.environ["POLICY_STORAGE"] = "database"

from ui.dataset_dashboard import main

# The reviewer initializes the shared database. This app only reads it.
main()
