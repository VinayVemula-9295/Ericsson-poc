"""Shared branded header and authenticated workspace entry."""
from pathlib import Path
import base64
import os
import time
import streamlit as st

PRODUCT = 'Intelligent Provisioning Platform'


def footer():
    st.markdown('<footer class="iipp-footer"><span>Copyright @2026, </span><span class="iipp-footer-brand">Innominds Software Inc.</span><span> All rights reserved.</span></footer>', unsafe_allow_html=True)


def brand():
    logo = Path(__file__).parent / 'assets' / 'innominds-logo.png'
    encoded = base64.b64encode(logo.read_bytes()).decode()
    st.markdown(f'<div class="header-brand"><img alt="Innominds" src="data:image/png;base64,{encoded}"><span>{PRODUCT}</span></div>', unsafe_allow_html=True)


def require_demo_login(*, browser_restore=True):
    """Restore browser-cookie sessions before showing the login form."""
    import app_auth
    from control_plane import enabled, principal
    browser_sessions = os.getenv('IIPP_BROWSER_SESSIONS_ENABLED', 'false').lower() == 'true'
    if browser_sessions and browser_restore:
        from browser_session_component import browser_identity
        logout = bool(st.session_state.pop('iipp-browser-logout', False))
        grant = st.session_state.get('iipp-pending-grant')
        response = browser_identity(grant=grant, logout=logout)
        completed = isinstance(response, dict) and response.get('complete')
        matching = completed and response.get('grant') == grant and response.get('logout') == logout
        if matching:
            st.session_state['iipp-session-check-done'] = True
            if grant:
                st.session_state.pop('iipp-pending-grant', None)
            if logout:
                st.session_state.pop('iipp-auth', None)
            elif response.get('identity'):
                proof = response['identity'].get('proof')
                if proof != st.session_state.get('iipp-consumed-proof'):
                    from browser_sessions import consume_identity_proof
                    identity = consume_identity_proof(proof)
                    st.session_state['iipp-consumed-proof'] = proof
                    if identity:
                        st.session_state['iipp-auth'] = identity
                    else:
                        st.session_state.pop('iipp-auth', None)
            else:
                st.session_state.pop('iipp-auth', None)
        elif response is None and not st.session_state.get('iipp-session-check-done') and not grant and not logout:
            st.info('Restoring your secure session…')
            footer()
            st.stop()
        elif (grant or logout) and response is not None and completed and not matching:
            st.info('Restoring your secure session…')
            footer()
            st.stop()
    if browser_sessions:
        from browser_sessions import restore_hash
        auth = st.session_state.get('iipp-auth') or {}
        identity = restore_hash(auth.get('session_key'))
        if identity:
            st.session_state['iipp-auth'] = identity
            username = identity['username']
            st.session_state['control-plane-user'] = username
            st.session_state['demo-profile'] = dict(name=username, initials=username[:2].upper(), role=identity['role'])
        else:
            st.session_state.pop('iipp-auth', None)
    if app_auth.session_valid(st.session_state):
        if enabled() or app_auth.uses_registry():
            try:
                principal()
            except (ValueError, OSError, KeyError):
                sign_out()
            else:
                return
        else:
            return
    if st.session_state.get('iipp-auth') or st.session_state.get('demo-profile'):
        sign_out()
    st.markdown('<style>[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"]{display:none}</style>', unsafe_allow_html=True)
    with st.container(border=True, key='admin-login'):
        brand()
        st.caption('CLOUD OPERATIONS WORKSPACE')
        st.subheader('Welcome back')
        st.write('Sign in to manage your cloud estate and review infrastructure changes.')
        if not app_auth.configured():
            st.info('Administrator access has not been configured. Run tools/setup_admin.py and load the generated environment file before starting the platform.')
        with st.form('admin-login-form', clear_on_submit=True):
            username = st.text_input('Username', autocomplete='username')
            password = st.text_input('Password', type='password', autocomplete='current-password')
            submitted = st.form_submit_button('Sign in', type='primary', use_container_width=True)
        if submitted:
            try:
                user = app_auth.authenticate(username.strip(), password)
            except (KeyError, OSError, ValueError, TypeError):
                user = None
            if user:
                for key in list(st.session_state):
                    st.session_state.pop(key, None)
                if browser_sessions:
                    from browser_sessions import issue_grant
                    st.session_state['iipp-pending-grant'] = issue_grant(user)
                else:
                    stamp = time.time()
                    st.session_state['iipp-auth'] = dict(user, created=stamp, last_seen=stamp)
                    st.session_state['control-plane-user'] = user['username']
                    st.session_state['demo-profile'] = dict(name=user['username'], initials=user['username'][:2].upper(), role=user['role'])
                st.rerun()
            st.error('Unable to sign in. Check your credentials or try again in five minutes.')
        st.caption('Authorized access only. Sessions expire automatically after inactivity.')
    footer()
    st.stop()

def sign_out():
    session_key = (st.session_state.get('iipp-auth') or {}).get('session_key')
    if session_key:
        from browser_sessions import revoke_hash
        revoke_hash(session_key)
    for key in list(st.session_state):
        del st.session_state[key]
    st.session_state['iipp-browser-logout'] = True


def sidebar_profile():
    profile = st.session_state['demo-profile']
    profile = dict(profile, initials='A') if profile.get('role', '').lower() in {'admin', 'administrator'} else profile
    from html import escape
    st.markdown(f'<div class="sidebar-profile"><span class="profile-avatar">{escape(profile["initials"])}</span><div><strong>{escape(profile["name"])}</strong><small>{escape(profile["role"])}</small></div></div>', unsafe_allow_html=True)


def header_profile():
    profile = st.session_state['demo-profile']
    profile = dict(profile, initials='A') if profile.get('role', '').lower() in {'admin', 'administrator'} else profile
    with st.popover(profile['initials'], help='Open profile'):
        st.write(profile['name'])
        st.caption(profile['role'])
        st.button('Sign out', on_click=sign_out, use_container_width=True, type='primary')
