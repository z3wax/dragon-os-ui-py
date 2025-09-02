#!/usr/bin/env python3
# coding: utf-8
"""
Geditor.py — Minimal, zamonaviy terminal-kod muharriri + PTY terminal.
To'liq ishchi misol. macOS / Linux uchun mos.
Talablar:
    pip install prompt_toolkit pygments
"""

import os
import sys
import threading
import time
import pty
import selectors
from typing import Optional

from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import (
    HSplit,
    VSplit,
    Layout,
    Window,
    ConditionalContainer,
)
from prompt_toolkit.widgets import TextArea, Frame, Button, Label, Box
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.lexers import PygmentsLexer
from pygments.lexers import get_lexer_for_filename, PythonLexer
from prompt_toolkit.styles import Style
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.margins import NumberedMargin
from prompt_toolkit.filters import Condition
from prompt_toolkit.shortcuts import input_dialog

ROOT_DIR = os.path.abspath(".")

# -------------------------
# PTY backend
# -------------------------
class PTYProcess:
    """
    Spawn a shell in a pty and provide simple read/write interface.
    """

    def __init__(self, shell: Optional[str] = None):
        self.shell = shell or os.environ.get("SHELL", "/bin/bash")
        self.master_fd = None
        self.pid = None
        self._running = False

    def spawn(self):
        master, slave = pty.openpty()
        self.master_fd = master
        pid = os.fork()
        if pid == 0:
            # child
            os.setsid()
            os.dup2(slave, 0)
            os.dup2(slave, 1)
            os.dup2(slave, 2)
            try:
                os.execvp(self.shell, [self.shell])
            finally:
                os._exit(1)
        else:
            # parent
            try:
                os.close(slave)
            except Exception:
                pass
            self.pid = pid
            self._running = True

    def read(self, n=1024) -> bytes:
        if self.master_fd is None:
            return b""
        try:
            return os.read(self.master_fd, n)
        except BlockingIOError:
            return b""
        except OSError:
            return b""

    def write(self, data: bytes):
        if self.master_fd is None:
            return
        try:
            os.write(self.master_fd, data)
        except OSError:
            pass

    def is_running(self):
        return self._running

    def close(self):
        try:
            if self.master_fd is not None:
                os.close(self.master_fd)
        except Exception:
            pass
        self._running = False


# -------------------------
# File tree helper
# -------------------------
def list_dir_tree(path: str):
    items = []
    try:
        names = sorted(os.listdir(path), key=lambda n: (not os.path.isdir(os.path.join(path, n)), n.lower()))
    except Exception:
        names = []
    for name in names:
        full = os.path.join(path, name)
        items.append((os.path.isdir(full), full, name))
    return items


