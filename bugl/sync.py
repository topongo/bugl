import os
import json
import socket
from subprocess import Popen, PIPE, STDOUT, DEVNULL
from paramiko import SSHClient, RSAKey, AutoAddPolicy, ssh_exception
from TopongoConfigs.configs import Configs
from hashlib import sha256
from stat import S_ISDIR
from threading import Thread
from time import sleep
from select import select
from sys import stderr
from typing import Callable

LOCAL = 0
REMOTE = 1


class Sync:
    PWD = 0
    PKEY = 1

    class AuthError(Exception):
        pass

    class NoHostSet(Exception):
        pass

    class ConnectionError(Exception):
        pass

    def __init__(self, _conf, _password_mtd=None, _full_init=False):
        self.conf = _conf
        self.ssh = SSHClient()
        self.pwd_mtd = _password_mtd
        self.home = None
        self.ssh.set_missing_host_key_policy(AutoAddPolicy())
        self.sftp = None
        if self.conf.get("remote_path")[-1] != "/":
            self.conf.set("remote_path", self.conf.get("remote_path") + "/")
            self.conf.write()
        if self.conf.get("mode") not in (self.PKEY, self.PWD):
            raise TypeError("Invalid mode in sync config file")
        else:
            self.mode = self.conf.get("mode")
        if self.mode == self.PKEY:
            if _full_init:
                self._authenticate()
            else:
                self.pkey = None

        self.connected = False

    def _update_status(self):
        try:
            self.sftp.listdir()
        except OSError:
            self.sftp.close()
            try:
                self.ssh.exec_command(f'ls {self.conf.get("remote_path")}', timeout=1)
                self.sftp = self.ssh.open_sftp()
                self.connected = True
            except ssh_exception.SSHException:
                self.ssh.close()
                self.connected = False

    def ready(self):
        self._update_status()
        return self.connected

    def _authenticate(self, custom_pwd=None):
        if self.mode == self.PWD:
            if custom_pwd:
                pwd_mtd = custom_pwd
            else:
                pwd_mtd = self.pwd_mtd

            if pwd_mtd is None:
                raise ValueError("No password retrieving method supplied.")
        elif self.mode == self.PKEY:
            try:
                self.pkey = RSAKey.from_private_key(open(self.conf.get("private_key_path", path=True)))
            except ssh_exception.PasswordRequiredException:
                try:
                    self.pkey = RSAKey.from_private_key(open(self.conf.get("private_key_path", path=True)),
                                                        password=self.pwd_mtd("Unlock Private Key: "))
                except ssh_exception.SSHException as _e:
                    raise self.AuthError("Invalid private key password")
                if self.pwd_mtd is None:
                    raise self.AuthError("No method supplied for password retrieving")

    def override_mode(self, new_mode):
        if new_mode in (Sync.PWD, Sync.PKEY):
            self.mode = new_mode
        else:
            raise TypeError

    def expanduser(self, _path):
        if self.home is None:
            _, stdout, _ = self.ssh.exec_command("echo -n ~")
            self.home = stdout.read().decode()
        return self.home

    def prepare_path(self, path_):
        created = []
        path_ = path_.replace("~", f"/home/{self.conf.get('user')}")
        try:
            self.sftp.stat(path_)
        except IOError:
            try:
                self.sftp.mkdir(path_)
                created.append(path_)
            except IOError:
                self.prepare_path(os.path.abspath(os.path.join(path_, os.path.pardir)))
                self.sftp.mkdir(path_)
                created.append(path_)
        return created

    def connect(self, custom_pwd=None):
        # if auth method is pwd, ask for it
        if self.mode == self.PWD or (self.mode == self.PKEY and self.pkey is None):
            self._authenticate(custom_pwd)

        if self.conf.get("host") is None:
            # no host set
            raise self.NoHostSet

        # if previously connected, check if connection is still alive
        if self.connected:
            self._update_status()

        # if still connected exit
        if self.connected:
            return
        else:
            try:
                if self.mode == self.PKEY:
                    self.ssh.connect(self.conf.get("host"), port=self.conf.get("port"), username=self.conf.get("user"),
                                     pkey=self.pkey)
                elif self.mode == self.PWD:
                    self.ssh.connect(self.conf.get("host"), port=self.conf.get("port"), username=self.conf.get("user"),
                                     password=self.pwd_mtd(f"Password for {self.conf.get('user')}"))
            except ValueError as e:
                if e.args[0] == "password and salt must not be empty":
                    raise self.AuthError("Empty password")
                else:
                    raise e
            except ssh_exception.AuthenticationException:
                raise self.AuthError("Invalid password")
            except (ssh_exception.SSHException, socket.gaierror) as e_:
                raise self.ConnectionError(e_)
            self.sftp = self.ssh.open_sftp()
            self.prepare_path(self.conf.get("remote_path"))
            self.sftp.chdir(self.conf.get("remote_path").replace("~", f"/home/{self.conf.get('user')}"))

            self._update_status()

    def disconnect(self):
        self.sftp.close()
        self.ssh.close()
        self._update_status()

    def r_walk(self, path_):
        files = []
        folders = []
        for f in self.sftp.listdir_attr(path_):
            if S_ISDIR(f.st_mode):
                folders.append(f.filename)
            else:
                files.append(f.filename)
        yield path_, folders, files
        for folder in folders:
            new_path = path_.join(path_, folder)
            for _i in self.r_walk(new_path):
                yield _i

    def upload(self, local, remote=None, callback=None):
        if self.sftp is None:
            self.connect()
        if remote is None:
            remote = local

        if callback:
            self.sftp.put(local, remote, callback=callback)
        else:
            self.sftp.put(local, remote)
        return [remote]

    def download(self, remote, local=None, callback=None):
        if self.sftp is None:
            self.connect()
        if local is None:
            local = remote

        if callback:
            self.sftp.get(local, remote, callback=callback)
        else:
            self.sftp.get(local, remote)
        return [local]

    def download_a(self, remote, local=None):
        # TODO: add full support for progress dialog objects
        created = []
        if local is None:
            local = remote
        for _p, _d, _f in self.r_walk(remote):
            if not os.path.exists(_p):
                created += self.prepare_path(_p)
            else:
                if not os.path.isdir(_p):
                    raise FileExistsError(f"{_p} exists locally")
            for _ff in _f:
                if _ff not in os.listdir(_p):
                    created += self.download(os.path.join(_p, _ff), os.path.join(_p.replace(remote, local), _ff))
            for _dd in _d:
                if not os.path.exists(os.path.join(_p, _dd)):
                    os.mkdir(os.path.join(_p, _dd))
                    created.append(os.path.join(_p, _dd))
                else:
                    if not os.path.isdir(os.path.join(_p, _dd)):
                        raise FileExistsError(f"{os.path.join(_p, _dd)} exists locally")
        return created

    def r_checksum(self, path_):
        if os.path.basename(path_) not in self.sftp.listdir(os.path.dirname(path_)):
            raise FileNotFoundError(f"Can't find {path_} on remote")
        _s = sha256()
        with self.sftp.open(path_) as _f:
            while True:
                _b = _f.read(1024)
                if _b == b"":
                    break
                _s.update(_b)
        return _s.hexdigest()

    def hash_compare(self, l_path, r_path=False):
        _s = sha256()
        with open(l_path, "rb") as _f:
            while True:
                _b = _f.read(1024)
                if _b == b"":
                    break
                _s.update(_b)
        return self.r_checksum(l_path if not r_path else r_path) == _s.hexdigest()

    def exists(self, path):
        try:
            return os.path.basename(path) in self.sftp.listdir(
                os.path.dirname(path.replace("~", f"/home/{self.conf.get('user')}")))
        except FileNotFoundError:
            return False


