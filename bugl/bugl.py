#!/usr/bin/env python
import curses
import subprocess
import os
from datetime import datetime, timedelta
from TopongoConfigs.configs import Configs
from sww import SafeWinWrapper
from sync import Sync, RConfigs, Rsync, Job, REMOTE, LOCAL
from uuid import uuid4
from itertools import chain
from threading import Thread
from collections import deque


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
        self.args = None
        self.rsync = None
        self.syncing = False
        prepare_path(self.conf.get("stdout"))
        prepare_path(self.conf.get("stderr"))
        # if some of its datapaths contains a numeric or empty key assign an uuid to the datapath
        deltas = {}
        for k, i in self.conf.get("data").items():
            try:
                int(k)
            except ValueError:
                if k != "":
                    continue
            deltas[k] = str(uuid4())
        if deltas:
            tmp = self.conf.get("data")
            for old, new in deltas.items():
                tmp[new] = tmp.pop(old)
            self.conf.set("data", tmp)

    def run(self):
        self.args = [self.conf.get("exec"), self.conf.get("exec_path", path=True)] + self.conf.get("exec_args")
        self.conf.set("latest_launch", datetime.now().timestamp())
        self.conf.game_conf.write()
        self._session_started = True
        for _i in ("stdout", "stderr"):
            prepare_path(self.conf.get(_i))
        if self.conf.get("exec_in_path"):
            cwd = {"cwd": os.path.dirname(self.conf.get("exec_path"))}
        else:
            cwd = {}
        self._proc = subprocess.Popen(self.args,
                                      stdout=open(self.conf.get("stdout", path=True), "w+"),
                                      stderr=open(self.conf.get("stderr", path=True), "w+"),
                                      stdin=subprocess.DEVNULL, **cwd)

    def name(self):
        return self.conf.get("name") + (" (Running)" if self.is_alive() else "")

    def is_alive(self):
        return self._proc is not None and self._proc.poll() is None

    def poll(self):
        if self._proc is None:
            raise self.PollBeforeStartException
        else:
            return self._proc.poll()

    def tick(self):
        if self._session_started:
            t_e = datetime.now() - datetime.fromtimestamp(self.conf.get("latest_launch"))
            self.conf.set("playtime", self._init_playtime + t_e.total_seconds())
            self.conf.game_conf.write()
            if not self.is_alive():
                self._session_started = False
                self._init_playtime = self.conf.get("playtime")
                # if process exited with a non-zero code return true
                if self.poll() != 0:
                    return True

    def wait(self):
        self._proc.wait()

    def kill(self):
        self._proc.kill()

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

    def sync_data(self):
        if not self.rsync.running:
            self.rsync.run()

    def sync_status(self):
        if self.rsync.running:
            return self.rsync.job.progress_()
        else:
            return "Not running"


class JobRunner:
    def __init__(self, *jobs: Job):
        self.bar_p = 0
        self.jobs = []
        self.dump = []
        self.thread = None
        self.current = None
        self.messages = deque()

        for j in jobs:
            if not isinstance(j, Job):
                raise TypeError
            self.jobs.append(j)

    def msg_clb(self, **kwargs):
        self.messages.append(kwargs)

    def fetch_messages(self):
        while len(self.messages) > 0:
            yield self.messages.popleft()

    def run_all(self):
        # support for dynamic adding to list
        # if using a normal if, the list would change size while iterating
        while True:
            t = None
            for j in self.jobs:
                if not j.done and not j.running:
                    t = j
            self.current = t
            if self.current is None:
                break
            self.current.running = True
            self.current.run(self.msg_clb)
            self.current.running = False
            self.current.done = True
        self.current = None
        if self.thread:
            self.thread = None

    def speed(self):
        if self.current:
            return self.current.speed
        else:
            return "N/A"

    def progress(self, include_dump=False):
        if not include_dump and len(self.jobs) == 0:
            return 0, 0, 0
        else:
            displayed = [j for j in ((self.jobs + self.dump) if include_dump else self.jobs) if j.display]
            tot_p = sum([j.progress() for j in displayed])
            return tot_p / len(displayed), len([i for i in displayed if i.done]), len(displayed)

    def add_job(self, job: Job):
        if isinstance(job, Job):
            self.jobs.append(job)
        else:
            raise TypeError(job)

    def dump_jobs(self):
        self.dump += self.jobs
        self.jobs = []

    def has_runnable_jobs(self):
        return len(self.jobs) > 0 and not all(map(lambda l: l.running, self.jobs))

    def completed(self):
        return len(self.jobs) > 0 and all(map(lambda l: l.done, self.jobs))

    def running(self):
        return self.thread is not None

    def run_threaded(self):
        if not self.running() and self.jobs:
            self.thread = Thread(target=self.run_all)
            self.thread.start()

    def bar(self):
        self.bar_p += 1
        return "-\\|/"[self.bar_p % 4]


