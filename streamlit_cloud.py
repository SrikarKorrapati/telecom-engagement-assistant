"""Streamlit Community Cloud entry point; secrets remain on the server."""

import os

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

# Accessing st.secrets loads the platform's secret store. Copy only known values.
try:
    for key in ("DATABASE_URL", "DEMO_ACCESS_PASSWORD", "OPENAI_API_KEY",
                "OPENAI_MODEL", "OPENAI_REASONING_EFFORT"):
        if key in st.secrets:
            os.environ[key] = str(st.secrets[key])
except FileNotFoundError:
    pass
os.environ["REQUIRE_DEMO_LOGIN"] = "true"
os.environ["POLICY_STORAGE"] = "database"

from app.hosted_bootstrap import initialize
from ui.streamlit_app import main


@st.cache_resource(show_spinner=False)
def prepare():
    initialize()
    return True


try:
    main(prepare=prepare)
except (SQLAlchemyError, ValueError, OSError):
    st.error("The hosted demo could not connect or initialize. Check the app secrets and database availability.")
    if st.button("Retry startup"):
        st.rerun()
