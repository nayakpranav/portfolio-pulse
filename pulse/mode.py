"""Fail-closed deployment configuration; configuration is not a data licence."""
import os

def mode():
    selected = os.environ.get('FOLIOLENS_MODE', '').strip().lower()
    if not selected:
        selected = 'personal' if os.environ.get('PULSE_ENABLE_UPLOADS') == '1' else 'public_demo'
    if selected not in {'personal', 'owner_hosted', 'public_demo'}:
        return 'public_demo'
    return selected

def uploads_enabled():
    selected = mode()
    if selected == 'personal':
        # Never let an accidental legacy flag enable a Community Cloud upload.
        return not os.environ.get('STREAMLIT_SHARING_MODE') and not os.environ.get('IS_STREAMLIT_CLOUD')
    return selected == 'owner_hosted' and os.environ.get('FOLIOLENS_OWNER_GATE_VERIFIED') == '1' and os.environ.get('FOLIOLENS_DATA_RIGHTS_VERIFIED') == '1'