class RConfigs(Configs):
    def __init__(self, sync: Sync, template: dict, config_path=None, load_from=REMOTE, raise_for_update_time=True):
        self.sync = sync
        self.ex_loc = os.path.exists(config_path)
        self.ex_rem = sync.exists(config_path)
        self.loaded_from = load_from
        if load_from == LOCAL:
            if self.ex_loc:
                Configs.__init__(self, template, config_path=config_path, raise_for_update_time=raise_for_update_time)
            else:
                raise FileNotFoundError
        elif load_from == REMOTE:
            if self.ex_rem:
                try:
                    d = json.load(self.sync.sftp.open(config_path))
                    Configs.__init__(self, template, data=d, config_path=config_path,
                                     raise_for_update_time=raise_for_update_time)
                except json.decoder.JSONDecodeError:
                    raise self.ConfigFormatErrorException
            else:
                raise FileNotFoundError
        else:
            raise TypeError("parameter load_from can only be RConfigs.LOCAL or RConfigs.REMOTE")

    @staticmethod
    def from_conf(sync: Sync, conf: Configs, load_from=REMOTE, raise_for_update_time=True):
        return RConfigs(sync, conf.template, conf.config_path, load_from, raise_for_update_time)

    def compare(self):
        if self.ex_rem and self.ex_loc:
            return False

        with open(self.config_path) as l_, self.sync.sftp.open(self.config_path) as r:
            while True:
                l_b, r_b = l_.read(1024), r.read(1024)
                if l_b != r_b:
                    return False
                elif l_b == b"":
                    break

    def write_remote(self):
        with self.sync.sftp.open(self.config_path, "w+") as r:
            Configs.write(self, r)

    def write_local(self):
        Configs.write(self)

    def write(self, _buffer=None, _indent=True):
        if self.loaded_from == LOCAL:
            self.write_local()
        elif self.loaded_from == REMOTE:
            self.write_remote()

    def write_all(self):
        self.write_local()
        self.write_remote()

    def newer(self, other: Configs):
        return self.get("__update_time__") > other.get("__update_time__")


