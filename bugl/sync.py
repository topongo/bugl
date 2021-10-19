import os
import json
from paramiko import SSHClient, RSAKey, AutoAddPolicy, ssh_exception
from TopongoConfigs.configs import Configs
from hashlib import sha256


class Sync:
    PWD = 0
    PKEY = 1

    class AuthError(Exception):
        pass

    def __init__(self, _conf, _password_mtd=None):
        self.conf = _conf
        if self.conf.get("signature") == "":
            sig = sha256()
            sig.update(os.urandom(4096))
            self.conf.set("signature", sig.hexdigest())
            self.conf.write()
        self.ssh = SSHClient()
        self.pwd_mtd = _password_mtd
        self.ssh.set_missing_host_key_policy(AutoAddPolicy())
        self.sftp = None
        if self.conf.get("remote_path")[-1] != "/":
            self.conf.set("remote_path", self.conf.get("remote_path")+"/")
            self.conf.write()
        if self.conf.get("mode") not in (self.PKEY, self.PWD):
            raise TypeError("Invalid mode in sync config file")
        if self.conf.get("mode") == self.PKEY:
            try:
                self.pkey = RSAKey.from_private_key(open(self.conf.get("private_key_path", path=True)))
            except ssh_exception.PasswordRequiredException:
                try:
                    self.pkey = RSAKey.from_private_key(open(self.conf.get("private_key_path", path=True)),
                                                        password=self.pwd_mtd())
                except ssh_exception.SSHException as _e:
                    raise self.AuthError("Invalid private key password")
                if self.pwd_mtd is None:
                    raise self.AuthError("No method supplied for password retrieving")

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

    def _authenticate(self, custom_pwd):
        if custom_pwd:
            pwd_mtd = custom_pwd
        else:
            pwd_mtd = self.pwd_mtd

        if pwd_mtd is None:
            raise ValueError("No password retrieving method supplied.")

    def prepare_path(self, path):
        path = path.replace("~", f"/home/{self.conf.get('user')}")
        print(path)
        try:
            self.sftp.stat(path)
        except IOError:
            try:
                self.sftp.mkdir(path)
            except IOError:
                self.prepare_path(os.path.abspath(os.path.join(path, os.path.pardir)))
                self.sftp.mkdir(path)

    def connect(self, custom_pwd=None):
        # if auth method is pwd, ask for it
        if self.conf.get("mode") == self.PWD:
            self._authenticate(custom_pwd)

        # if previously connected, check if connection is still alive
        if self.connected:
            self._update_status()

        # if still connected exit
        if self.connected:
            return
        else:
            if self.conf.get("mode") == self.PKEY:
                self.ssh.connect(self.conf.get("host"), port=self.conf.get("port"), username=self.conf.get("user"),
                                 pkey=self.pkey)
            elif self.conf.get("mode") == self.PWD:
                self.ssh.connect(self.conf.get("host"), port=self.conf.get("port"), username=self.conf.get("user"),
                                 password=self.pwd_mtd())
            self.sftp = self.ssh.open_sftp()
            self.prepare_path(self.conf.get("remote_path"))
            self.sftp.chdir(self.conf.get("remote_path").replace("~", f"/home/{self.conf.get('user')}"))

            self._update_status()

    def disconnect(self):
        self.sftp.close()
        self.ssh.close()
        self._update_status()

    def r_checksum(self, path):
        if path not in self.sftp.listdir():
            raise FileNotFoundError(f"Can't find {path} on remote")
        _s = sha256()
        with self.sftp.open(path) as _f:
            while True:
                _b = _f.read(1024)
                if _b == b"":
                    break
                _s.update(_b)
        return _s.hexdigest()

    def hash_compare(self, l_path, r_path=False, abs=False):
        _s = sha256()
        with open(l_path, "rb") as _f:
            while True:
                _b = _f.read(1024)
                if _b == b"":
                    break
                _s.update(_b)
        return self.r_checksum(l_path if not r_path else r_path) == _s.hexdigest()

    def exists(self, path):
        return os.path.basename(path) in self.sftp.listdir(os.path.dirname(path))


class RConfigs(Configs):
    def __init__(self, parent, template, config_path=None):
        self.parent = parent
        if self.parent.exists(config_path):
            d = json.load(self.parent.sftp.open(config_path))
        else:
            raise FileNotFoundError
        Configs.__init__(self, template, data=d, config_path=config_path)
