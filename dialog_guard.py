"""Allow at most one Streamlit dialog to be opened during each app render."""
import streamlit as st


_OPENED_KEY = 'iipp-dialog-opened-this-run'


def begin_render():
    st.session_state[_OPENED_KEY] = False


def claim_dialog():
    if st.session_state.get(_OPENED_KEY, False):
        return False
    st.session_state[_OPENED_KEY] = True
    return True
