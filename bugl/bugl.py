import curses
import subprocess
import os
from datetime import datetime, timedelta
from TopongoConfig.configs import Configs


def prepare_path(_f, _c_f=False):
    _f = os.path.expandvars(_f)
    if not os.path.exists(_f):
        if not os.path.exists(os.path.dirname(_f)):
            os.makedirs(os.path.dirname(_f))
        if _c_f:
            with open(_f, "a"):
                pass


def time_elapsed(_d: timedelta, none_word="Now", form="{}"):
    out = ""
    if _d.days:
        out += f"{_d.days} day(s) and {int(_d.seconds/60/60%24)} hour(s)"
    elif int(_d.seconds/60/60) > 0:
        out += f"{int(_d.seconds/60/60)} hour(s) and {int(_d.seconds/60%60)} min(s)"
    elif int(_d.seconds/60) > 0:
        out += f"{int(_d.seconds/60)} min(s)"

    elif int(_d.seconds) > 0:
        out += f"{int(_d.seconds)} sec(s)"
    else:
        out += none_word
    return form.format(out)


class SafeWinWrapper:
    def __init__(self, win: curses.window):
        self.win = win
        self._refresh_defaults = None

    def getmaxyx(self):
        return tuple((_i - 1 for _i in self.win.getmaxyx()))

    def addstr(self, _y: int, _x: int, _str, _attr=0, _mode="cut", h_align=None, k_align=True):
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
            if h_align is not None:
                raise TypeError("h_align argument only allowed when _mode argument is set to \"cut\"")
            if len(_str) > (_my-_y)*(_mx+1) + _mx - _x:
                raise IndexError("text overflow on the br corner")
            self.win.addstr(_y, _x, _str, _attr)
        elif _mode == "wrap_word":
            _str.replace("\n", " ")
            _l = 0
            _buff = ""
            for _w in _str.split():
                if len(_buff) == 0 and len(_w):
                    self.win.addstr(_y+_l, _x if (k_align or _l == 0) else 0, _w[:_mx+1-(_x if (k_align or _l == 0) else 0)], _attr)
                    _l += 1
                elif len(_buff+_w) >= _mx+1:
                    self.win.addstr(_y+_l, _x, _buff, _attr)
                    _buff = ""
                    _l += 1
                else:
                    _buff += _w
            self.win.addstr(_y+_l, _x, _buff, _attr)

        elif _mode == "cut":
            self.win.addstr(_y, _x, _str[:_mx-_x+1], _attr)
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

    def clear(self):
        return self.win.clear()

    def vline(self, *args, **kwargs):
        return self.win.vline(*args, **kwargs)

    def resize(self, *args, **kwargs):
        return self.win.resize(*args, **kwargs)

    def refresh_defaults(self, *args):
        self._refresh_defaults = args


