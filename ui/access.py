"""A shared demo password gate; this does not establish reviewer identity."""

import hashlib
import hmac

from app.access import demo_password


def require_access(st) -> None:
    try:
        expected = demo_password()
    except ValueError:
        st.error("Demo access has not been configured. Contact the app owner.")
        st.stop()
        return
    if not expected:
        return  # Existing local, loopback-only workflows stay available.

    fingerprint = hashlib.sha256(expected.encode()).hexdigest()
    if hmac.compare_digest(st.session_state.get("_demo_access", ""), fingerprint):
        if st.sidebar.button("Sign out"):
            st.session_state.clear()
            st.rerun()
        return

    def sign_in():
        submitted = st.session_state.pop("_demo_password", "")
        valid = hmac.compare_digest(submitted.encode(), expected.encode())
        st.session_state.clear()
        if valid:
            st.session_state["_demo_access"] = fingerprint
        else:
            st.session_state["_demo_login_failed"] = True

    st.title("Telecom engagement demo")
    st.caption("Enter the demo password shared by the app owner.")
    with st.form("demo_login"):
        st.text_input("Demo password", type="password", key="_demo_password")
        st.form_submit_button("Sign in", on_click=sign_in)
    if st.session_state.get("_demo_login_failed"):
        st.error("Incorrect password. Try again.")
    st.stop()
