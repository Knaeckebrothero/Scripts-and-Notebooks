"""
Admin page for user management.
Provides CRUD operations for user accounts.
"""
import streamlit as st

from db import HACCPDatabase
from db.repositories import UserRepository, SessionRepository
from auth import hash_password, generate_secure_password, get_available_roles, require_role


@require_role(["admin"])
def render_admin_page(db: HACCPDatabase):
    """Render the admin user management page."""
    st.title("👤 Benutzerverwaltung")

    user_repo = UserRepository(db)
    session_repo = SessionRepository(db)

    # Tabs for different operations
    tab_list, tab_create = st.tabs(["📋 Benutzerliste", "➕ Neuer Benutzer"])

    with tab_list:
        _render_user_list(user_repo, session_repo, db)

    with tab_create:
        _render_create_user_form(user_repo)


def _render_user_list(
    user_repo: UserRepository,
    session_repo: SessionRepository,
    db: HACCPDatabase,
):
    """Render the list of all users with edit/deactivate options."""
    st.subheader("Alle Benutzer")

    users = user_repo.get_all(order_by="username ASC")

    if not users:
        st.info("Keine Benutzer vorhanden.")
        return

    # Display users in a table
    for user in users:
        with st.expander(
            f"{'✅' if user.active else '❌'} {user.username} ({user.role})",
            expanded=False,
        ):
            col1, col2 = st.columns(2)

            with col1:
                st.markdown(f"**Benutzername:** {user.username}")
                st.markdown(f"**Anzeigename:** {user.display_name or '-'}")
                st.markdown(f"**Rolle:** {user.role}")

            with col2:
                st.markdown(f"**Status:** {'Aktiv' if user.active else 'Inaktiv'}")
                st.markdown(f"**Erstellt:** {user.created_at or '-'}")
                st.markdown(f"**Letzter Login:** {user.last_login or '-'}")

            st.markdown("---")

            # Edit section
            _render_edit_user_form(user, user_repo, session_repo, db)


def _render_edit_user_form(
    user,
    user_repo: UserRepository,
    session_repo: SessionRepository,
    db: HACCPDatabase,
):
    """Render edit form for a single user."""
    col_edit, col_password, col_status = st.columns(3)

    with col_edit:
        st.markdown("**Bearbeiten**")
        with st.form(key=f"edit_user_{user.id}"):
            new_display_name = st.text_input(
                "Anzeigename",
                value=user.display_name or "",
                key=f"display_name_{user.id}",
            )
            new_role = st.selectbox(
                "Rolle",
                options=get_available_roles(),
                index=get_available_roles().index(user.role)
                if user.role in get_available_roles()
                else 0,
                key=f"role_{user.id}",
            )

            if st.form_submit_button("💾 Speichern"):
                _update_user(db, user.id, new_display_name, new_role)

    with col_password:
        st.markdown("**Passwort zurücksetzen**")
        if st.button("🔑 Neues Passwort", key=f"reset_pw_{user.id}"):
            _reset_password(db, user, session_repo)

    with col_status:
        st.markdown("**Status ändern**")
        if user.active:
            if st.button("🚫 Deaktivieren", key=f"deactivate_{user.id}"):
                _deactivate_user(db, user.id, session_repo)
        else:
            if st.button("✅ Aktivieren", key=f"activate_{user.id}"):
                _activate_user(db, user.id)


def _render_create_user_form(user_repo: UserRepository):
    """Render form to create a new user."""
    st.subheader("Neuen Benutzer anlegen")

    with st.form("create_user_form", clear_on_submit=True):
        col1, col2 = st.columns(2)

        with col1:
            username = st.text_input(
                "Benutzername *",
                help="Eindeutiger Benutzername für die Anmeldung",
            )
            display_name = st.text_input(
                "Anzeigename",
                help="Name der in der App angezeigt wird",
            )

        with col2:
            role = st.selectbox(
                "Rolle *",
                options=get_available_roles(),
                index=get_available_roles().index("staff"),
                help="Bestimmt die Zugriffsrechte des Benutzers",
            )
            password_option = st.radio(
                "Passwort",
                options=["Automatisch generieren", "Manuell eingeben"],
                horizontal=True,
            )

        manual_password = None
        if password_option == "Manuell eingeben":
            manual_password = st.text_input(
                "Passwort *",
                type="password",
                help="Mindestens 8 Zeichen",
            )

        submitted = st.form_submit_button("👤 Benutzer anlegen", width="stretch")

        if submitted:
            _create_user(user_repo, username, display_name, role, manual_password)


