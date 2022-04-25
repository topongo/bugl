from sync import Sync

bugl_defaults = {
    "stdout": "~/.log/bugl/%i/out.log",
    "stderr": "~/.log/bugl/%i/err.log",
    "games_folder": "~/.config/bugl/games/",
    "ignore_missing_host": False
}

game_defaults = {
    "id": "",
    "name": "",
    "exec": "",
    "exec_path": "",
    "exec_in_path": False,
    "exec_args": [],
    "latest_launch": -1.0,
    "playtime": 0.0,
    "data": {}
}

game_shared = (
    "latest_launch",
    "playtime"
)

sync_defaults = {
    "user": "",
    "host": "",
    "port": 22,
    "private_key_path": "",
    "mode": Sync.PWD,
    "remote_path": "~/.config/bugl/",
    "remote_data_path": "~/data/bugl/data/"
}