class Game:
    class PollBeforeStartException(Exception):
        pass

    def __init__(self, conf: Configs, bugl):
        self.conf = self.GameConfig(conf, bugl.conf)
        self.bugl = bugl
        self._proc = None
        self._session_started = False
        self._init_playtime = self.conf.get("playtime")
        prepare_path(self.conf.get("stdout"))
        prepare_path(self.conf.get("stderr"))

    def run(self):
        args = [self.conf.get("exec"), self.conf.get("exec_path")] + self.conf.get("exec_args")
        self.conf.set("latest_launch", datetime.now().timestamp())
        self.conf.game_conf.write()
        self._session_started = True
        self._proc = subprocess.Popen(args,
                                      stdout=open(self.conf.get("stdout"), "w+"),
                                      stderr=open(self.conf.get("stderr"), "w+"),
                                      stdin=subprocess.DEVNULL)

    def name(self):
        return self.conf.get("name") + (" (Running)" if self.is_alive() else "")

    def is_alive(self):
        return self._proc is not None and self._proc.poll() is None

    def poll(self):
        if self._proc is None:
            raise self.PollBeforeStartException
        else:
            return self._proc.poll()

    def update_playtime(self):
        if self._session_started:
            t_e = datetime.now() - datetime.fromtimestamp(self.conf.get("latest_launch"))
            self.conf.set("playtime", self._init_playtime + t_e.total_seconds())
            self.conf.game_conf.write()
            if not self.is_alive():
                self._session_started = False
                self._init_playtime = self.conf.get("playtime")

    def wait(self):
        self._proc.wait()

    class GameConfig:
        PLACEHOLDERS = {
            "n": "name",
            "i": "id"
        }

        def __init__(self, game_conf, parent_conf):
            self.game_conf = game_conf
            self.parent_conf = parent_conf

        def get(self, key):
            try:
                val = self.game_conf.get(key)
            except KeyError:
                try:
                    val = self.parent_conf.get(key)
                    for _r in self.PLACEHOLDERS:
                        val = val.replace(f"%{_r}", self.game_conf.get(self.PLACEHOLDERS[_r]))
                except KeyError:
                    raise KeyError(key)
            if type(val) is str:
                if "$" in val:
                    if os.path.exists(os.path.expandvars(val)) or \
                            os.path.exists(os.path.dirname(os.path.expandvars(val))):
                        val = os.path.expandvars(val)
            return val

        def set(self, key, value):
            self.game_conf.set(key, value)

        def keys(self):
            return tuple({i for i in self.game_conf.keys() + self.parent_conf.keys()})

    def get_details(self):
        yield "Name", self.conf.get("name")
        yield "Executable Interpreter", self.conf.get("exec")
        yield "Executable Path", self.conf.get("exec_path")
        yield "Config Path", self.conf.game_conf.config_path
        yield "Running", ("Yes" if self.is_alive() else "No")
        _l_l = self.conf.get("latest_launch")
        if _l_l == -1:
            _l_l_o = "Never"
        else:
            _l_l_o = datetime.fromtimestamp(_l_l).strftime("%Y/%m/%d %H:%M") + " "
            _l_l_o += time_elapsed(datetime.now() - datetime.fromtimestamp(_l_l), "(Now)", "({})")
        yield "Last Played", _l_l_o
        yield "Time Played", time_elapsed(timedelta(seconds=self.conf.get("playtime")), "0 secs")


