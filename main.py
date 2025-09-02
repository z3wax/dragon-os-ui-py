# Modern greeter for DragonOS (GTK3)
# - JSON-based user database (PBKDF2)
# - Non-blocking auth
# - Polished, modern UI (glass card, subtle animations, inline errors)
# - Robust CapsLock indicator
import gi
import os
import sys
import json
import logging
import getpass
import threading
import hashlib
import binascii
from pathlib import Path

# ---- GTK ----
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib

# ---- Optional PAM (kept for compatibility; JSON is primary) ----
USE_PAM = False
pam_auth = None
try:
    import pam  # python-pam
    pam_auth = pam.pam()
    USE_PAM = True
except Exception:
    try:
        import simplepam as pam  # alternative
        pam_auth = pam
        USE_PAM = True
    except Exception:
        USE_PAM = False

from oos_ui import Desktop, DESKTOP_FOLDER
from helpers import ensure_desktop_folder, load_icon

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("greeter")

# =============================================================
#                      JSON AUTH BACKEND
# =============================================================
# File location (~/.config/dragonos/users.json)
CONF_DIR = Path(os.path.expanduser("~/.config/dragonos"))
USERS_JSON = CONF_DIR / "users.json"   # <-- fixed to "users.json"
PBKDF2_ITERS = 240_000
ALG = "sha256"

def _pbkdf2_hash(password: str, salt_hex: str) -> str:
    try:
        salt = binascii.unhexlify(salt_hex)
    except Exception:
        # If salt isn't valid hex, treat as failure
        raise
    dk = hashlib.pbkdf2_hmac(ALG, password.encode("utf-8"), salt, PBKDF2_ITERS)
    return binascii.hexlify(dk).decode("ascii")


def _new_salt(nbytes: int = 16) -> str:
    return os.urandom(nbytes).hex()


def _ensure_default_users_json():
    """Create a minimal users.json if not present (admin/1234)."""
    if USERS_JSON.exists():
        return
    try:
        CONF_DIR.mkdir(parents=True, exist_ok=True)
        salt = _new_salt()
        demo_pw = "1234"  # Demo default; change immediately in real installs
        data = {
            "users": [
                {
                    "username": "admin",
                    "display": "Administrator",
                    "salt": salt,
                    "hash": _pbkdf2_hash(demo_pw, salt),
                    "avatar": ""
                }
            ]
        }
        USERS_JSON.write_text(json.dumps(data, indent=2), encoding="utf-8")
        LOG.warning("Created default users.json with demo credentials admin/1234; CHANGE IT!")
    except Exception:
        LOG.exception("Failed to create default users.json")


