# apps.py
import gi, os, stat
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, Pango

class Calculator(Gtk.Box):
    """
    Zamonaviy, xavfsiz kalkulyator widget.
    Drop-in replacement: eski Calculator klassi o'rniga shu klassni qo'ying.
    """
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.set_border_width(6)

        # hisoblash uchun saqlanuvchi (oldingi natija) va memory
        self.ans = 0
        self.memory = 0

        # Display (entry) va natija label
        self.entry = Gtk.Entry(); self.entry.set_placeholder_text("Ifoda kiriting — misol: (12+3)*4  yoki  sin(pi/2)")
        self.entry.set_hexpand(True)
        self.entry.connect("activate", self.on_evaluate)  # Enter bilan hisoblash
        self.pack_start(self.entry, False, False, 0)

        self.result_lbl = Gtk.Label(label="Natija: "); self.result_lbl.set_xalign(0)
        self.pack_start(self.result_lbl, False, False, 0)

        # Buttons grid
        grid = Gtk.Grid(row_spacing=6, column_spacing=6)
        self.pack_start(grid, True, True, 0)

        # tugmalar ro'yxati (row, col, label, callback or text to insert)
        buttons = [
            (0,0,'MC', self.on_mem_clear), (0,1,'MR', self.on_mem_recall), (0,2,'M+', self.on_mem_add), (0,3,'M-', self.on_mem_sub),
            (1,0,'7', lambda *_: self.insert('7')), (1,1,'8', lambda *_: self.insert('8')), (1,2,'9', lambda *_: self.insert('9')), (1,3,'÷', lambda *_: self.insert('/')),
            (2,0,'4', lambda *_: self.insert('4')), (2,1,'5', lambda *_: self.insert('5')), (2,2,'6', lambda *_: self.insert('6')), (2,3,'×', lambda *_: self.insert('*')),
            (3,0,'1', lambda *_: self.insert('1')), (3,1,'2', lambda *_: self.insert('2')), (3,2,'3', lambda *_: self.insert('3')), (3,3,'-', lambda *_: self.insert('-')),
            (4,0,'0', lambda *_: self.insert('0')), (4,1,'.', lambda *_: self.insert('.')), (4,2,'%', lambda *_: self.insert('%')), (4,3,'+', lambda *_: self.insert('+')),
        ]
        for r,c,label,cb in buttons:
            btn = Gtk.Button(label=label); btn.set_size_request(64, 40)
            btn.connect("clicked", cb)
            grid.attach(btn, c, r, 1, 1)

        # ops column: parentheses, power, backspace, clear, equals
        ops = [
            (0,4,'(' , lambda *_: self.insert('(')),
            (1,4,')' , lambda *_: self.insert(')')),
            (2,4,'^' , lambda *_: self.insert('^')),  # will be mapped to **
            (3,4,'⌫' , self.on_backspace),
            (4,4,'C' , self.on_clear),
        ]
        for r,c,label,cb in ops:
            btn = Gtk.Button(label=label); btn.set_size_request(64, 40)
            btn.connect("clicked", cb)
            grid.attach(btn, c, r, 1, 1)

        # Right-side: functions column
        funcs = [
            (0,5,'sin', lambda *_: self.insert('sin(')),
            (1,5,'cos', lambda *_: self.insert('cos(')),
            (2,5,'tan', lambda *_: self.insert('tan(')),
            (3,5,'sqrt', lambda *_: self.insert('sqrt(')),
            (4,5,'log', lambda *_: self.insert('log(')),  # natural log (use log10 if needed)
        ]
        for r,c,label,cb in funcs:
            btn = Gtk.Button(label=label); btn.set_size_request(72, 40)
            btn.connect("clicked", cb)
            grid.attach(btn, c, r, 1, 1)

        # Equals and advanced row
        eq_btn = Gtk.Button(label="="); eq_btn.set_size_request(300, 40)
        eq_btn.connect("clicked", self.on_evaluate)
        # place equals below grid (span columns)
        self.pack_start(eq_btn, False, False, 0)

        # Touch-bar like quick action bar (pastki panel)
        touchbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        touchbar.set_halign(Gtk.Align.FILL)
        touchbar.set_margin_top(6)
        quicks = [
            ('π', lambda *_: self.insert('pi')),
            ('e', lambda *_: self.insert('e')),
            ('Ans', lambda *_: self.insert('Ans')),
            ('x²', lambda *_: self.insert('**2')),
            ('ⁿ', lambda *_: self.insert('**')),
            ('%', lambda *_: self.insert('%')),
            ('Copy', self.on_copy_result),
        ]
        for label, cb in quicks:
            b = Gtk.Button(label=label); b.set_size_request(72, 36); b.connect("clicked", cb); touchbar.pack_start(b, False, False, 0)
        self.pack_start(touchbar, False, False, 0)

        # key press support for backspace
        self.entry.connect("key-press-event", self.on_keypress)

    # ---------- helper methods ----------
    def insert(self, text):
        """Insert text at cursor in entry."""
        cur, sel = self.entry.get_position(), self.entry.get_selection_bounds()
        if sel:
            start, end = sel
            self.entry.delete_text(start, end)
            self.entry.set_position(start)
            cur = start
        self.entry.insert_text(text, cur)
        # move cursor after inserted text
        self.entry.set_position(cur + len(text))

    def on_backspace(self, *_):
        pos = self.entry.get_position()
        if pos > 0:
            start = pos - 1
            self.entry.delete_text(start, pos)
            self.entry.set_position(start)

    def on_clear(self, *_):
        self.entry.set_text('')
        self.result_lbl.set_text("Natija: ")

    def on_mem_clear(self, *_):
        self.memory = 0
        self.result_lbl.set_text("Memory cleared")

    def on_mem_recall(self, *_):
        # insert memory value
        self.insert(str(self.memory))

    def on_mem_add(self, *_):
        try:
            val = self._safe_eval(self._preprocess(self.entry.get_text()))
            self.memory += val
            self.result_lbl.set_text(f"Memory = {self.memory}")
        except Exception as e:
            self.result_lbl.set_text(f"Memory error: {e}")

    def on_mem_sub(self, *_):
        try:
            val = self._safe_eval(self._preprocess(self.entry.get_text()))
            self.memory -= val
            self.result_lbl.set_text(f"Memory = {self.memory}")
        except Exception as e:
            self.result_lbl.set_text(f"Memory error: {e}")

    def on_copy_result(self, *_):
        text = self.result_lbl.get_text().replace("Natija: ", "")
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD) if hasattr(Gtk, 'Clipboard') else None
        # Fallback: set to entry
        try:
            if clipboard:
                clipboard.set_text(text, -1)
        except Exception:
            pass

    def on_keypress(self, widget, event):
        key = event.keyval
        # Backspace
        if key == 65288:  # GDK_KEY_BackSpace
            self.on_backspace()
        return False

    # ---------- evaluation ----------
    def on_evaluate(self, *_):
        expr = self.entry.get_text().strip()
        if not expr:
            self.result_lbl.set_text("Natija: ")
            return
        try:
            proc = self._preprocess(expr)
            val = self._safe_eval(proc)
            self.ans = val
            self._display_result(val)
        except Exception as e:
            self.result_lbl.set_text(f"Xatolik: {e}")

    def _display_result(self, val):
        # pretty format
        if isinstance(val, float):
            if abs(val - int(val)) < 1e-12:
                val = int(round(val))
            else:
                # limit to 12 significant digits
                val = float(f"{val:.12g}")
        self.result_lbl.set_text(f"Natija: {val}")

    def _preprocess(self, expr: str) -> str:
        """Preprocess user-friendly tokens to Python-expression."""
        s = expr.replace('^', '**')
        s = s.replace('×', '*').replace('÷', '/')
        # convert percentage like 50% -> (50/100)
        # also 12.5% etc.
        s = re.sub(r'(\d+(\.\d+)?)\%', r'(\1/100)', s)
        # replace Ans and pi,e with lowercase python names
        s = re.sub(r'\bAns\b', str(self.ans), s)
        s = s.replace('π', 'pi')
        return s

    def _safe_eval(self, expr: str):
        """
        Safe evaluator using ast. Supports arithmetic, pow, unary ops, function calls
        to allowed functions, and constants (pi, e).
        """
        # allowed functions mapping
        funcs = {
            'sin': math.sin, 'cos': math.cos, 'tan': math.tan,
            'asin': math.asin, 'acos': math.acos, 'atan': math.atan,
            'sqrt': math.sqrt, 'log': math.log, 'log10': math.log10,
            'exp': math.exp, 'abs': abs, 'factorial': math.factorial,
            'degrees': math.degrees, 'radians': math.radians,
            'pow': pow, 'round': round
        }
        names = {'pi': math.pi, 'e': math.e}
        # parse
        node = ast.parse(expr, mode='eval')

        def _eval(n):
            if isinstance(n, ast.Expression):
                return _eval(n.body)
            if isinstance(n, ast.Constant):
                return n.value
            if isinstance(n, ast.Num):  # for older py versions
                return n.n
            if isinstance(n, ast.BinOp):
                left = _eval(n.left); right = _eval(n.right)
                op = n.op
                if isinstance(op, ast.Add): return left + right
                if isinstance(op, ast.Sub): return left - right
                if isinstance(op, ast.Mult): return left * right
                if isinstance(op, ast.Div): return left / right
                if isinstance(op, ast.FloorDiv): return left // right
                if isinstance(op, ast.Mod): return left % right
                if isinstance(op, ast.Pow): return left ** right
                raise ValueError("Ruxsat etilmagan binop")
            if isinstance(n, ast.UnaryOp):
                operand = _eval(n.operand)
                if isinstance(n.op, ast.UAdd): return +operand
                if isinstance(n.op, ast.USub): return -operand
                raise ValueError("Ruxsat etilmagan unary op")
            if isinstance(n, ast.Call):
                if not isinstance(n.func, ast.Name):
                    raise ValueError("Noto'g'ri funksiya chaqiruvi")
                fname = n.func.id
                if fname not in funcs:
                    raise ValueError(f"Ruxsat etilmagan funksiya: {fname}")
                args = [_eval(a) for a in n.args]
                return funcs[fname](*args)
            if isinstance(n, ast.Name):
                if n.id in names:
                    return names[n.id]
                # allow Ans as previously preprocessed
                raise ValueError(f"Ruxsat etilmagan identifikator: {n.id}")
            if isinstance(n, ast.Tuple):
                return tuple(_eval(elt) for elt in n.elts)
            raise ValueError("Ruxsat etilmagan konstruksiya")
        return _eval(node)