#!/usr/bin/env python3
"""
Terminal_plus_full.py

A more complete, "real" terminal widget for DragonOS.
Features:
 - Uses VTE (preferred) for full terminal emulation when available
 - Robust PTY fallback using pty + optional pyte ANSI emulation
 - Tabs, per-tab vertical split
 - Persistent session logs
 - Drag & drop files to feed into prompt or send to app handler
 - Quick SSH connect dialog (spawns `ssh user@host` in a new tab)
 - Search (works for PTY fallback), command palette, font control
 - Safe accelerator registration and CSS-based font handling

Dependencies (recommended):
 - GTK3 + VTE (system): on macOS use Homebrew to install vte3 / gtk+3
 - Python packages (optional enhancements): pip install pyte pexpect

Save this file and run with your virtualenv activated:
    python Terminal_plus_full.py

Swap into your project as pylauncher/Terminal.py if desired.
"""

import os
import sys
import json
import time
import threading
import pty
import fcntl
import errno
import signal
import shlex
import subprocess
import re
from typing import Optional, Callable

# ensure TERM
os.environ.setdefault('TERM', 'xterm-256color')

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango

HAVE_VTE = False
try:
    gi.require_version('Vte', '2.91')
    from gi.repository import Vte
    HAVE_VTE = True
except Exception:
    HAVE_VTE = False

# try optional pyte for better fallback rendering
HAVE_PYTE = False
try:
    import pyte
    HAVE_PYTE = True
except Exception:
    HAVE_PYTE = False

CONFIG_DIR = os.path.expanduser('~/.config/dragonos')
os.makedirs(CONFIG_DIR, exist_ok=True)
CFG_PATH = os.path.join(CONFIG_DIR, 'terminal_plus.json')
LOG_DIR = os.path.expanduser('~/.local/share/dragonos/term_logs')
os.makedirs(LOG_DIR, exist_ok=True)

DEFAULT_SHELL = os.environ.get('SHELL', '/bin/sh')

# load/save config

def load_cfg():
    try:
        with open(CFG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'font_size': 12}


