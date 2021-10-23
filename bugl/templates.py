from sync import Sync

bugl_defaults = {
    "stdout": "~/.log/bugl/%i/out.log",
    "stderr": "~/.log/bugl/%i/err.log",
    "games_folder": "~/.config/bugl/games/",
    "ignore_missing_host": False,
    "signature": ""
}

game_defaults = {
    "id": "",
    "name": "",
    "exec": "",
    "exec_path": "",
    "exec_args": [],
    "latest_launch": -1.0,
    "playtime": 0.0,
    "signature": ""
}

game_shared = (
    "latest_launch",
    "playtime"
)

sync_defaults = {
    "user": None,
    "host": None,
    "port": 22,
    "private_key_path": None,
    "mode": Sync.PWD,
    "remote_path": "~/.config/bugl/",
    "signature": ""
}