def load_users_json():
    _ensure_default_users_json()
    try:
        with open(USERS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        users = data.get("users", [])
        # sanitize minimal fields
        clean = []
        for u in users:
            if not isinstance(u, dict):
                continue
            uname = str(u.get("username", "")).strip()
            if not uname:
                continue
            # ensure salt/hash are strings (empty if missing)
            salt = u.get("salt") or ""
            phash = u.get("hash") or ""
            clean.append({
                "username": uname,
                "display": u.get("display") or uname,
                "salt": salt,
                "hash": phash,
                "avatar": u.get("avatar") or ""
            })
        return clean
    except Exception:
        LOG.exception("Failed to read users.json")
        return []


def authenticate_json(username: str, password: str) -> bool:
    # Guest shortcut
    if username == "guest":
        return True
    users = load_users_json()
    for u in users:
        if u["username"] == username:
            salt = u.get("salt") or ""
            phash = u.get("hash") or ""
            if not salt or not phash:
                return False
            try:
                return _pbkdf2_hash(password, salt) == phash
            except Exception:
                LOG.exception("PBKDF2 failure")
                return False
    return False


# Utility: for the UI (user list and avatar resolution)
def get_json_users_for_ui():
    users = load_users_json()
    # return list of tuples (id, display)
    return [(u["username"], u.get("display", u["username"])) for u in users]


# =============================================================
#                     FALLBACK POSIX ENUM (optional)
# =============================================================

def get_local_users():
    """Enumerate local interactive users (POSIX). Fallback to current user or 'guest'."""
    users = []
    try:
        import pwd
        for p in pwd.getpwall():
            # typical desktop UIDs >= 1000; tweak as needed
            if p.pw_uid >= 1000 and p.pw_shell and "nologin" not in p.pw_shell:
                gecos = p.pw_gecos or p.pw_name
                users.append((p.pw_name, gecos))
    except Exception:
        pass
    if not users:
        try:
            cu = getpass.getuser()
            users = [(cu, cu)]
        except Exception:
            users = [("guest", "Guest")]
    return users


# =============================================================
#                         LOGIN WINDOW
# =============================================================

class LoginWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="DragonOS")
        self.app = app
        self.set_default_size(720, 480)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_resizable(False)

        # enable key events
        self.add_events(Gdk.EventMask.KEY_PRESS_MASK)

        # Root overlay to allow background + card
        overlay = Gtk.Overlay()
        self.add(overlay)

        # Background
        bg = Gtk.EventBox()
        bg.set_name("bg")
        overlay.add(bg)

        # Centered card
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.set_halign(Gtk.Align.CENTER)
        root.set_valign(Gtk.Align.CENTER)
        overlay.add_overlay(root)

        # Card/frame
        card = Gtk.Frame()
        card.set_shadow_type(Gtk.ShadowType.NONE)
        card.set_size_request(560, 390)
        card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        card_box.set_border_width(24)
        card.add(card_box)
        root.pack_start(card, False, False, 0)

        # Avatar (static default; could be personalized per user on change)
        self.avatar_box = Gtk.EventBox()
        self.avatar_box.set_size_request(96, 96)
        self.avatar_box.set_halign(Gtk.Align.CENTER)
        try:
            avatar_img = load_icon("avatar-default", size=96)
        except Exception:
            avatar_img = Gtk.Image.new_from_icon_name("avatar-default", Gtk.IconSize.DIALOG)
        self.avatar_box.add(avatar_img)
        card_box.pack_start(self.avatar_box, False, False, 0)

        # Title & subtitle
        title = Gtk.Label(label="Welcome to DragonOS")
        title.get_style_context().add_class("greeter-title")
        title.set_halign(Gtk.Align.CENTER)
        subtitle = Gtk.Label(label="Sign in with your account")
        subtitle.get_style_context().add_class("greeter-subtitle")
        subtitle.set_halign(Gtk.Align.CENTER)
        card_box.pack_start(title, False, False, 0)
        card_box.pack_start(subtitle, False, False, 0)

        # Inputs
        inputs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card_box.pack_start(inputs, False, False, 0)

        # User selector (from JSON first; fallback to POSIX)
        # Use id/text variant of ComboBoxText so we can show 'display' but read 'id' (username)
        self.user_combo = Gtk.ComboBoxText()
        self.user_combo.set_hexpand(True)
        self._populate_users()
        self.user_combo.connect("changed", self._on_user_changed)
        inputs.pack_start(self._row(self.user_combo, "👤"), False, False, 0)

        # Password with reveal
        self.pw = Gtk.Entry()
        self.pw.set_placeholder_text("Password")
        self.pw.set_visibility(False)
        self.pw.set_hexpand(True)
        self.pw.set_activates_default(True)

        self.reveal_btn = Gtk.ToggleButton()
        self.reveal_btn.set_relief(Gtk.ReliefStyle.NONE)
        try:
            self.reveal_btn.set_image(load_icon("view-password-symbolic", size=18))
        except Exception:
            self.reveal_btn.set_label("👁")
        self.reveal_btn.connect("toggled", self.on_reveal_toggled)

        pw_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        pw_row.pack_start(self.pw, True, True, 0)
        pw_row.pack_start(self.reveal_btn, False, False, 0)
        inputs.pack_start(pw_row, False, False, 0)

        # Inline message row (caps + errors)
        msg_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        msg_row.set_halign(Gtk.Align.FILL)
        self.caps_lbl = Gtk.Label(label="")
        self.caps_lbl.get_style_context().add_class("caps")
        self.caps_lbl.set_halign(Gtk.Align.START)
        self.err_lbl = Gtk.Label(label="")
        self.err_lbl.get_style_context().add_class("error")
        self.err_lbl.set_halign(Gtk.Align.END)
        msg_row.pack_start(self.caps_lbl, True, True, 0)
        msg_row.pack_end(self.err_lbl, False, False, 0)
        inputs.pack_start(msg_row, False, False, 0)

        # Actions
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        actions.set_halign(Gtk.Align.CENTER)
        self.login_btn = Gtk.Button(label="Sign in")
        self.login_btn.get_style_context().add_class("primary")
        self.login_btn.set_can_default(True)
        self.login_btn.connect("clicked", self.on_login)

        self.spinner = Gtk.Spinner()
        self.spinner.set_no_show_all(True)

        guest_btn = Gtk.Button(label="Guest")
        guest_btn.connect("clicked", self.on_guest)

        actions.pack_start(self.login_btn, True, True, 0)
        actions.pack_start(guest_btn, True, True, 0)
        actions.pack_start(self.spinner, False, False, 0)
        card_box.pack_start(actions, False, False, 0)

        # Footer
        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.set_halign(Gtk.Align.END)
        self.lang = Gtk.ComboBoxText()
        for L in ("en_US", "uz_UZ", "ru_RU"):
            self.lang.append_text(L)
        self.lang.set_active(0)
        footer.pack_start(self.lang, False, False, 0)

        shutdown_btn = Gtk.Button()
        shutdown_btn.set_relief(Gtk.ReliefStyle.NONE)
        shutdown_btn.set_tooltip_text("Shutdown")
        try:
            shutdown_btn.set_image(load_icon("system-shutdown", size=16))
        except Exception:
            shutdown_btn.set_label("⏻")
        shutdown_btn.connect("clicked", self.on_shutdown)
        footer.pack_start(shutdown_btn, False, False, 0)
        card_box.pack_end(footer, False, False, 0)

        # key handling + caps tick
        self.connect("key-press-event", self.on_key)
        GLib.timeout_add_seconds(1, self._tick_caps)

        # apply CSS
        self.apply_css()

        # default button on Enter
        try:
            self.set_default(self.login_btn)
        except Exception:
            pass

        self.show_all()

    # --- UI helpers ---
    def _row(self, widget: Gtk.Widget, prefix_text: str = "") -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        if prefix_text:
            pre = Gtk.Label(label=prefix_text)
            pre.get_style_context().add_class("prefix")
            row.pack_start(pre, False, False, 0)
        row.pack_start(widget, True, True, 0)
        return row

    def _populate_users(self):
        self.user_combo.remove_all()
        jusers = get_json_users_for_ui()
        if jusers:
            # Use append(id, text) so we show display name but keep username as id
            for uname, dname in jusers:
                try:
                    # ComboBoxText.append(id, text)
                    self.user_combo.append(uname, dname)
                except Exception:
                    # fallback for weird GTK bindings: append_text (shows username)
                    self.user_combo.append_text(uname)
            # set first active by id if possible
            try:
                first_id = jusers[0][0]
                self.user_combo.set_active_id(first_id)
            except Exception:
                self.user_combo.set_active(0)
        else:
            # fallback to POSIX
            locals_ = get_local_users()
            for uname, dname in locals_:
                try:
                    self.user_combo.append(uname, dname)
                except Exception:
                    self.user_combo.append_text(uname)
            try:
                self.user_combo.set_active_id(locals_[0][0])
            except Exception:
                self.user_combo.set_active(0)

    def _on_user_changed(self, *_):
        # could switch avatar based on JSON avatar path later
        self.err_lbl.set_text("")

    # --- CSS ---
    def apply_css(self):
        css = b"""
        window { background: #0b0e13; }
        #bg { background-image: linear-gradient(135deg, rgba(56,189,248,0.06), rgba(139,92,246,0.06)); }
        frame { background: rgba(255,255,255,0.06); border-radius: 18px; border: 1px solid rgba(255,255,255,0.08); }
        .greeter-title { font-size: 22px; color: #ffffff; font-weight: 700; }
        .greeter-subtitle { font-size: 12px; color: #c9d1d9; padding-bottom: 4px; }
        .prefix { color: #aeb6c2; padding: 8px 6px 8px 8px; }
        eventbox { background: transparent; }
        eventbox image { border-radius: 48px; }
        combo { }
        combobox, entry { background: rgba(255,255,255,0.08); color: #fff; padding: 10px; border-radius: 10px; }
        entry:focus, combobox:focus { background: rgba(255,255,255,0.12); }
        button.primary { background-color: #6a79ff; color: #fff; border-radius: 10px; padding: 10px 14px; }
        button.primary:active { background-color: #5867ff; }
        button { background: rgba(255,255,255,0.04); color: #e2e8f0; border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; padding: 8px 12px; }
        button:hover { background: rgba(255,255,255,0.07); }
        .caps { color: #ffb86b; font-size: 11px; }
        .error { color: #ff6b6b; font-size: 11px; padding-right: 4px; }
        spinner { margin-left: 6px; }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        screen = Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    # --- Events ---
    def on_reveal_toggled(self, btn):
        self.pw.set_visibility(btn.get_active())

    def _tick_caps(self):
        caps = False
        try:
            # avvalo modern API (Display -> Seat -> Keyboard)
            display = Gdk.Display.get_default()
            if display is not None:
                seat = display.get_default_seat()
                if seat is not None:
                    kb = seat.get_keyboard()
                    if kb is not None:
                        mods = kb.get_modifiers()
                        caps = bool(mods & Gdk.ModifierType.LOCK_MASK)
            # agar yuqoridagi yo‘l ishlamasa, eski usulga tushib qolmaslik uchun ham tekshir:
            if not caps:
                # ba'zi bindinglarda get_default_keymap hali bo'lmasligi mumkin
                try:
                    keymap = Gdk.Keymap.get_default()
                    if keymap is not None:
                        caps = keymap.get_caps_lock_state()
                except Exception:
                    # ignore — agar deprecated bo'lsa, fallback allaqachon tekshirildi
                    caps = caps
        except Exception:
            caps = False

        self.caps_lbl.set_text("CAPS ON" if caps else "")
        return True


    def on_key(self, widget, ev):
        name = Gdk.keyval_name(ev.keyval)
        if name in ("Return", "KP_Enter"):
            self.on_login(None)
            return True
        if name == "Escape":
            self.err_lbl.set_text("")
            self.pw.set_text("")
            self.pw.grab_focus()
            return True
        return False

    # -------------- AUTH --------------
    def authenticate(self, username, password) -> bool:
        LOG.info("Auth attempt user=%s backend=JSON%s PAM=%s", username,
                 " (primary)", USE_PAM)
        if authenticate_json(username, password):
            return True
        # Optional: allow PAM as secondary if JSON fails
        if username == "guest":
            return True
        if USE_PAM and pam_auth:
            try:
                try:
                    ok = pam_auth.authenticate(username, password)
                except Exception:
                    ok = bool(pam_auth.authenticate(username, password))
                return bool(ok)
            except Exception as e:
                LOG.exception("PAM error: %s", e)
                return False
        return False

    def _set_ui_busy(self, busy: bool):
        def do():
            self.user_combo.set_sensitive(not busy)
            self.pw.set_sensitive(not busy)
            self.reveal_btn.set_sensitive(not busy)
            self.login_btn.set_sensitive(not busy)
            if busy:
                self.spinner.show(); self.spinner.start()
            else:
                self.spinner.stop(); self.spinner.hide()
            return False
        GLib.idle_add(do)

    def on_login(self, *_):
        # IMPORTANT: if we used append(id, text) with ComboBoxText, read username via get_active_id()
        try:
            username = self.user_combo.get_active_id()
        except Exception:
            # fallback to older bindings
            username = self.user_combo.get_active_text()

        password = self.pw.get_text()
        if not username:
            self._inline_error("No user selected")
            return

        self._inline_error("")
        self._set_ui_busy(True)

        def worker():
            ok = False
            try:
                ok = self.authenticate(username, password)
            finally:
                def after():
                    self._set_ui_busy(False)
                    if ok:
                        LOG.info("Login OK: %s", username)
                        ensure_desktop_folder(DESKTOP_FOLDER)
                        desktop = Desktop(self.get_application())
                        desktop.show_all(); desktop.present()
                        self.destroy()
                    else:
                        self._inline_error("Authentication failed — Iltimos login va parolni tekshiring.")
                        self.pw.set_text("")
                        self.pw.grab_focus()
                    return False
                GLib.idle_add(after)
        threading.Thread(target=worker, daemon=True).start()

    def _inline_error(self, msg: str):
        self.err_lbl.set_text(msg)

    def on_guest(self, *_):
        # Direct guest login (don't rely on combo indices)
        if self.authenticate("guest", ""):
            ensure_desktop_folder(DESKTOP_FOLDER)
            desktop = Desktop(self.get_application())
            desktop.show_all(); desktop.present()
            self.destroy()

    def on_shutdown(self, *_):
        q = Gtk.MessageDialog(transient_for=self, flags=0,
                              message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.OK_CANCEL,
                              text="Shutdown the system?")
        resp = q.run(); q.destroy()
        if resp == Gtk.ResponseType.OK:
            try:
                if sys.platform == "darwin":
                    os.system('osascript -e \'tell app "System Events" to shut down\'')
                else:
                    os.system("pkexec shutdown -h now || systemctl poweroff || shutdown -h now")
            except Exception:
                LOG.exception("Shutdown failed")


# =============================================================
#                               APP
# =============================================================

class DragonDesktopApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.dragon.desktop")
        self.login_window = None

    def do_activate(self):
        if not self.login_window:
            self.login_window = LoginWindow(self)
        self.login_window.show_all(); self.login_window.present()


if __name__ == "__main__":
    # Ensure JSON exists before launching (creates demo admin/1234 on first run)
    _ensure_default_users_json()
    app = DragonDesktopApp()
    app.run(None)