class Bugl:
    VERSION = 0.2

    def __init__(self, conf: Configs):
        try:
            from bugl.templates import game_defaults as _c_d
        except ModuleNotFoundError:
            from templates import game_defaults as _c_d
        self.conf = conf
        self.game_defaults = Game.GameConfig(_c_d, self.conf).game_conf
        self._games = []
        self._selected = None
        self._section = "main"

    def add_game(self, _config_path):
        self._games.append(Game(Configs(self.game_defaults, config_path=_config_path), self))

    def index(self, _g: Game):
        if type(_g) is not Game:
            raise TypeError
        return self._games.index(_g)

    def select(self, _i):
        if type(_i) is int:
            if len(self._games) > _i >= 0:
                self._selected = self._games[_i]
        if type(_i) is str:
            if _i == "next":
                if self.index(self._selected) == len(self._games)-1:
                    self._selected = self._games[0]
                else:
                    self._selected = self._games[self.index(self._selected) + 1]
            elif _i == "prev":
                if self.index(self._selected) == 0:
                    self._selected = self._games[-1]
                else:
                    self._selected = self._games[self.index(self._selected) - 1]
            elif _i == "last":
                self._selected = self._games[-1]
            elif _i == "first":
                self._selected = self._games[0]
            else:
                raise ValueError

    def write(self):
        self.conf.write()
        for _g in self._games:
            _g.conf.game_conf.write()

    def ls_games(self, win):
        pass

    def render_details(self, win: SafeWinWrapper, selected=None):
        win.addstr(0, 0, "Details")
        for _i, (_n, _p) in enumerate(self._selected.get_details()):
            win.addstr(_i*2+1, 1, f'{_n}:', curses.A_REVERSE)
            if _n == "Last Played" and len(_p) > win.getmaxyx()[1]:
                _p = _p.split(" (")[0]
            if len(_p) > win.getmaxyx()[1]:
                _p = _p[:win.getmaxyx()[1]-5]+"..."
            win.addstr(_i*2+2, 2, _p, curses.A_REVERSE if _i == selected else 0)

    def render_tooltip(self, win: SafeWinWrapper, _section):
        msg = f"BUGL {self.VERSION} - "
        msg += {
            "main": f"[{chr(8593)+chr(8595)}] to navigate, [Enter] to play, "
                    f"[Q] to exit.",
            "dialog": f"[{chr(8592)+chr(8594)}] to navigate, [Enter] to select.",
        }[_section]
        msg += (" " * (win.getmaxyx()[1] - 1 - len(msg)))
        win.win.addstr(win.getmaxyx()[0]-1, 0, msg, curses.A_REVERSE)

    class Button:
        def __init__(self, _win, y, x, txt, _ret=None):
            self._win = _win
            self.y = y
            self.x = x
            self.txt = txt
            self._ret = _ret

        def render(self, sel):
            self._win.addstr(self.y, self.x, self.txt, curses.A_REVERSE if sel else 0)

        def return_(self):
            return self._ret

    def dialog(self, win: SafeWinWrapper, title, msg, _type="alert", tooltip="dialog", butts=None, _placeholder=""):
        self.render_tooltip(win, tooltip)
        maxy, maxx = win.getmaxyx()
        d_maxy, d_maxx = int(maxy / 4), int(maxx / 2)
        diag = curses.newwin(d_maxy + 1, d_maxx + 1, int((maxy / 2) - (d_maxy / 2)), int((maxx / 2) - (d_maxx / 2)))
        diag.border()
        diag.addstr(0, int(d_maxx / 2 - len(title) / 2), title)
        if _type != "insert":
            diag.addstr(1, 1, msg)
        if _type == "alert":
            buttons = [
                self.Button(diag, d_maxy, int(d_maxx / 2 - len("Ok") / 2), "Ok")
            ]
        elif _type == "insert" or _type == "confirm":
            if butts is None:
                butts = ("Ok", "Cancel")
            buttons = [
                self.Button(diag, d_maxy, int(d_maxx / 4 - len(butts[0]) / 2), butts[0], True),
                self.Button(diag, d_maxy, int((d_maxx / 4 * 3) - len(butts[1]) / 2), butts[1], False)
            ]
        else:
            raise TypeError
        win.refresh()
        sel = 0
        ins = _placeholder
        while True:
            for _nb, _b in enumerate(buttons):
                _b.render(_nb == sel)
                diag.refresh()

            if ins:
                diag.addstr(1, 1, (d_maxx-2)*" ")
                diag.addstr(1, 1, ins)

            win.refresh()
            _inp = win.getch()
            if _type == "insert":
                if _inp == ord("\t"):
                    sel = 1 - sel
                elif _inp == curses.KEY_BACKSPACE or _inp == 127:
                    if len(ins) > 0:
                        ins = ins[:len(ins) - 1]
                elif _inp == curses.KEY_ENTER or _inp == ord("\n"):
                    diag.untouchwin()
                    if buttons[sel].return_():
                        return ins
                else:
                    try:
                        b = chr(_inp)
                        ins += b
                    except ValueError:
                        pass
            else:
                if _inp == curses.KEY_ENTER or _inp == ord("\n"):
                    diag.untouchwin()
                    return buttons[sel].return_()
                elif _inp == curses.KEY_LEFT:
                    if sel - 1 >= 0:
                        sel -= 1
                elif _inp == curses.KEY_RIGHT:
                    if sel + 1 <= len(buttons) - 1:
                        sel += 1

    def gui(self, scr: SafeWinWrapper):
        maxy, maxx = scr.getmaxyx()
        scr.timeout(500)
        curses.curs_set(False)
        p_g_select = SafeWinWrapper(curses.newpad(300, int(maxx/2)-1))
        p_g_details = SafeWinWrapper(curses.newpad(300, int(maxx/2)-1))
        self.select(0)
        scr.refresh()

        while True:
            maxy, maxx = scr.getmaxyx()
            self._selected.update_playtime()
            p_g_details.refresh_defaults(0, 0, 0, lambda l: int(scr.getmaxyx()[1]/2)+1, lambda l: scr.getmaxyx()[0]-1, lambda l: scr.getmaxyx()[1])
            p_g_select.refresh_defaults(0, 0, 0, 0, lambda l: scr.getmaxyx()[0]-1, lambda l: int(scr.getmaxyx()[1]/2))

            p_g_select.clear()
            p_g_details.clear()
            # p_g_details.border()
            # p_g_select.border()
            scr.vline(0, int(maxx/2), curses.ACS_VLINE, maxy)

            p_g_select.addstr(0, 0, "Select Game")
            for _n, _g in enumerate(self._games):
                attr = 0
                if _g == self._selected:
                    attr = curses.A_REVERSE

                p_g_select.addstr(_n+1, 2, _g.name(), attr)
            p_g_select.refresh()

            self.render_details(p_g_details)
            p_g_details.refresh()

            self.render_tooltip(scr, "main")
            inp = scr.getch()

            if inp == curses.KEY_DOWN:
                self.select("next")
            if inp == curses.KEY_UP:
                self.select("prev")
            elif inp == curses.KEY_ENTER or inp == ord("\n"):
                self._selected.run()
                """
                while True:
                    if not bugl.g().is_alive():
                        scr.addstr(1, 0, f"{bugl.g().d_name} exited.")
                        break
                    sleep(1)
                if bugl.g().poll() != 0:
                    scr.clear()
                    with open(bugl.g().conf.get("stderr")) as _e:
                        max_len = maxy - 1
                        lines = []
                        for i in _e.readlines():
                            lines.append(i)
                            if len(lines) > max_len:
                                lines.pop(0)
                    scr.addstr(0, 0, f"An error occurred while launching {bugl.g().d_name}"
                     "(showing the last {len(lines)} line/s):")
                    scr.refresh()
                    for _ln, _l in enumerate(lines):
                        scr.addstr(_ln + 1, 0, _l[:maxx])
                    scr.refresh()
                    scr.getch()
                    scr.clear()
                scr.addstr(maxy, 0, "Press any key to continue...")
                scr.refresh()
                sleep(1)
                scr.getch()
                """
            elif inp == curses.KEY_EXIT or inp == ord("q"):
                if self.dialog(scr, "Quit?", "Are you sure you want to quit?", _type="confirm"):
                    return
                else:
                    scr.clear()
                    continue
            elif inp == curses.KEY_RESIZE:
                maxy, maxx = scr.getmaxyx()
                p_g_select.resize(300, int(maxx/2))
                p_g_details.resize(300, int(maxx/2))
                while maxy < len(list(self._games))+5 or maxx < 2+2+30+30:
                    scr.clear()
                    scr.addstr(0, 0, f"Term too little (at least {len(list(self._games))+5}x{2+2+30+30})")
                    scr.refresh()
                    scr.getch()
                    maxy, maxx = scr.getmaxyx()
                scr.clear()


if __name__ == "__main__":
    try:
        from bugl.templates import bugl_defaults
    except ModuleNotFoundError:
        from templates import bugl_defaults

    if os.name == "nt":
        CONFIGS = "%USERPROFILE%/bugl/config.json"
    else:
        CONFIGS = "$HOME/.config/bugl/config.json"
    CONFIGS = os.path.expandvars(CONFIGS)

    if not os.path.exists(CONFIGS):
        if not os.path.isdir(os.path.dirname(CONFIGS)):
            os.makedirs(os.path.dirname(CONFIGS))
        g_conf = Configs(bugl_defaults)
        g_conf.write(CONFIGS)
    else:
        g_conf = Configs(bugl_defaults, config_path=CONFIGS)
    bugl = Bugl(g_conf)
    for _c in os.listdir(f"{os.path.dirname(CONFIGS)}/games/"):
        if _c.split(".")[-1] == "json":
            bugl.add_game(f"{os.path.dirname(CONFIGS)}/games/{_c}")

    try:
        curses.wrapper(lambda l: bugl.gui(SafeWinWrapper(l)))
    finally:
        bugl.write()
