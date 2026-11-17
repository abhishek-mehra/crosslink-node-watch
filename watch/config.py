"""Settings, read from one TOML file. See config.example.toml for what each one means."""
import os
import tomllib

DEFAULTS = {
    "node_url": "http://127.0.0.1:8232",
    "cookie_path": "~/.cache/zebra/.cookie",
    "mining_address": "",
    "results_log": "",
    "state_dir": "state",
    "node_state_dir": "",
    "growth_factor": 2.0,
    "growth_window_hours": 6,
    "known_good": {},
}


def load(path):
    try:
        with open(path, "rb") as f:
            cfg = {**DEFAULTS, **tomllib.load(f)}
    except FileNotFoundError:
        raise SystemExit(f"{path} not found: copy config.example.toml to config.toml and edit it")
    for key in ("cookie_path", "results_log", "state_dir", "node_state_dir"):
        cfg[key] = os.path.expanduser(cfg[key])
    return cfg
