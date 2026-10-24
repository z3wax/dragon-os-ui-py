# Modern greeter for DragonOS (GTK3) — Alpine Edition
# - JSON-based user database (PBKDF2)
# - Non-blocking auth
# - Polished glass UI: gradient backdrop, monogram avatar, inline errors
# - Alpine-friendly power actions
import gi
import os
import sys
import json
import logging
import getpass
import threading
import hashlib
import binascii
import subprocess
from pathlib import Path

# ---- GTK ----
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib, Pango, Gio

try:
    import cairo
    HAVE_CAIRO = True
except Exception:
    HAVE_CAIRO = False

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

from oos_ui import Desktop, DESKTOP_FOLDER, theme_icon
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

class MonogramAvatar(Gtk.DrawingArea):
    """Circular gradient avatar with a monogram letter (no emoji fonts needed)."""

    def __init__(self, letter="D", size=96):
        super().__init__()
        self.letter = letter
        self.size = size
        self.set_size_request(size, size)
        try:
            self.connect("draw", self._draw)
        except Exception:
            pass

    def _draw(self, widget, cr):
        try:
            alloc = widget.get_allocation()
            w, h = alloc.width, alloc.height
            cx, cy, r = w / 2.0, h / 2.0, min(w, h) / 2.0

            grad = cairo.LinearGradient(0, 0, w, h)
            grad.add_color_stop_rgba(0.0, 0.486, 0.549, 1.0, 1.0)   # #7c8cff
            grad.add_color_stop_rgba(1.0, 0.659, 0.333, 0.969, 1.0) # #a855f7
            cr.set_source(grad)
            cr.arc(cx, cy, r, 0, 2.0 * 3.141592653589793)
            cr.fill()

            soft = cairo.RadialGradient(cx, cy - r * 0.35, 0, cx, cy, r)
            soft.add_color_stop_rgba(0.0, 1.0, 1.0, 1.0, 0.35)
            soft.add_color_stop_rgba(1.0, 1.0, 1.0, 1.0, 0.0)
            cr.set_source(soft)
            cr.arc(cx, cy, r, 0, 2.0 * 3.141592653589793)
            cr.fill()

            cr.set_source_rgba(1.0, 1.0, 1.0, 0.96)
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(r * 0.72)
            ext = cr.text_extents(self.letter)
            cr.move_to(cx - ext.width / 2.0 - ext.x_bearing,
                       cy - ext.height / 2.0 - ext.y_bearing)
            cr.show_text(self.letter)

            cr.set_source_rgba(1.0, 1.0, 1.0, 0.55)
            cr.set_line_width(2.5)
            cr.arc(cx, cy, r - 2.5, 0, 2.0 * 3.141592653589793)
            cr.stroke()
        except Exception:
            pass
        return False


class LoginWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="DragonOS")
        self.app = app
        self.set_default_size(720, 480)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_resizable(False)
        self.set_name("greeter")
        try:
            visual = self.get_screen().get_rgba_visual()
            if visual:
                self.set_visual(visual)
        except Exception:
            pass

        # enable key events
        self.add_events(Gdk.EventMask.KEY_PRESS_MASK)

        # Root overlay to allow background + card
        overlay = Gtk.Overlay()
        self.add(overlay)

        # Background gradient layer
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
        card.set_name("greeter-card")
        card.set_shadow_type(Gtk.ShadowType.NONE)
        card.set_size_request(404, 386)
        card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card_box.set_border_width(28)
        card.add(card_box)
        root.pack_start(card, False, False, 0)

        # Avatar: gradient monogram (no emoji/icon fonts needed)
        self.avatar_box = Gtk.Box()
        self.avatar_box.set_halign(Gtk.Align.CENTER)
        try:
            avatar = MonogramAvatar(letter="D", size=92)
        except Exception:
            avatar = Gtk.Image.new_from_icon_name("avatar-default", Gtk.IconSize.DIALOG)
        self.avatar_box.add(avatar)
        card_box.pack_start(self.avatar_box, False, False, 0)

        # Title & subtitle
        title = Gtk.Label(label="DragonOS")
        title.get_style_context().add_class("greeter-title")
        title.set_halign(Gtk.Align.CENTER)
        subtitle = Gtk.Label(label="Sign in to your account")
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
        inputs.pack_start(self._row(self.user_combo, "avatar-default-symbolic"), False, False, 0)

        # Password with reveal
        self.pw = Gtk.Entry()
        self.pw.set_placeholder_text("Password")
        self.pw.set_visibility(False)
        self.pw.set_hexpand(True)
        self.pw.set_activates_default(True)

        self.reveal_btn = Gtk.ToggleButton()
        self.reveal_btn.set_relief(Gtk.ReliefStyle.NONE)
        try:
            theme = Gtk.IconTheme.get_default()
            icon_name = ("view-reveal-symbolic"
                         if theme.has_icon("view-reveal-symbolic")
                         else "view-password-symbolic")
            self.reveal_btn.set_image(theme_icon(icon_name, 18))
        except Exception:
            self.reveal_btn.set_label("*")
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
            theme = Gtk.IconTheme.get_default()
            icon = ("system-shutdown-symbolic"
                    if theme.has_icon("system-shutdown-symbolic")
                    else "system-shutdown")
            shutdown_btn.set_image(theme_icon(icon, 16))
        except Exception:
            shutdown_btn.set_label("@")
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
            if " " not in prefix_text:
                pre = theme_icon(prefix_text, 16)
                pre.get_style_context().add_class("prefix-icon")
            else:
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
        css = """
        /* DragonOS greeter - glass design (Alpine Edition) */
        window { background: #0b0e13; }
        window.frame { border-radius: 16px; }
        #bq { background: transparent; }
        #bg {
          background-image: radial-gradient(120% 90% at 12% 8%, rgba(124,140,255,0.16), rgba(0,0,0,0)),
                            radial-gradient(110% 90% at 88% 92%, rgba(168,85,247,0.16), rgba(0,0,0,0)),
                            linear-gradient(to bottom, #0d1120, #0b0e13);
        }
        #greeter-card {
          background: rgba(17, 21, 30, 0.74);
          border-radius: 22px;
          border: 1px solid rgba(255,255,255,0.10);
          box-shadow: inset 0 1px 0 rgba(255,255,255,0.07),
                      0 24px 60px rgba(0,0,0,0.55);
        }
        .greeter-title {
          font-size: 26px; color: #ffffff; font-weight: 800; letter-spacing: 1px;
          text-shadow: none;
        }
        .greeter-subtitle { font-size: 12px; color: rgba(233,240,252,0.66); padding-bottom: 2px; }
        .prefix { color: #aeb6c2; padding: 8px 6px 8px 8px; }
        .prefix-icon { margin-left: 8px; opacity: 0.75; }
        eventbox { background: transparent; }
        combobox, entry {
          background: rgba(255,255,255,0.07); color: #ffffff; padding: 9px;
          border-radius: 12px; border: 1px solid rgba(255,255,255,0.07);
          box-shadow: inset 0 1px 2px rgba(0,0,0,0.25);
          caret-color: #7c8cff; transition: background 120ms ease-out;
        }
        entry:focus, combobox:focus { background: rgba(255,255,255,0.12); }
        button.primary {
          background-image: linear-gradient(120deg, #7c8cff, #a855f7);
          color: #ffffff; border-radius: 12px; padding: 10px 16px;
          font-weight: 700; border: none;
          transition: all 140ms ease-out;
        }
        button.primary:hover { padding: 10px 18px; }
        button.primary:active { background-image: linear-gradient(120deg, #6a76ee, #9147e0); }
        button {
          background: rgba(255,255,255,0.05); color: #e8eefc;
          border-radius: 12px; padding: 8px 14px;
          border: 1px solid rgba(255,255,255,0.07); font-weight: 600;
          transition: all 130ms ease-out;
        }
        button:hover { background: rgba(255,255,255,0.10); }
        button:active { background: rgba(124,140,255,0.28); }
        button.image-button { padding: 8px; border-radius: 10px; }
        .caps { color: #ffb86b; font-size: 11px; }
        .error { color: #ff7b7b; font-size: 11px; padding-right: 4px; }
        spinner { margin-left: 6px; }
        combobox { color: #ffffff; }
        label { color: #dfe7f3; }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
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
                              text="Power off the system?")
        q.set_modal(True)
        q.format_secondary_text("Make sure unsaved work has been saved.")
        resp = q.run(); q.destroy()
        if resp == Gtk.ResponseType.OK:
            try:
                if sys.platform == "darwin":
                    os.system('osascript -e \'tell app "System Events" to shut down\'')
                elif sys.platform.startswith("win"):
                    os.system("shutdown /s /t 0")
                else:
                    # Alpine-friendly: doas/sudo first, then polkit, then plain poweroff
                    for cmd in ("doas -n poweroff", "sudo -n poweroff",
                                "pkexec poweroff", "loginctl poweroff", "poweroff"):
                        if subprocess.call(["sh", "-c", cmd],
                                           stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL) == 0:
                            return
                    LOG.error("Poweroff failed: no privileged power command succeeded")
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
