import os
import json
from subprocess import Popen, PIPE, STDOUT, DEVNULL
from paramiko import SSHClient, RSAKey, AutoAddPolicy, ssh_exception
from TopongoConfigs.configs import Configs
from hashlib import sha256
from stat import S_ISDIR
from threading import Thread
from time import sleep


class Sync:
    PWD = 0
    PKEY = 1

    class AuthError(Exception):
        pass

    def __init__(self, _conf, _password_mtd=None, _full_init=False):
        self.conf = _conf
        self.ssh = SSHClient()
        self.pwd_mtd = _password_mtd
        self.ssh.set_missing_host_key_policy(AutoAddPolicy())
        self.sftp = None
        if self.conf.get("remote_path")[-1] != "/":
            self.conf.set("remote_path", self.conf.get("remote_path") + "/")
            self.conf.write()
        if self.conf.get("mode") not in (self.PKEY, self.PWD):
            raise TypeError("Invalid mode in sync config file")
        if self.conf.get("mode") == self.PKEY:
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
        if self.conf.get("mode") == self.PWD:
            if custom_pwd:
                pwd_mtd = custom_pwd
            else:
                pwd_mtd = self.pwd_mtd

            if pwd_mtd is None:
                raise ValueError("No password retrieving method supplied.")
        elif self.conf.get("mode") == self.PKEY:
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

    def prepare_path(self, path):
        created = []
        path = path.replace("~", f"/home/{self.conf.get('user')}")
        try:
            self.sftp.stat(path)
        except IOError:
            try:
                self.sftp.mkdir(path)
                created.append(path)
            except IOError:
                self.prepare_path(os.path.abspath(os.path.join(path, os.path.pardir)))
                self.sftp.mkdir(path)
                created.append(path)
        return created

    class NoHostSet(Exception):
        pass

    def connect(self, custom_pwd=None):
        # if auth method is pwd, ask for it
        if self.conf.get("mode") == self.PWD or (self.conf.get("mode") == self.PKEY and self.pkey is None):
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
                if self.conf.get("mode") == self.PKEY:
                    self.ssh.connect(self.conf.get("host"), port=self.conf.get("port"), username=self.conf.get("user"),
                                     pkey=self.pkey)
                elif self.conf.get("mode") == self.PWD:
                    self.ssh.connect(self.conf.get("host"), port=self.conf.get("port"), username=self.conf.get("user"),
                                     password=self.pwd_mtd(f"Password for {self.conf.get('user')}"))
            except ValueError as e:
                if e.args[0] == "password and salt must not be empty":
                    raise self.AuthError("Empty password")
                else:
                    raise e
            except ssh_exception.AuthenticationException:
                raise self.AuthError("Invalid password")
            self.sftp = self.ssh.open_sftp()
            self.prepare_path(self.conf.get("remote_path"))
            self.sftp.chdir(self.conf.get("remote_path").replace("~", f"/home/{self.conf.get('user')}"))

            self._update_status()

    def disconnect(self):
        self.sftp.close()
        self.ssh.close()
        self._update_status()

    def r_walk(self, path):
        files = []
        folders = []
        for f in self.sftp.listdir_attr(path):
            if S_ISDIR(f.st_mode):
                folders.append(f.filename)
            else:
                files.append(f.filename)
        yield path, folders, files
        for folder in folders:
            new_path = os.path.join(path, folder)
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

    def download_a(self, remote, local=None, diag_callback=None):
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

    def r_checksum(self, path):
        if os.path.basename(path) not in self.sftp.listdir(os.path.dirname(path)):
            raise FileNotFoundError(f"Can't find {path} on remote")
        _s = sha256()
        with self.sftp.open(path) as _f:
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
        return os.path.basename(path) in self.sftp.listdir(
            os.path.dirname(path.replace("~", f"/home/{self.conf.get('user')}")))