class Bugl:
    VERSION = "0.3.2"

    def __init__(self, conf: Configs, sync_conf: Configs):
        try:
            from bugl.templates import game_defaults as _c_d
        except ModuleNotFoundError:
            from templates import game_defaults as _c_d
        self.conf = conf
        self.sync_c = sync_conf
        self.sync = None
        self.rsync = None
        self.game_defaults = Game.GameConfig(_c_d, self.conf).game_conf
        self._games = []
        self._jobs = JobRunner()
        self._selected = None
        self._section = "main"
        self._progress = True

    def _init_sync(self, scr, override_mode=None):
        if not self.sync:
            self.sync = Sync(self.sync_c, lambda l: self.dialog(scr, l, "Password:", "password"))

        if override_mode is not None:
            self.sync.override_mode(override_mode)

        if not self.sync.sftp:
            try:
                self.sync.connect()
            except self.DialogCancel:
                return
            except Sync.AuthError:
                scr.erase()
                if self.sync.conf.get("mode") == Sync.PKEY:
                    if self.dialog(scr, "Connection Error",
                                   "Authentication error: private key authentication failed, "
                                   "try with password method?", "confirm"):
                        self._init_sync(scr, override_mode=Sync.PWD)
                else:
                    self.dialog(scr, "Connection Error",
                                "Authentication error: the password you entered is wrong or invalid.")
                return
            except Sync.NoHostSet:
                scr.erase()
                self.dialog(scr, "Connection Error",
                            f"No host set in config file ({os.path.abspath(self.sync.conf.config_path)})")
                return
            except Sync.ConnectionError as _e:
                scr.erase()
                self.dialog(scr, "Connection Error", _e.args[0])
                return

        return True

    def gen_rsync(self, scr):
        self.sync.prepare_path(self.sync.conf.get("remote_data_path"))
        return Rsync(self.sync)

    def render_loading(self, scr, title, msg="Loading..."):
        self.dialog(scr, title, msg, _type="blank")

    def add_game(self, _config_path):
        try:
            self._games.append(Game(Configs(self.game_defaults, config_path=_config_path), self))
            return _config_path, None
        except Configs.ConfigFormatErrorException as e:
            # raise e
            return _config_path, e

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
            elif _i == "last_played":
                if self._games:
                    self._selected = max(self._games, key=lambda l: l.conf.get("latest_launch"))
            else:
                raise ValueError

    def check_for_sync(self, win):
        # TODO: generalize this abomination along Bugl.sync_conf
        for conf in chain((self.conf, self.sync_c), map(lambda l: l.conf.game_conf, self._games)):
            try:
                rconf = RConfigs(self.sync, conf.template, conf.config_path)
                if rconf.newer(conf):
                    if conf.get("__to_sync__"):
                        self.dialog(win, "Sync Conf",
                                    f"Fatal error: there's a conflict in config file {conf.config_path}, remote is "
                                    f"newer, but local has never been uploaded.")
                        return -1
                    rconf.set("__to_sync__", False)
                    rconf.write_all()
                    conf.read()
                else:
                    rconf = RConfigs(self.sync, conf.template, conf.config_path, load_from=LOCAL)
                    rconf.set("__to_sync__", False)
                    rconf.write_all()
                    conf.read()
            except FileNotFoundError:
                rconf = RConfigs(self.sync, conf.template, conf.config_path, load_from=LOCAL)
                rconf.write_remote()
                self.sync_conf(conf)
            except Configs.MissingPropertyException:
                if self.dialog(win, "Sync Conf",
                               f"Remote config with path {conf.config_path} misses a mandatory internal property, "
                               f"probably it's outdated, upload local file to remote?", "confirm"):
                    self.render_loading(win, "Sync Conf", "Uploading conf...")
                    self.sync_conf(conf, force_push=True)
                else:
                    return -1
            except Configs.ConfigFormatErrorException:
                if self.dialog(win, "Sync Conf",
                               f"Remote config with path {conf.config_path} is corrupted, upload the local one?",
                               "confirm"):
                    self.render_loading(win, "Sync Conf", "Uploading conf...")
                    self.sync_conf(conf, force_push=True)
                else:
                    return -2

    def sync_conf(self, conf: Configs, force_pull=False, force_push=False, msg_clb=None) -> None:
        conf.write()

        if force_pull and force_push:
            raise TypeError("You can only force one action")

        if force_pull:
            r_conf = RConfigs(self.sync, conf.template, conf.config_path)
            r_conf.set("__to_sync__", False)
            r_conf.write_local()
            conf.read()
        if force_push:
            r_conf = RConfigs(self.sync, conf.template, conf.config_path, load_from=LOCAL)
            r_conf.set("__to_sync__", False)
            r_conf.write_all()
            conf.read()

        try:
            r_conf = RConfigs(self.sync, conf.template, conf.config_path)
            if r_conf.newer(conf):
                r_conf.set("__to_sync__", False)
                r_conf.write_all()
                conf.read()
            else:
                r_conf = RConfigs(self.sync, conf.template, conf.config_path, load_from=LOCAL)
                r_conf.set("__to_sync__", False)
                r_conf.write_remote()
        except (Configs.MissingPropertyException, FileNotFoundError):
            r_conf = RConfigs(self.sync, conf.template, conf.config_path, load_from=LOCAL)
            r_conf.set("__to_sync__", False)
            r_conf.write_all()
        msg_clb(title="Test", msg="OK")

    def sync_data(self, g: Game, win, operation=Rsync.PULL):
        if self._init_sync(win):
            rem = f"{self.sync.conf.get('remote_data_path')}{g.conf.get('id')}/"
            if win:
                self.render_loading(win, "Starting transaction")
            g.rsync = self.gen_rsync(win)
            for uniq, loc in g.conf.get("data").items():
                loc = os.path.expanduser(loc)
                if not os.path.exists(loc):
                    self.dialog(win, "Sync Data", f"warning: path {loc} does not exists on disk.")
                else:
                    if os.path.isdir(loc) and not os.path.islink(loc) and loc[-1] != "/":
                        loc += "/"
                j = g.rsync.gen_job(loc, rem, uniq, operation=operation)
                if isinstance(j, int):
                    if j == 0:
                        return
                    if j == -1:
                        if operation == Rsync.PULL:
                            if not self.dialog(win, "Sync Data",
                                               f"Data folder corresponding to \n{loc}\ndoesn't exists on remote,"
                                               f" upload it?", "confirm"):
                                return
                            else:
                                self.sync_data(g, win, Rsync.PUSH)
                        else:
                            if self.dialog(win, "Sync Data",
                                           "warning: data on local doesn't exist. Download id?", "confirm"):
                                self.sync_data(g, win, Rsync.PULL)
                            else:
                                return

                    self.dialog(win, "Sync Data",
                                f"Error: rsync exited with code {j} during data sync.")
                    return

                self._jobs.add_job(j)

            self._jobs.run_threaded()
            if not self._jobs.running():
                self.dialog(win, "Sync Data", f"No data to be synced.")

        else:
            self.dialog(win, "Sync Data", "Can't sync data without connection with remote.")

    def write(self, sync=False):
        self.conf.set("__to_sync__", True)
        self.conf.write()
        for _g in self._games:
            _g.conf.set("__to_sync__", True)
            _g.conf.game_conf.write()
        if sync:
            return self._sync_all()

    def _sync_all(self):
        if self.sync and self.sync.sftp:
            self._jobs.add_job(Job(self.conf.config_path, 1, actual_job=self.sync_conf, actual_job_args=(self.conf,)))
            self._jobs.add_job(Job(self.sync_c.config_path, 1, actual_job=self.sync_conf,
                                   actual_job_args=(self.sync_c,)))
            for _g in self._games:
                self._jobs.add_job(Job(_g.conf.game_conf.config_path, 1, actual_job=self.sync_conf,
                                       actual_job_args=(_g.conf.game_conf, )))
            self._jobs.run_threaded()

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
                    f"[S] to sync, "
                    f"{'[Shift+K] to kill selected game, ' if self._selected and self._selected.is_alive() else ''}"
                    f"[Q] to exit.",
            "dialog": f"[{chr(8592)+chr(8594)}] to navigate, [Enter] to select.",
        }[_section]
        msg += (" " * (win.getmaxyx()[1] - 1 - len(msg)))
        win.addstr(win.getmaxyx()[0]-1, 0, msg, curses.A_REVERSE)

    def render_progress(self, win: SafeWinWrapper, sticky=False):
        self._jobs.run_threaded()
        if self._jobs.running():
            gran, done, tot = self._jobs.progress()
            msg = f"[{self._jobs.bar()}] Operations are in progress: {done:2d}/{tot:2d} | " \
                  f"Overall: {gran*100:5.1f}% | Speed: {self._jobs.speed()}"

            fill = int(win.getmaxyx()[1] * gran)
            msg += (" " * (win.getmaxyx()[1] - len(msg)))
            win.addstr(win.getmaxyx()[0] - 2, 0, msg[:fill], _attr=curses.A_REVERSE)
            if fill < win.getmaxyx()[0]:
                win.addstr(win.getmaxyx()[0] - 2, fill, msg[fill:])
        else:
            if self._jobs.completed() or sticky:
                self._jobs.dump_jobs()
                gran, done, tot = self._jobs.progress(include_dump=True)
                msg = f"[✔️] Operations completed. {done:2d}/{tot:2d}"
                msg += (" " * (win.getmaxyx()[1] - len(msg)))
                win.addstr(win.getmaxyx()[0] - 2, 0, msg, _attr=curses.A_BLINK | curses.A_REVERSE)
                return True

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
            self._win.addstr(self.maxy-2, 0, f"{round((_s+_prog*(_e-_s))*100, 3)}%", h_center=True)
            self._win.refresh()

        def next_slice(self):
            self._cur_slice += 1

        def finish(self):
            self.update(1, 1)
            self.button.render(True)
            self._win.refresh()

    class DialogCancel(Exception):
        pass

    def dialog(self, win: SafeWinWrapper, title, msg, _type="alert", tooltip="dialog", butts=None, _placeholder=None,
               fullscreen=False):
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
            if not fullscreen:
                self.render_tooltip(win, tooltip)
                self.render_progress(win)
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

    def gui(self, scr: SafeWinWrapper, faulty_confs):
        def panic(reason):
            scr.erase()
            self.dialog(scr, "!BUGL PANIC!",
                        f"Cannot continue in the current state. Please restart bugl.\nReason: {reason}",
                        fullscreen=True)

        # generate gui layout
        maxy, maxx = scr.getmaxyx()
        scr.timeout(500)
        curses.curs_set(False)
        p_g_select = SafeWinWrapper(curses.newpad(300, int(maxx/2)-1))
        p_g_details = SafeWinWrapper(curses.newpad(300, int(maxx/2)-1))

        # setup variables
        o_maxy, o_maxx = -1, -1
        o_progress = self._progress
        show_completed = False
        request_refresh = False

        # warn if host has not set
        if self.sync_c.get("host") is None and not self.conf.get("ignore_missing_host"):
            if self.dialog(scr, f"No host set",
                           f"Warning: no remote host set for synchronization, set it in the sync.json file "
                           f"({os.path.abspath(self.sync_c.config_path)}).\n"
                           f"Disable this warning?",
                           "confirm", _placeholder=1, butts=("Yes", "No")):
                self.conf.set("ignore_missing_host", True)
                self.write()

        # check for remote updates
        self.render_loading(scr, "BUGL Startup", "Connecting to remote...")
        if self._init_sync(scr):
            self.render_loading(scr, "BUGL Startup", "Synchronizing with remote...")
            if self.check_for_sync(scr) == -1:
                panic("Could not remain synced to remote.")
                return 1
        else:
            if not self.dialog(scr, "Offline", "Cannot connect to remote, continue in offline mode?", "confirm"):
                return 0

        # check for faulty configs
        for path_, ex_ in faulty_confs.items():
            if self.dialog(scr, f"Config loading error",
                           f"The config at path {path_} have an error:\nDownload this file from remote?",
                           "confirm", butts=("Yes", "No")):
                self.render_loading(scr, "Connecting")
                if self._init_sync(scr):
                    try:
                        rconf = RConfigs(self.sync, self.game_defaults, path_)
                        rconf.write_local()
                        self.add_game(path_)
                    except Configs.ConfigFormatErrorException:
                        self.dialog(scr, "Download Failed", f"Even the remote file contains errors.\n"
                                                            f"Please manually check {path_}.")

        # if no games
        if not self._games:
            if self.dialog(scr, f"Game Loader",
                           f"No games found under the game library path ({self.conf.get('games_folder')}).\n"
                           f"Download configs from remote now?", "confirm", butts=("Yes", "No")):
                self.render_loading(scr, "Connecting")
                if self._init_sync(scr):
                    _s = self.sync.download_a("games")
                    self.dialog(scr, "Game Loader", "Downloaded successful for all the games found on remote.")
                    if _s:
                        self.dialog(scr, "Game Loader", f"Synchronized {len(_s)} games, a restart is needed.")
                        return -1
                    else:
                        self.dialog(scr, "Game Loader", f"No games found on remote.")

        self.select("last_played")
        scr.erase()
        scr.refresh()

        while True:
            maxy, maxx = scr.getmaxyx()
            if self._selected:
                if self._selected.tick():
                    self.dialog(scr, f'{self._selected.conf.get("name")} errored.',
                                f'{self._selected.conf.get("name")} exited with code {self._selected.poll()}.\n'
                                f'Error log:\n{" ".join(self._selected.args)}')

            for m in self._jobs.fetch_messages():
                self.dialog(scr, **m)

            if o_maxx != maxx or o_maxy != maxy or request_refresh:
                if request_refresh:
                    request_refresh = False
                p_g_details.refresh_defaults(
                    0,
                    0,
                    0,
                    lambda l: int(maxx/2)+1,
                    lambda l: maxy-2-(1 if self._progress else 0),
                    lambda l: maxx
                )
                p_g_select.refresh_defaults(
                    0,
                    0,
                    0,
                    0,
                    lambda l: maxy-2-(1 if self._progress else 0),
                    lambda l: int(maxx/2)
                )

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
            show_completed = self.render_progress(scr, sticky=show_completed)

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
                if show_completed:
                    show_completed = False
                    request_refresh = True
                self.select("next")
            if inp == curses.KEY_UP:
                if show_completed:
                    show_completed = False
                    request_refresh = True
                self.select("prev")
            elif inp == curses.KEY_ENTER or inp == ord("\n"):
                if show_completed:
                    show_completed = False
                if self._selected:
                    if self._selected.is_alive():
                        self.dialog(scr, "Already running", "The selected game is already running, close it before "
                                                            "starting it again. If it's not responding press "
                                                            "Shift+K to kill it.")
                    else:
                        self._selected.run()
            elif inp == curses.KEY_EXIT or inp == ord("q"):
                if self.dialog(scr, "Quit", "Are you sure you want to quit?", "confirm"):
                    if not self.sync or not self.sync.sftp:
                        if self.dialog(scr, "Quit", "Connect to remote and sync before exiting?", "confirm",
                                       _placeholder=1, butts=("Yes", "No")):
                            self.render_loading(scr, "Connecting")
                            if self._init_sync(scr):
                                self.render_loading(scr, "Syncing")
                                self.write(sync=True)
                    return 0
                else:
                    scr.erase()
                    continue
            elif inp == ord("s"):
                self.render_loading(scr, "Connecting")
                if self._init_sync(scr):
                    self.render_loading(scr, "Syncing")
                    self._sync_all()

                scr.erase()
            elif inp == ord("K"):
                if self._selected.is_alive():
                    if self.dialog(scr, "Kill game?", "Are you sure of killing the game? Note that every game "
                                                      "progress not yet saved will be likely lost.",
                                   "confirm"):
                        self._selected.kill()
            elif inp == ord("t"):
                # put tests here
                self.render_loading(scr, "Generating rsync object")
                self.sync_data(self._selected, scr)
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


def prepare():
    try:
        from bugl.templates import bugl_defaults
        from bugl.templates import sync_defaults
    except ModuleNotFoundError:
        from templates import bugl_defaults
        from templates import sync_defaults

    if os.name == "nt":
        conf = "~/Documents/bugl/"
    else:
        conf = "~/.config/bugl/"
    conf = os.path.expanduser(conf)

    prepare_path(conf)
    os.chdir(conf)
    if not os.path.exists(conf + "config.json"):
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

    _b = Bugl(g_conf, s_conf)
    prepare_path("games", _folder=True)
    _errs = {}
    for _c in os.listdir("games"):
        if _c.split(".")[-1] == "json":
            _path, _ex = _b.add_game(f"games/{_c}")
            _errs[_path] = _ex
    return _b, {_p: _e for _p, _e in _errs.items() if _e}


if __name__ == "__main__":
    while True:
        bugl, errs = prepare()
        try:
            r = curses.wrapper(lambda l: bugl.gui(SafeWinWrapper(l), errs))
            if r != -1:
                bugl.write()
                exit()
        except KeyboardInterrupt:
            bugl.write()
            exit()
