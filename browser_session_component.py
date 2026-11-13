"""Bridge the same-origin session cookie endpoint into Streamlit state."""
from pathlib import Path
import streamlit.components.v1 as components

_component = components.declare_component(
    'iipp_browser_session', path=str(Path(__file__).parent / 'assets' / 'browser-session'))


def browser_identity(grant=None, logout=False):
    return _component(grant=grant, logout=logout, default=None, key='iipp-browser-session')