class RConfigs(Configs):
    LOCAL = 0
    REMOTE = 0

    def __init__(self, sync: Sync, template, config_path=None, load_from=REMOTE):
        self.sync = sync
        self.ex_loc = os.path.exists(config_path)
        self.ex_rem = sync.exists(config_path)
        if load_from == self.LOCAL:
            if self.ex_loc:
                Configs.__init__(self, template, config_path=config_path)
            else:
                raise FileNotFoundError
        elif load_from == self.REMOTE:
            if self.ex_rem:
                try:
                    d = json.load(self.sync.sftp.open(config_path))
                    Configs.__init__(self, template, data=d, config_path=config_path)
                except json.decoder.JSONDecodeError:
                    raise self.ConfigFormatErrorException
            else:
                raise FileNotFoundError
        else:
            raise TypeError("parameter load_from can only be RConfigs.LOCAL or RConfigs.REMOTE")

    def compare(self):
        if self.ex_rem and self.ex_loc:
            return False

        with open(self.config_path) as l, self.sync.sftp.open(self.config_path) as r:
            while True:
                l_b, r_b = l.read(1024), r.read(1024)
                if l_b != r_b:
                    return False
                elif l_b == b"":
                    break

    def write_remote(self):
        with self.sync.sftp.open(self.config_path, "w+") as r:
            self.write(r)

    def write_local(self):
        self.write()


class Rsync:
    class Transfer:
        def __init__(self, cmd, files, t):
            self.cmd = cmd
            self.files = files
            self.proc = None
            self.progress = 0
            self.count = -1
            self.tot_bytes = 0
            self.size = 0
            self.type = t
            self.speed = "N/A"
            self.eta = "N/A"

        def commit(self):
            self.proc = Popen(self.cmd, stdout=PIPE, stderr=STDOUT)
            stop = 10
            rem = ""
            while stop:
                data = self.proc.stdout.readline(128).decode()
                if "\r" not in data:
                    lines = [data]
                else:
                    data = rem + data
                    lines = data.split("\r")
                    rem = lines[-1]
                    lines = lines[:-1]
                for li in lines:
                    li = li.strip()
                    if li == '':
                        stop -= 1
                    if "B/s" in li:
                        tot_bytes, perc, speed, eta = li.split()
                        self.tot_bytes = int(tot_bytes.replace(",", ""))
                        self.progress = float(perc.replace("%", "")) / 100
                        self.speed = speed
                        self.eta = eta
                    elif li.strip() in self.files:
                        self.count += 1
                sleep(.5)
            print()

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
        self.pending = []
        self.running = False
        self.job = None

    def command_gen(self, dry=False):
        return ["rsync", f"-{self.switches}" + ("n" if dry else ""), "-e", f"ssh -p {self.sync.conf.get('port')}"]

    def gen_remote(self, path):
        return f"{self.sync.conf.get('user')}@{self.sync.conf.get('host')}:{path}"

    def gen_job(self, local, remote, uniq):
        """
        Returns a pair of integers, for pull and push status:
         0: OK
        -1: Error on sender
        -2: Error on receiver

        :param local:
        :param remote:
        :param uniq:
        :return:
        """
        local = os.path.expanduser(local)
        remote = os.path.join(self.gen_remote(remote).replace("~", f"/home/{self.sync.conf.get('user')}"), uniq)
        cmd_pull = lambda l: self.command_gen(dry=l) + [remote, local]
        cmd_push = lambda l: self.command_gen(dry=l) + [local, remote]
        proc_pull = Popen(cmd_pull(True), stdout=PIPE, stderr=STDOUT, stdin=DEVNULL)
        proc_push = Popen(cmd_push(True), stdout=PIPE, stderr=STDOUT, stdin=DEVNULL)
        ret = {}
        for proc, cmd, name in zip((proc_pull, proc_push), (cmd_pull, cmd_push), ("Pull", "Push")):
            output = proc.communicate()[0].decode()
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
                            ret[proc] = -1 if target == "sender" else -2
                if proc not in ret:
                    ret[proc] = -100
            else:
                files = []

                start = False
                for ln_ in output.split("\n"):
                    if start:
                        if "created directory" in ln_:
                            continue
                        if ln_ == "":
                            break
                        files.append(ln_)
                    else:
                        if "sending incremental file list" in ln_:
                            start = True

                if files:
                    self.pending.append(Rsync.Transfer(cmd(False), files, name))
                ret[proc] = 0
        return ret[proc_pull], ret[proc_push]

    def commit(self):
        def th():
            self.running = True
            while len(self.pending):
                self.job = self.pending.pop(0)
                self.job.commit()
            self.running = False

        t = Thread(target=th, daemon=True)
        t.start()