class Job:
    def __init__(self, files, tot_bytes, actual_job=None, actual_job_args=(), msg_clb=None):
        self.files = files
        self.tot_bytes = tot_bytes
        self.speed = "N/A"
        self.eta = "N/A"
        self.speed = "N/A"
        self.running = False
        self.done = False
        self.count = 0
        self.bytes = 0
        self.progress_ = 0
        self.display = True
        self.proc = None
        if actual_job is not None and not isinstance(actual_job, Callable):
            raise TypeError(actual_job)
        self.actual_job = actual_job
        self.actual_job_args = actual_job_args

    def run(self, msg_clb):
        if self.actual_job:
            self.actual_job(*self.actual_job_args, msg_clb=(msg_clb if msg_clb else lambda l: None))
        self.progress_ = 1

    def standalone_run(self, msg_clb=None):
        self.proc = Thread(target=self.run, args=(msg_clb, ) if msg_clb else (lambda l: None, ))
        self.proc.start()

    def is_alive(self):
        return self.proc is not None and self.proc.poll() is None

    def progress(self):
        return self.progress_


class Rsync:
    PULL = 0
    PUSH = 1

    class Transfer(Job):
        def __init__(self, cmd, files, t, tot_bytes):
            super().__init__(files, tot_bytes)
            self.cmd = cmd
            self.proc = None
            self.count = -1
            self.type = t
            self.speed = "0B/s"

        def run(self):
            self.proc = Popen(self.cmd, stdout=PIPE, stderr=STDOUT, bufsize=1000)
            sleep(2)

            def split_read(proc):
                buff = b""
                stop = False
                count = 0
                while not stop:
                    if proc.poll() is not None:
                        rd, _ = proc.communicate()
                        for j in rd.splitlines(True):
                            yield j
                        break
                    rd, _, _ = select([proc.stdout], [], [], .1)
                    if rd:
                        ch = rd[0].read(1)
                        if ch == b"":
                            count += 1
                        else:
                            count = 0
                        buff += ch
                        if ch == b"\r" or ch == b"\n":
                            yield buff
                            buff = b""
                    else:
                        count += 1
                    if count > 1000:
                        count = 0
                        stop = True
                yield buff

            for line in split_read(self.proc):
                line = line.decode()
                if "B/s" in line:
                    if ":" not in line:
                        bytes_, perc, speed = line.split()
                        eta = "0:00:00"
                    else:
                        bytes_, perc, speed, eta = line.split()
                    self.bytes = int(bytes_.replace(",", ""))
                    self.progress_ = float(perc.replace("%", "")) / 100.0
                    self.speed = speed
                    self.eta = eta
                elif line.strip() in self.files:
                    self.count += 1
            self.bytes = self.tot_bytes
            self.speed = "0B/s"
            self.eta = "Finished"
            self.progress_ = 1
            self.count = len(self.files)

        def progress(self):
            return 1.0 * self.bytes / self.tot_bytes

    class GenericError(Exception):
        pass

    def __init__(self, sync: Sync, switches="PrlptgEovu", s_extra="", s_exclude=""):
        self.sync = sync
        # P: --partial and --progress
        # h: human-readable
        # r: recursive
        # l: links
        # p: preserve permissions
        # t: preserve times
        # g: preserve groups
        # o: preserve owner
        # E: preserve execution
        # v: verbose
        # u: update: keep newer files
        self.switches = switches + s_extra
        for i in s_exclude:
            self.switches.replace(i, "")
        self.proc = None

    def command_gen(self, dry=False):
        return ["rsync", f"-{self.switches}" + ("n" if dry else ""), "-e", f"ssh -p {self.sync.conf.get('port')}"] + \
               ([] if not dry else ["--stats"])

    def gen_remote(self, path):
        path = path.replace("~", f"/home/{self.sync.conf.get('user')}")
        if self.sync.exists(path):
            attr = self.sync.sftp.lstat(path)
            if S_ISDIR(attr.st_mode) and path[-1] != "/":
                path += "/"
        return f"{self.sync.conf.get('user')}@{self.sync.conf.get('host')}:{path}"

    def gen_job(self, local, remote, uniq, operation=0):
        """
        Returns a pair of integers, for pull and push status:
         0: OK
        -1: Error on sender
        -2: Error on receiver

        :param local:
        :param remote:
        :param uniq:
        :param operation:
        :return:
        """
        local = os.path.expanduser(local)
        remote = self.gen_remote(os.path.join(remote, uniq))

        def cmd(l_):
            if operation == Rsync.PULL:
                return self.command_gen(dry=l_) + [remote, local]
            elif operation == Rsync.PUSH:
                return self.command_gen(dry=l_) + [local, remote]

        proc = Popen(cmd(True), stdout=PIPE, stderr=STDOUT, stdin=DEVNULL)
        output = proc.communicate()[0].decode()
        ret = 0
        if proc.poll():
            for li in output.split("\n"):
                if "failed" in li:
                    if "[Receiver]" in li:
                        target = "receiver"
                    elif "[sender]" in li:
                        target = "sender"
                    else:
                        target = "unknown"
                    if "No such file or directory" in output:
                        ret = -1 if target == "sender" else -2
        else:
            files = []

            start = False
            it = output.split("\n")
            for ln_ in it:
                if start:
                    if "created directory" in ln_:
                        continue
                    if ln_ == "":
                        break
                    files.append(ln_)
                else:
                    if " incremental file list" in ln_:
                        start = True

            tot_bytes = 0
            for ln_ in it:
                if "Total transferred file size:" in ln_:
                    tot_bytes = int(
                        ln_.split("Total transferred file size: ")[-1].split(" bytes")[0].replace(",", "")
                    )

            if files:
                return Rsync.Transfer(cmd(False), files, {0: "Pull", 1: "Push"}[operation], tot_bytes)
            else:
                return ret
