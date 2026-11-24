import os
import json
from typing import Dict, Any, List

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG: Dict[str, Any] = {
    "drive_root": r"G:\Mon Drive\___UPSSITECH",
    "moodle_url": "https://www.stri.fr/eformation",
    "gemini_api_key": "",
    "student_names": []
}


def load_config() -> Dict[str, Any]:
    """Charge la configuration locale depuis config.json (avec valeurs par défaut)."""
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
                if isinstance(user_cfg, dict):
                    cfg.update({k: v for k, v in user_cfg.items() if v is not None})
        except Exception:
            pass
    return cfg


def get_drive_root() -> str:
    return os.environ.get("COURS_DRIVE_ROOT") or load_config().get("drive_root", DEFAULT_CONFIG["drive_root"])


def get_moodle_url() -> str:
    return (os.environ.get("MOODLE_URL") or load_config().get("moodle_url", DEFAULT_CONFIG["moodle_url"])).rstrip("/")


def get_student_names() -> List[str]:
    names = load_config().get("student_names", [])
    return [n.strip() for n in names if isinstance(n, str) and n.strip()]
