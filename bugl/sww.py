import curses


class SafeWinWrapper:
    def __init__(self, win: curses.window):
        self.win = win
        self._refresh_defaults = None

    def getmaxyx(self):
        return tuple((_i - 1 for _i in self.win.getmaxyx()))

    def addstr(self, _y: int, _x: int, _str, _attr=0, _mode="cut", h_center=False, k_align=True):
        _my, _mx = self.getmaxyx()
        if type(_str) is not str:
            try:
                _str = str(_str)
            except ValueError:
                raise ValueError(f"Cannot Convert {type(_str).__name__} to string")
        if not (0 <= _x < _mx):
            raise IndexError(f"x coord out of range: not(0 <= {_x} < {_mx})")
        if not (0 <= _y < _my):
            raise IndexError(f"y coord out of range: not(0 <= {_y} < {_my})")
        if _mode == "wrap":
            if h_center is not None:
                raise TypeError("h_align argument only allowed when _mode argument is set to \"cut\"")
            if len(_str) > (_my-_y)*(_mx+1) + _mx - _x:
                raise IndexError("text overflow on the br corner")
            self.win.addstr(_y, _x, _str, _attr)
        elif _mode == "wrap_word":
            _buff = ""
            _process = []
            rows = []
            for _w in _str.split(" "):
                if "\n" in _w:
                    _process += [("" if n == 0 else "\n")+i for n, i in enumerate(_w.split("\n"))]
                else:
                    _process.append(_w)

            for _w in _process:
                if _w[0] == "\n":
                    rows.append(_buff)
                    _buff = ""
                    _w = _w[1:]
                if len(_buff) == 0 and len(_w) > _mx:
                    rows.append(_w[:_mx+1-(_x if k_align else 0)])
                elif len(_buff+_w) >= _mx+1:
                    rows.append(_buff)
                    _buff = ""
                else:
                    _buff += _w + " "
            rows.append(_buff)
            for _n, _i in enumerate(rows):
                self.win.addstr(_y + _n, _x if k_align else 0, _i, _attr)

        elif _mode == "cut":
            _str_cut = _str[:_mx-_x+1]
            if h_center:
                _x = int((_mx / 2) - (len(_str_cut)/2))
            self.win.addstr(_y, _x, _str_cut, _attr)
        else:
            raise ValueError("_mode argument must be either \"cut\", \"wrap\", \"wrap_word\" or omitted.")

    def timeout(self, *args, **kwargs):
        return self.win.timeout(*args, **kwargs)

    def getch(self):
        return self.win.getch()

    def refresh(self, *args, **kwargs):
        if self._refresh_defaults is None or len(args) > 0 or len(kwargs) > 0:
            return self.win.refresh(*args, **kwargs)
        else:
            r_args = tuple((_i if type(_i) is int else _i(None) for _i in self._refresh_defaults))
            return self.win.refresh(*r_args)

    def erase(self):
        return self.win.erase()

    def vline(self, *args, **kwargs):
        return self.win.vline(*args, **kwargs)

    def resize(self, *args, **kwargs):
        return self.win.resize(*args, **kwargs)

    def refresh_defaults(self, *args):
        self._refresh_defaults = args

    def border(self, *args, **kwargs):
        self.win.border(*args, **kwargs)

    def untouchwin(self, *args, **kwargs):
        self.win.untouchwin(*args, **kwargs)

