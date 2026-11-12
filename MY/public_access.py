import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def public_base(settings):
    if settings.public_base_url:
        return settings.public_base_url.rstrip("/")
    try:
        data = json.loads(Path(settings.public_address_file).read_text())
        for key in ["server_pid", "tunnel_pid"]:
            os.kill(int(data[key]), 0)
        url = data["url"].rstrip("/")
        if urlsplit(url).scheme == "https":
            return url
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return ""
