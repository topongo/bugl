import curses
import subprocess
import os
from datetime import datetime, timedelta
from TopongoConfigs.configs import Configs
from sww import SafeWinWrapper
from sync import Sync, RConfigs


def prepare_path(_f, _c_f=False, _folder=False):
    _f = os.path.expanduser(os.path.expandvars(_f))
    if not os.path.exists(_f):
        if _folder:
            os.makedirs(_f)
        else:
            if not os.path.exists(os.path.dirname(_f)) and os.path.dirname(_f) != "":
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


class Game:
    class PollBeforeStartException(Exception):
        pass

    def __init__(self, conf: Configs, _bugl):
        self.conf = self.GameConfig(conf, _bugl.conf)
        self.bugl = _bugl
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
        for _i in ("stdout", "stderr"):
            prepare_path(self.conf.get(_i))
        self._proc = subprocess.Popen(args,
                                      stdout=open(self.conf.get("stdout", path=True), "w+"),
                                      stderr=open(self.conf.get("stderr", path=True), "w+"),
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

        def get(self, key, path=False):
            try:
                val = self.game_conf.get(key, path)
            except KeyError:
                try:
                    val = self.parent_conf.get(key, path)
                    for _r in self.PLACEHOLDERS:
                        val = val.replace(f"%{_r}", self.game_conf.get(self.PLACEHOLDERS[_r], path))
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

    def __init__(self, conf: Configs, sync_conf: Configs):
        try:
            from bugl.templates import game_defaults as _c_d
        except ModuleNotFoundError:
            from templates import game_defaults as _c_d
        self.conf = conf
        self.sync_c = sync_conf
        self.sync = None
        self.game_defaults = Game.GameConfig(_c_d, self.conf).game_conf
        self._games = []
        self._selected = None
        self._section = "main"

    def _init_sync(self, scr):
        if not self.sync:
            self.sync = Sync(self.sync_c, lambda l: self.dialog(scr, l, "Password:", "password"))

        if not self.sync.sftp:
            try:
                self.sync.connect()
            except self.DialogCancel:
                return False
            except Sync.AuthError:
                scr.erase()
                self.dialog(scr, "Connection Error",
                            "Authentication error, the password you entered is wrong or invalid.")
                return False
            except Sync.NoHostSet:
                scr.erase()
                self.dialog(scr, "Connection Error",
                            f"No host set in config file ({os.path.abspath(self.sync.conf.config_path)})")

        return True

    def render_loading(self, scr, title):
        self.dialog(scr, title, "Loading...", _type="blank")

    def add_game(self, _config_path):
        self._games.append(Game(Configs(self.game_defaults, config_path=_config_path), self))

    def index(self, _g: Game):
        if type(_g) is not Game:
            raise TypeError
        return self._games.index(_g)

    def select(self, _i):
        if not self._games:
            return
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

    def sync_conf(self, conf: Configs, callback=None, win=None):
        conf.write()

        def mtime(path):
            return datetime.fromtimestamp(os.stat(path).st_mtime), \
                   datetime.fromtimestamp(self.sync.sftp.stat(path).st_mtime)

        if self.sync.exists(conf.config_path):
            if self.sync.hash_compare(conf.config_path):
                # files on local and remote are identical
                if callback:
                    callback(1, 1)
                if win:
                    self.dialog(win, "No sync required", "Files are identical")
                return

            else:
                l_mtime, r_mtime = mtime(conf.config_path)
                if l_mtime > r_mtime:
                    # local file is newer, upload
                    self.sync.upload(conf.config_path, callback=callback)
                elif l_mtime < r_mtime:
                    self.sync.download(conf.config_path, callback=callback)
        else:
            # file not found on remote, upload
            self.sync.upload(conf.config_path, callback=callback)

    def write(self, sync=False):
        self.conf.set("signature", self.sync_c.get("signature"))
        self.conf.write()
        for _g in self._games:
            _g.conf.game_conf.write()
        if sync:
            self._sync_all()

    def _sync_all(self):
        if self.sync and self.sync.sftp:
            self.sync_conf(self.conf)
            for _g in self._games:
                self.sync_conf(_g.conf.game_conf)

    def ls_games(self, win):
        pass

    def render_details(self, win: SafeWinWrapper, selected=None):
        win.addstr(0, 0, "Details")
        if not self._selected:
            win.addstr(3, 0, "Wow, such empty", h_center=True)
            return
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
                    f"[S] to sync, [Q] to exit.",
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

    class ProgressDialog:
        def __init__(self, diag: SafeWinWrapper, msg, button):
            self._win = diag
            self.progress = 0.0
            self._msg = msg
            self.maxy, self.maxx = self._win.getmaxyx()
            self._win.addstr(1, 1, self._msg)
            self._l = self.maxx - 4
            self._slices = (1, )
            self._cur_slice = 0
            self.button = button
            self.update(0, 1)

        def set_slices(self, _s=(1, )):
            self._slices = _s

        def update_msg(self, msg):
            self._win.addstr(1, 1, " "*len(self._msg))
            self._msg = msg
            self._win.addstr(1, 1, self._msg)

        def update(self, _p, _t):
            _prog = _p / _t
            if self._cur_slice == 0:
                _s = 0
            else:
                _s = self._slices[self._cur_slice-1]
            _e = self._slices[self._cur_slice]
            _str = ("#" * int(_s*self._l))
            _str += ("#" * int(_prog*(_e-_s)*(self._l+1)))
            _str += ("-" * (self._l - len(_str)))
            self._win.addstr(self.maxy-3, 0, _str, h_center=True)
            self._win.addstr(self.maxy-2, 0, f"{round((_s+_prog*(_e-_s))*100, 2)}%", h_center=True)
            self._win.refresh()

        def next_slice(self):
            self._cur_slice += 1

        def finish(self):
            self.update(1, 1)
            self.button.render(True)
            self._win.refresh()

    class DialogCancel(Exception):
        pass

    def dialog(self, win: SafeWinWrapper, title, msg, _type="alert", tooltip="dialog", butts=None, _placeholder=None):
        self.render_tooltip(win, tooltip)
        maxy, maxx = win.getmaxyx()
        d_maxy, d_maxx = 6, int(maxx / 2)
        diag = SafeWinWrapper(curses.newwin(d_maxy + 2,
                                            d_maxx + 2,
                                            int((maxy / 2) - (d_maxy / 2)),
                                            int((maxx / 2) - (d_maxx / 2))))
        diag.border()
        diag.addstr(1, int(d_maxx / 2 - len(title) / 2) + 1, title, curses.A_REVERSE)
        if _type != "password":
            diag.addstr(2, 2, msg, _mode="wrap_word")
        if _type == "alert":
            buttons = [
                self.Button(diag, d_maxy, int(d_maxx / 2 - len("Ok") / 2), "Ok")
            ]
        elif _type == "blank":
            win.refresh()
            diag.refresh()
            return
        elif _type == "progress":
            return self.ProgressDialog(diag, msg,
                                       self.Button(diag, d_maxy, int((d_maxx / 4 * 3) - len("Ok") / 2), "Ok"))
        elif _type == "password" or _type == "confirm":
            if butts is None:
                butts = ("Ok", "Cancel")
            buttons = [
                self.Button(diag, d_maxy, int(d_maxx / 4 - len(butts[0]) / 2), butts[0], True),
                self.Button(diag, d_maxy, int((d_maxx / 4 * 3) - len(butts[1]) / 2), butts[1], False)
            ]
        else:
            diag.erase()
            raise TypeError
        win.refresh()
        if type(_placeholder) is int and _placeholder < len(buttons):
            sel = _placeholder
        else:
            sel = 0
        ins = ""
        while True:
            if _type == "password":
                diag.addstr(3, 3, " " * (d_maxx - 4), curses.A_REVERSE)
                diag.addstr(3, 3, "*" * len(ins) + "_", curses.A_REVERSE)

            for _nb, _b in enumerate(buttons):
                _b.render(_nb == sel)
                diag.refresh()

            _inp = win.getch()
            if _type == "password":
                if _inp == ord("\t") or _inp in (curses.KEY_LEFT, curses.KEY_RIGHT):
                    sel = 1 - sel
                elif _inp == curses.KEY_BACKSPACE or _inp == 127:
                    if len(ins) > 1:
                        ins = ins[:len(ins) - 1]
                    else:
                        ins = ""
                elif _inp == curses.KEY_ENTER or _inp == ord("\n"):
                    diag.untouchwin()
                    if buttons[sel].return_():
                        return ins
                    else:
                        diag.erase()
                        raise self.DialogCancel
                else:
                    if _inp in (curses.KEY_UP, curses.KEY_DOWN):
                        continue
                    try:
                        ins += chr(_inp)
                    except ValueError:
                        pass
            else:
                if _inp == curses.KEY_ENTER or _inp == ord("\n"):
                    diag.untouchwin()
                    diag.erase()
                    return buttons[sel].return_()
                elif _inp == curses.KEY_LEFT:
                    if sel - 1 >= 0:
                        sel -= 1
                elif _inp == curses.KEY_RIGHT:
                    if sel + 1 <= len(buttons) - 1:
                        sel += 1
        diag.erase()

    def gui(self, scr: SafeWinWrapper):
        maxy, maxx = scr.getmaxyx()
        scr.timeout(500)
        curses.curs_set(False)
        p_g_select = SafeWinWrapper(curses.newpad(300, int(maxx/2)-1))
        p_g_details = SafeWinWrapper(curses.newpad(300, int(maxx/2)-1))
        if not self._games:
            self.dialog(scr, f"No games found",
                        f"No games found under the game library path ({self.conf.get('games_folder')})")
        if self.sync_c.get("host") is None and not self.conf.get("ignore_missing_host"):
            if self.dialog(scr, f"No host set",
                           f"Warning: no remote host set for synchronization, set it in the sync.json file "
                           f"({os.path.abspath(self.sync_c.config_path)}).\n"
                           f"Disable this warning?",
                           "confirm", _placeholder=1, butts=("Yes", "No")):
                self.conf.set("ignore_missing_host", True)
                self.write()
        self.select(0)
        scr.erase()
        scr.refresh()
        # synced = False

        while True:
            maxy, maxx = scr.getmaxyx()
            if self._selected:
                self._selected.update_playtime()
            p_g_details.refresh_defaults(0,
                                         0,
                                         0,
                                         lambda l: int(scr.getmaxyx()[1]/2)+1,
                                         lambda l: scr.getmaxyx()[0]-1,
                                         lambda l: scr.getmaxyx()[1])
            p_g_select.refresh_defaults(0,
                                        0,
                                        0,
                                        0,
                                        lambda l: scr.getmaxyx()[0]-1,
                                        lambda l: int(scr.getmaxyx()[1]/2))

            p_g_select.erase()
            p_g_details.erase()
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

            """if not synced:
                sync_prog = self.dialog(scr, "Syncing with remote", "Connecting to remote", "progress")
                sync_prog.set_slices((1/4, 2/4, 3/4, 1))
                self.sync.connect()
                sync_prog.update(1, 1)
                for _m, _f in [
                    ("Syncing bugl configs...", lambda l: self.sync_conf(self.conf, callback=sync_prog.update)),
                    ("Syncing games configs...", lambda l: self.sync_games(callback=sync_prog.update))
                ]:
                    sync_prog.next_slice()
                    _f()
                sync_prog.finish()

                synced = True"""

            inp = scr.getch()

            if inp == curses.KEY_DOWN:
                self.select("next")
            if inp == curses.KEY_UP:
                self.select("prev")
            elif inp == curses.KEY_ENTER or inp == ord("\n"):
                if self._selected:
                    self._selected.run()
                """
                while True:
                    if not bugl.g().is_alive():
                        scr.addstr(1, 0, f"{bugl.g().d_name} exited.")
                        break
                    sleep(1)
                if bugl.g().poll() != 0:
                    scr.erase()
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
                    scr.erase()
                scr.addstr(maxy, 0, "Press any key to continue...")
                scr.refresh()
                sleep(1)
                scr.getch()
                """
            elif inp == curses.KEY_EXIT or inp == ord("q"):
                if self.dialog(scr, "Quit", "Are you sure you want to quit?", "confirm"):
                    if not self.sync or not self.sync.sftp:
                        if self.dialog(scr, "Quit", "Connect to remote and sync before exiting?", "confirm",
                                       _placeholder=1, butts=("Yes", "No")):
                            self.render_loading(scr, "Connecting")
                            if self._init_sync(scr):
                                self.render_loading(scr, "Syncing")
                                self.write(sync=True)
                    return
                else:
                    scr.erase()
                    continue
            elif inp == curses.KEY_EXIT or inp == ord("s"):
                self.render_loading(scr, "Connecting")
                if self._init_sync(scr):
                    self.render_loading(scr, "Syncing")
                    if self._selected:
                        self.sync_conf(self._selected.conf.game_conf, win=scr)
                    else:
                        self._sync_all()
                    curses.napms(1000)
                scr.erase()
            elif inp == curses.KEY_RESIZE:
                maxy, maxx = scr.getmaxyx()
                p_g_select.resize(300, int(maxx/2))
                p_g_details.resize(300, int(maxx/2))
                while maxy < len(list(self._games))+5 or maxx < 2+2+30+30:
                    scr.erase()
                    scr.addstr(0, 0, f"Term too little (at least {len(list(self._games))+5}x{2+2+30+30})")
                    scr.refresh()
                    scr.getch()
                    maxy, maxx = scr.getmaxyx()
                scr.erase()


if __name__ == "__main__":
    try:
        from bugl.templates import bugl_defaults
        from bugl.templates import sync_defaults
    except ModuleNotFoundError:
        from templates import bugl_defaults
        from templates import sync_defaults

    if os.name == "nt":
        CONFIGS = "~/Documents/bugl/"
    else:
        CONFIGS = "~/.config/bugl/"
    CONFIGS = os.path.expanduser(CONFIGS)

    prepare_path(CONFIGS)
    os.chdir(CONFIGS)
    if not os.path.exists(CONFIGS+"config.json"):
        prepare_path("config.json")
        g_conf = Configs(bugl_defaults, config_path="config.json", write=True)
        g_conf.write("config.json")
    else:
        g_conf = Configs(bugl_defaults, config_path="config.json")

    if not os.path.exists("sync.json"):
        prepare_path("sync.json")
        s_conf = Configs(sync_defaults, config_path="sync.json", write=True)
        s_conf.write("sync.json")
    else:
        s_conf = Configs(sync_defaults, config_path="sync.json")

    if g_conf.get("signature") == "":
        g_conf.set("signature", s_conf.get("signature"))
    bugl = Bugl(g_conf, s_conf)
    prepare_path(bugl.conf.get("games_folder"), _folder=True)
    for _c in os.listdir("games"):
        if _c.split(".")[-1] == "json":
            bugl.add_game(f"games/{_c}")

    try:
        curses.wrapper(lambda l: bugl.gui(SafeWinWrapper(l)))
    finally:
        bugl.write()