def save_cfg(cfg):
    try:
        with open(CFG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass

cfg = load_cfg()

# helpers

def _safe_accel_connect(accel_group, accel_strs, func):
    if isinstance(accel_strs, str):
        accel_strs = (accel_strs,)
    for a in accel_strs:
        try:
            key, mod = Gtk.accelerator_parse(a)
            if key and key > 0:
                accel_group.connect(key, mod, Gtk.AccelFlags.VISIBLE, lambda *args: func())
                return True
        except Exception:
            pass
    return False


def append_log(name, text):
    try:
        with open(name, 'a', encoding='utf-8', errors='replace') as f:
            f.write(text)
    except Exception:
        pass

# ------------------ PTY fallback (with optional pyte) ------------------
class PtyView(Gtk.ScrolledWindow):
    def __init__(self, argv=None, cwd=None, on_write: Optional[Callable[[str], None]] = None):
        super().__init__()
        self.set_border_width(6)
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self.text = Gtk.TextView()
        self.text.set_editable(False)
        self.text.set_monospace(True)
        self.buf = self.text.get_buffer()
        self.add(self.text)
        self.show_all()

        self.argv = argv or [DEFAULT_SHELL]
        self.cwd = cwd or os.path.expanduser('~')
        self.on_write = on_write

        self.pid = None
        self.fd = None
        self.alive = False

        # pyte screen if available
        self.screen = pyte.Screen(80, 24) if HAVE_PYTE else None
        self.stream = pyte.Stream() if HAVE_PYTE else None
        if self.stream and self.screen:
            self.stream.attach(self.screen)

        self._spawn()
        self.text.connect('key-press-event', self._on_key)
        self.text.connect('button-press-event', self._on_button)

    def _spawn(self):
        def set_nb(fd):
            try:
                flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            except Exception:
                pass

        def run():
            pid, fd = pty.fork()
            if pid == 0:
                os.environ.setdefault('TERM', 'xterm-256color')
                try:
                    os.chdir(self.cwd)
                except Exception:
                    pass
                try:
                    os.execvp(self.argv[0], self.argv)
                except Exception:
                    os._exit(1)
            else:
                self.pid = pid
                self.fd = fd
                self.alive = True
                set_nb(fd)
                GLib.io_add_watch(fd, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self._on_read)
        t = threading.Thread(target=run, daemon=True)
        t.start()

    def _on_read(self, fd, cond):
        try:
            data = os.read(fd, 8192)
            if not data:
                return False
            s = data.decode('utf-8', 'replace')
            if self.stream:
                try:
                    self.stream.feed(s)
                    out = '\n'.join(self.screen.display) + '\n'
                except Exception:
                    out = re.sub(r'\x1B\[[0-?]*[ -/]*[@-~]', '', s)
            else:
                out = re.sub(r'\x1B\[[0-?]*[ -/]*[@-~]', '', s)
            GLib.idle_add(self._append, out)
            append_log(os.path.join(LOG_DIR, f'session-{self.pid}.log'), out)
        except OSError as e:
            if e.errno in (errno.EIO, errno.EBADF):
                return False
        except Exception:
            return False
        return True

    def _append(self, s):
        end = self.buf.get_end_iter()
        self.buf.insert(end, s)
        vadj = self.get_vadjustment()
        try:
            vadj.set_value(vadj.get_upper() - vadj.get_page_size())
        except Exception:
            pass

    def _on_key(self, widget, ev):
        txt = ev.string
        if txt:
            try:
                os.write(self.fd, txt.encode())
            except Exception:
                pass
            return True
        key = Gdk.keyval_name(ev.keyval)
        if key == 'Return':
            try:
                os.write(self.fd, b'\n')
            except Exception:
                pass
            return True
        if key == 'BackSpace':
            try:
                os.write(self.fd, b'\x7f')
            except Exception:
                pass
            return True
        return False

    def _on_button(self, widget, ev):
        if ev.button == 3:
            m = Gtk.Menu()
            mi_copy = Gtk.MenuItem(label='Copy')
            mi_paste = Gtk.MenuItem(label='Paste')
            mi_copy.connect('activate', lambda *_: self._do_copy())
            mi_paste.connect('activate', lambda *_: self._do_paste())
            m.append(mi_copy); m.append(mi_paste); m.show_all(); m.popup_at_pointer(ev)
            return True
        return False

    def _do_copy(self):
        sel = self.buf.get_selection_bounds()
        if sel:
            a, b = sel
            text = self.buf.get_text(a, b, True)
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)

    def _do_paste(self):
        txt = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).wait_for_text()
        if txt:
            try:
                os.write(self.fd, txt.encode())
            except Exception:
                pass

    def write(self, s: str):
        try:
            os.write(self.fd, s.encode())
            if callable(self.on_write):
                self.on_write(s)
        except Exception:
            pass

    def feed_text(self, s: str):
        safe = shlex.quote(s)
        self.write(safe + ' ')

    def destroy(self):
        try:
            if self.pid:
                os.kill(self.pid, signal.SIGTERM)
        except Exception:
            pass
        super().destroy()

# ------------------ VTE wrapper ------------------
if HAVE_VTE:
    class VteView(Gtk.Box):
        def __init__(self, argv=None, cwd=None, on_write: Optional[Callable[[str], None]] = None):
            super().__init__()
            self.set_orientation(Gtk.Orientation.VERTICAL)
            self.terminal = Vte.Terminal()
            self.pack_start(self.terminal, True, True, 0)
            self.argv = argv or [DEFAULT_SHELL]
            self.cwd = cwd or os.path.expanduser('~')
            self.on_write = on_write
            env = os.environ.copy(); env.setdefault('TERM', 'xterm-256color')
            try:
                self.terminal.spawn_async(Vte.PtyFlags.DEFAULT, self.cwd, self.argv, env, GLib.SpawnFlags.DO_NOT_REAP_CHILD, None, None, -1, None, None)
            except Exception:
                try:
                    self.terminal.spawn_sync(Vte.PtyFlags.DEFAULT, self.cwd, self.argv, env)
                except Exception:
                    pass
            try:
                self.terminal.set_allow_hyperlink(True)
            except Exception:
                pass

        def write(self, s: str):
            try:
                self.terminal.feed(s)
                if callable(self.on_write):
                    self.on_write(s)
            except Exception:
                try:
                    self.terminal.feed_child(s.encode(), -1)
                except Exception:
                    pass

        def feed_text(self, s: str):
            self.write(s)

        def destroy(self):
            try:
                pid = self.terminal.get_child_pid()
                if pid and pid > 0:
                    os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
            super().destroy()