def _create_user(
    user_repo: UserRepository,
    username: str,
    display_name: str,
    role: str,
    manual_password: str = None,
):
    """Create a new user account."""
    # Validation
    if not username:
        st.error("Benutzername ist erforderlich.")
        return

    if len(username) < 3:
        st.error("Benutzername muss mindestens 3 Zeichen lang sein.")
        return

    # Check for existing user
    existing = user_repo.get_by_username(username)
    if existing:
        st.error(f"Benutzername '{username}' ist bereits vergeben.")
        return

    # Generate or validate password
    if manual_password:
        if len(manual_password) < 8:
            st.error("Passwort muss mindestens 8 Zeichen lang sein.")
            return
        password = manual_password
    else:
        password = generate_secure_password(12)

    # Hash password
    password_hash, salt = hash_password(password)

    # Create user
    from auth.models import User

    new_user = User(
        username=username,
        password_hash=password_hash,
        password_salt=salt,
        role=role,
        display_name=display_name or username,
    )

    try:
        user_id = user_repo.insert(new_user)
        st.success(f"Benutzer '{username}' wurde erfolgreich angelegt.")

        # Show generated password once
        if not manual_password:
            st.warning("⚠️ **Bitte notieren Sie das generierte Passwort:**")
            st.code(password, language=None)
            st.info(
                "Dieses Passwort wird nur einmal angezeigt. "
                "Teilen Sie es dem Benutzer sicher mit."
            )
    except Exception as e:
        st.error(f"Fehler beim Anlegen des Benutzers: {e}")


def _update_user(db: HACCPDatabase, user_id: int, display_name: str, role: str):
    """Update user display name and role."""
    try:
        db.execute(
            "UPDATE users SET display_name = %s, role = %s WHERE id = %s",
            (display_name or None, role, user_id),
        )
        st.success("Benutzer wurde aktualisiert.")
        st.rerun()
    except Exception as e:
        st.error(f"Fehler beim Aktualisieren: {e}")


def _reset_password(db: HACCPDatabase, user, session_repo: SessionRepository):
    """Reset user password to a new generated one."""
    new_password = generate_secure_password(12)
    password_hash, salt = hash_password(new_password)

    try:
        db.execute(
            "UPDATE users SET password_hash = %s, password_salt = %s WHERE id = %s",
            (password_hash, salt, user.id),
        )

        # Invalidate all sessions for this user
        session_repo.delete_for_user(user.id)

        st.success(f"Passwort für '{user.username}' wurde zurückgesetzt.")
        st.warning("⚠️ **Neues Passwort:**")
        st.code(new_password, language=None)
        st.info(
            "Dieses Passwort wird nur einmal angezeigt. "
            "Der Benutzer wurde automatisch abgemeldet."
        )
    except Exception as e:
        st.error(f"Fehler beim Zurücksetzen des Passworts: {e}")


def _deactivate_user(db: HACCPDatabase, user_id: int, session_repo: SessionRepository):
    """Deactivate a user account (soft delete)."""
    # Prevent self-deactivation
    current_user_id = st.session_state.get("user_id")
    if current_user_id == user_id:
        st.error("Sie können sich nicht selbst deaktivieren.")
        return

    try:
        db.execute("UPDATE users SET active = FALSE WHERE id = %s", (user_id,))

        # Invalidate all sessions for this user
        session_repo.delete_for_user(user_id)

        st.success("Benutzer wurde deaktiviert.")
        st.rerun()
    except Exception as e:
        st.error(f"Fehler beim Deaktivieren: {e}")


def _activate_user(db: HACCPDatabase, user_id: int):
    """Reactivate a user account."""
    try:
        db.execute("UPDATE users SET active = TRUE WHERE id = %s", (user_id,))
        st.success("Benutzer wurde aktiviert.")
        st.rerun()
    except Exception as e:
        st.error(f"Fehler beim Aktivieren: {e}")