# -------------------------
# App creator
# -------------------------
def create_app(root_dir: str = ROOT_DIR):
    # Buffers and controls
    editor_buffer = Buffer()
    terminal_view = TextArea(style="class:terminal", text="", scrollbar=True, focusable=False, wrap_lines=False)
    terminal_input = TextArea(height=1, prompt="$ ", multiline=False)
    editor_control = BufferControl(buffer=editor_buffer, lexer=PygmentsLexer(PythonLexer))
    editor_window = Window(content=editor_control, wrap_lines=False, left_margins=[NumberedMargin()])

    title_bar = Label(text=[("class:title", "  DragonOS — Geditor  "), ("class:subtitle", "  Ctrl-S save  ·  Ctrl-Q quit  ·  F2 toggle terminal  ·  F3 open file")])

    # File list area
    file_list_text = TextArea(text="", focusable=True, scrollbar=True, width=36)

    def refresh_file_list():
        entries = list_dir_tree(root_dir)
        lines = []
        lines.append(f"📁 {os.path.basename(root_dir) or root_dir}")
        for is_dir, full, name in entries:
            prefix = "📂" if is_dir else "📄"
            lines.append(f"{prefix} {name}")
        file_list_text.text = "\n".join(lines)

    refresh_file_list()

    # PTY process
    ptyproc = PTYProcess()
    ptyproc.spawn()

    # Terminal reader thread (append output to terminal_view)
    reader_stop = threading.Event()

    def terminal_reader(app_ref):
        while not reader_stop.is_set() and ptyproc.is_running():
            data = ptyproc.read(4096)
            if not data:
                time.sleep(0.03)
                continue
            try:
                s = data.decode("utf-8", errors="replace")
            except Exception:
                s = repr(data)
            def _append():
                terminal_view.buffer.insert_text(s)
                terminal_view.buffer.cursor_position = len(terminal_view.buffer.text)
            # use call_from_executor for thread-safe UI update
            try:
                app_ref.call_from_executor(_append)
            except Exception:
                # maybe app closed
                break

    # send terminal input
    def send_terminal_input():
        txt = terminal_input.text
        if not txt.endswith("\n"):
            txt += "\n"
        ptyproc.write(txt.encode("utf-8"))
        terminal_input.text = ""

    # current file path
    current_path = {"path": None}

    def open_file_by_name(name: str):
        full = os.path.join(root_dir, name)
        if not os.path.exists(full):
            status_bar.text = f"Not found: {name}"
            return
        try:
            with open(full, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            content = f"# Error opening file: {e}\n"
        editor_buffer.text = content
        current_path["path"] = full
        try:
            lex = get_lexer_for_filename(full)
            editor_control.lexer = PygmentsLexer(lex.__class__)
        except Exception:
            editor_control.lexer = PygmentsLexer(PythonLexer)
        status_bar.text = f"Opened: {full}"

    def save_current_file():
        path = current_path["path"]
        if not path:
            path = os.path.join(root_dir, "untitled.py")
            current_path["path"] = path
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(editor_buffer.text)
            status_bar.text = f"Saved: {path}"
            refresh_file_list()
        except Exception as e:
            status_bar.text = f"Save error: {e}"

    # Status bar
    status_bar = Label(text=" Ready ", style="class:status")

    # Key bindings
    kb = KeyBindings()

    @kb.add("c-q")
    def _(event):
        try:
            reader_stop.set()
            ptyproc.close()
        except Exception:
            pass
        event.app.exit()

    @kb.add("c-s")
    def _(event):
        save_current_file()

    # show_terminal as mutable container
    show_terminal = [True]

    @kb.add("f2")
    def _(event):
        show_terminal[0] = not show_terminal[0]
        # force redraw
        event.app.invalidate()

    @kb.add("f3")
    def _(event):
        filename = input_dialog(title="Open file", text="Relative path from project root:").run()
        if filename:
            open_file_by_name(filename)

    @kb.add("enter")
    def _(event):
        # If terminal_input has focus -> send to PTY
        if event.app.layout.has_focus(terminal_input):
            send_terminal_input()
            event.app.layout.focus(terminal_input)
        else:
            # insert newline into editor buffer
            event.app.layout.current_buffer.insert_text("\n")

    # Buttons
    btn_save = Button(text="Save (Ctrl+S)", handler=lambda: save_current_file())
    btn_toggle = Button(text="Toggle Terminal (F2)", handler=lambda: (show_terminal.__setitem__(0, not show_terminal[0]), app.invalidate()))

    # Layout
    left_column = HSplit(
        [
            Frame(body=file_list_text, title="Project"),
            Box(body=VSplit([btn_save, btn_toggle]), height=3),
        ],
        width=36,
    )

    editor_frame = Frame(body=editor_window, title="Editor", style="class:editor.frame")

    terminal_column = HSplit(
        [
            Frame(body=terminal_view, title="Terminal Output", height=12),
            Frame(body=terminal_input, title="Terminal Input (type & Enter)", height=3),
        ]
    )

    main_area = HSplit(
        [
            title_bar,
            VSplit(
                [
                    left_column,
                    editor_frame,
                ],
                padding=1,
            ),
            # Use Condition filter (expects a callable returning bool)
            ConditionalContainer(content=terminal_column, filter=Condition(lambda: show_terminal[0])),
            status_bar,
        ]
    )

    style = Style.from_dict(
        {
            "title": "bold ansicyan",
            "subtitle": "italic ansigreen",
            "status": "reverse",
            "editor.frame": "bg:#1c1f24",
            "terminal": "bg:#0f1720",
        }
    )

    app = Application(layout=Layout(main_area), key_bindings=kb, mouse_support=True, full_screen=True, style=style)

    # start reader thread after app created
    reader_thread = threading.Thread(target=lambda: terminal_reader(app), daemon=True)
    def start_reader():
        if not reader_thread.is_alive():
            reader_thread.start()
    app.pre_run_callables.append(start_reader)

    return app


if __name__ == "__main__":
    app = create_app(ROOT_DIR)
    try:
        app.run()
    except Exception as e:
        print("App error:", e, file=sys.stderr)