# ------------------ Terminal Tab (split-capable) ------------------
class TermTab(Gtk.Box):
    def __init__(self, argv=None, cwd=None, on_file_drop: Optional[Callable[[str], None]] = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.argv = argv; self.cwd = cwd; self.on_file_drop = on_file_drop
        self.split = False
        self._build_single()

    def _build_single(self):
        View = VteView if HAVE_VTE else PtyView
        self.left = View(self.argv, self.cwd, on_write=self._on_write)
        self.pack_start(self.left, True, True, 0)
        self.show_all()

    def _build_split(self):
        try: self.remove(self.left)
        except Exception: pass
        self.paned = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        View = VteView if HAVE_VTE else PtyView
        self.pane_left = View(self.argv, self.cwd, on_write=self._on_write)
        self.pane_right = View(self.argv, self.cwd, on_write=self._on_write)
        self.paned.add1(self.pane_left); self.paned.add2(self.pane_right)
        self.pack_start(self.paned, True, True, 0)
        self.show_all()

    def toggle_split(self):
        if not self.split:
            self._build_split(); self.split = True
        else:
            try: self.remove(self.paned); self.paned.destroy()
            except Exception: pass
            self._build_single(); self.split = False

    def write(self, s: str):
        try:
            if self.split:
                self.pane_left.write(s)
            else:
                self.left.write(s)
        except Exception:
            pass

    def feed_text(self, s: str):
        if self.split:
            self.pane_left.feed_text(s)
        else:
            self.left.feed_text(s)

    def _on_write(self, s: str):
        append_log(os.path.join(LOG_DIR, f'tab-{int(time.time())}.log'), s)

    def destroy(self):
        try:
            if self.split:
                self.pane_left.destroy(); self.pane_right.destroy(); self.paned.destroy()
            else:
                self.left.destroy()
        except Exception:
            pass
        super().destroy()

# ---------------- Command Palette and dialogs ----------------
class CommandPalette(Gtk.Dialog):
    def __init__(self, parent, commands):
        super().__init__(title='Palette', transient_for=parent, flags=0)
        self.add_button('Cancel', Gtk.ResponseType.CANCEL)
        self.set_default_size(480, 260)
        box = self.get_content_area(); box.set_border_width(8)
        self.search = Gtk.SearchEntry(); box.pack_start(self.search, False, False, 0)
        self.list = Gtk.ListBox(); box.pack_start(self.list, True, True, 0)
        for name, cb in commands:
            r = Gtk.ListBoxRow(); h = Gtk.Box(spacing=6); lbl = Gtk.Label(label=name); h.pack_start(lbl, True, True, 0); r.add(h); r._cb = cb; self.list.add(r)
        self.list.show_all(); self.search.connect('changed', self._filter); self.list.connect('row-activated', lambda lb,r: self._run(r))

    def _filter(self, e):
        q = e.get_text().lower()
        for r in self.list.get_children():
            child = r.get_child(); label = child.get_children()[0].get_label(); r.set_visible(q in label.lower())

    def _run(self, row):
        try: row._cb(); self.response(Gtk.ResponseType.OK)
        except Exception: self.response(Gtk.ResponseType.CANCEL)

class SSHDialog(Gtk.Dialog):
    def __init__(self, parent):
        super().__init__(title='Quick SSH', transient_for=parent, flags=0)
        self.add_button('Cancel', Gtk.ResponseType.CANCEL); self.add_button('Connect', Gtk.ResponseType.OK)
        box = self.get_content_area(); box.set_border_width(8)
        self.entry = Gtk.Entry(); self.entry.set_placeholder_text('user@host'); box.pack_start(self.entry, False, False, 0); self.show_all()

    def get_target(self):
        return self.entry.get_text().strip()

class SearchDialog(Gtk.Dialog):
    def __init__(self, parent, tab: TermTab):
        super().__init__(title='Find', transient_for=parent, flags=0)
        self.add_button('Close', Gtk.ResponseType.CLOSE)
        box = self.get_content_area(); box.set_border_width(8)
        self.entry = Gtk.Entry(); self.entry.set_placeholder_text('Find text...'); box.pack_start(self.entry, False, False, 0)
        self.tab = tab; self.entry.connect('activate', lambda e: self.find(e.get_text()))
        self.show_all()

    def find(self, q):
        if not q: return
        # only supports PTY fallback search
        view = getattr(self.tab.left, 'text', None) or getattr(getattr(self.tab,'pane_left',None), 'text', None)
        if not view:
            dlg = Gtk.MessageDialog(transient_for=self, flags=0, message_type=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.OK, text='Search not available')
            dlg.format_secondary_text('Search only supported in fallback TextView. Use grep in shell for VTE.')
            dlg.run(); dlg.destroy(); return
        buf = view.get_buffer(); start = buf.get_start_iter(); found = start.forward_search(q, 0, None)
        if found:
            a,b = found; view.scroll_to_iter(a, 0.0, True, 0.5,0.5); buf.select_range(a,b)

# ---------------- Main Window ----------------
class TerminalPlusWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title='DragonOS Terminal Plus')
        self.set_default_size(1100, 720)
        self.connect('destroy', Gtk.main_quit)

        # CSS provider for font
        self.css = Gtk.CssProvider(); self.css_template = '.term-plus {{ font-family: monospace; font-size: {}px; }}'
        screen = Gdk.Screen.get_default(); Gtk.StyleContext.add_provider_for_screen(screen, self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6); self.add(self.vbox)
        toolbar = self._make_toolbar(); self.vbox.pack_start(toolbar, False, False, 0)
        self.nb = Gtk.Notebook(); self.nb.set_scrollable(True); self.vbox.pack_start(self.nb, True, True, 0)
        self.status = Gtk.Statusbar(); self.vbox.pack_start(self.status, False, False, 0)

        self.accel = Gtk.AccelGroup(); self.add_accel_group(self.accel)
        self._bind_accels()

        self.font_size = cfg.get('font_size', 12)
        self.new_tab()
        self.show_all()

    def _make_toolbar(self):
        hb = Gtk.Box(spacing=6)
        b_new = Gtk.Button(label='New'); b_new.connect('clicked', lambda *_: self.new_tab()); hb.pack_start(b_new, False, False, 0)
        b_close = Gtk.Button(label='Close'); b_close.connect('clicked', lambda *_: self.close_tab()); hb.pack_start(b_close, False, False, 0)
        b_split = Gtk.Button(label='Split'); b_split.connect('clicked', lambda *_: self.toggle_split()); hb.pack_start(b_split, False, False, 0)
        b_ssh = Gtk.Button(label='SSH'); b_ssh.connect('clicked', lambda *_: self.quick_ssh()); hb.pack_start(b_ssh, False, False, 0)
        b_palette = Gtk.Button(label='Palette'); b_palette.connect('clicked', lambda *_: self.show_palette()); hb.pack_start(b_palette, False, False, 0)
        return hb

    def _bind_accels(self):
        _safe_accel_connect(self.accel, '<Control>t', lambda: self.new_tab())
        _safe_accel_connect(self.accel, '<Control>w', lambda: self.close_tab())
        _safe_accel_connect(self.accel, ('<Control>plus','<Control>equal','<Control>KP_Add'), lambda: self.change_font(1))
        _safe_accel_connect(self.accel, ('<Control>minus','<Control>KP_Subtract'), lambda: self.change_font(-1))
        _safe_accel_connect(self.accel, '<Control>0', lambda: self.reset_font())
        _safe_accel_connect(self.accel, '<Control>p', lambda: self.show_palette())
        _safe_accel_connect(self.accel, '<Control>f', lambda: self.show_search())
        _safe_accel_connect(self.accel, ('<Control>\\','<Control>backslash'), lambda: self.toggle_split())

    def new_tab(self, argv=None, cwd=None):
        tab = TermTab(argv=argv, cwd=cwd, on_file_drop=self._on_file_drop)
        lbl = Gtk.Label(label='Shell')
        idx = self.nb.append_page(tab, lbl); self.nb.set_current_page(idx)
        self._apply_font(tab); self.nb.show_all(); return tab

    def close_tab(self):
        p = self.nb.get_current_page();
        if p >= 0:
            child = self.nb.get_nth_page(p)
            try: child.destroy()
            except Exception: pass
            self.nb.remove_page(p)

    def switch_next(self):
        n = self.nb.get_n_pages();
        if n < 2: return
        i = self.nb.get_current_page(); self.nb.set_current_page((i+1)%n)

    def switch_prev(self):
        n = self.nb.get_n_pages();
        if n < 2: return
        i = self.nb.get_current_page(); self.nb.set_current_page((i-1)%n)

    def toggle_split(self):
        p = self.nb.get_current_page();
        if p < 0: return
        tab = self.nb.get_nth_page(p); tab.toggle_split(); self._apply_font(tab)

    def _apply_font(self, tab):
        try:
            css = self.css_template.format(self.font_size)
            self.css.load_from_data(css.encode('utf-8'))
        except Exception:
            pass
        try:
            if hasattr(tab, 'left'):
                if isinstance(tab.left, PtyView):
                    tab.left.text.get_style_context().add_class('term-plus')
                elif HAVE_VTE and hasattr(tab.left, 'terminal'):
                    try:
                        desc = Pango.FontDescription(f'monospace {self.font_size}')
                        tab.left.terminal.set_font(desc)
                    except Exception:
                        pass
        except Exception:
            pass

    def change_font(self, delta):
        self.font_size = max(8, min(32, self.font_size + delta)); cfg['font_size'] = self.font_size; save_cfg(cfg)
        for i in range(self.nb.get_n_pages()): self._apply_font(self.nb.get_nth_page(i))

    def reset_font(self): self.font_size = 12; cfg['font_size'] = self.font_size; save_cfg(cfg);

    def show_palette(self):
        cmds = [('New tab', lambda: self.new_tab()), ('Close tab', lambda: self.close_tab()), ('SSH quick', lambda: self.quick_ssh())]
        dlg = CommandPalette(self, cmds); dlg.run(); dlg.destroy()

    def quick_ssh(self):
        dlg = SSHDialog(self)
        if dlg.run() == Gtk.ResponseType.OK:
            target = dlg.get_target()
            dlg.destroy()
            if target:
                # spawn ssh in new tab
                argv = ['ssh', target]
                self.new_tab(argv=argv)
        else:
            dlg.destroy()

    def show_search(self):
        p = self.nb.get_current_page();
        if p < 0: return
        tab = self.nb.get_nth_page(p)
        dlg = SearchDialog(self, tab); dlg.run(); dlg.destroy()

    def _on_file_drop(self, path):
        # default: open file in editor or print path — you can override to open in MusicPlayer etc
        dlg = Gtk.MessageDialog(transient_for=self, flags=0, message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.NONE, text='File dropped')
        dlg.format_secondary_text(path)
        dlg.add_button('Type into prompt', Gtk.ResponseType.YES); dlg.add_button('Open with app', Gtk.ResponseType.NO); dlg.add_button('Cancel', Gtk.ResponseType.CANCEL)
        res = dlg.run(); dlg.destroy()
        if res == Gtk.ResponseType.YES:
            p = self.nb.get_current_page();
            if p < 0: return
            tab = self.nb.get_nth_page(p); tab.feed_text(path)
        elif res == Gtk.ResponseType.NO:
            subprocess.Popen(['xdg-open' if os.name != 'darwin' else 'open', path])

# ---------------- run ----------------
if __name__ == '__main__':
    win = TerminalPlusWindow(); win.show_all(); Gtk.main()
