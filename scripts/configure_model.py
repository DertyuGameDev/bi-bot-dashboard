"""Terminal setup, usable with plain Python in Docker before dependencies are installed."""

import argparse
import getpass
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

# Keep the first-run wizard independent of Pydantic/Python packages on the host.
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("model_presets", ROOT / "shared/model_providers.py")
presets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(presets)
PROVIDERS = presets.PROVIDERS


def write_settings(path, values):
    """Replace only model fields, atomically; never print credentials or source contents."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Файл настроек не должен быть символической ссылкой")
    lines = path.read_text().splitlines() if path.exists() else []
    for key, value in values.items():
        if not re.fullmatch(r"(?:BI|RPA)_[A-Z_]+", key) or any(c in value for c in "\r\n\0"):
            raise ValueError("Некорректное поле настройки")
    lines = [
        line
        for line in lines
        if not any(re.match(rf"\s*(?:export\s+)?{re.escape(k)}\s*=", line) for k in values)
    ]
    for key, value in values.items():
        # Both Compose and python-dotenv accept literal single-quoted values.
        quoted = value if key.startswith("BI_") else value.replace("\\", "\\\\")
        quoted = quoted.replace("'", "\\'")
        lines.append(f"{key}='{quoted}'")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".model-config-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            out.write("\n".join(lines) + "\n")
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if Path(temp).exists():
            Path(temp).unlink()


def ask(label, default="", *, required=False, read=input):
    while True:
        answer = read(label + (f" [{default}]" if default else "") + ": ").strip() or default
        if answer or not required:
            return answer
        print("Укажите значение.")


def configure(path, docker=False, *, read=input, secret=getpass.getpass):
    choices = [key for key in PROVIDERS if key != "demo"]
    print("Подключение модели. Доступ и расходы принадлежат владельцу установки.")
    for i, key in enumerate(choices, 1):
        print(f"{i}. {PROVIDERS[key]['label']}")
    while True:
        chosen = ask("Сервис (номер или имя)", "codex", read=read)
        adapter = (
            choices[int(chosen) - 1] if chosen.isdigit() and 1 <= int(chosen) <= len(choices) else chosen
        )
        if adapter in choices:
            break
        print("Выберите сервис из списка.")
    preset = PROVIDERS[adapter]
    model = ask(
        "ID модели в вашем сервисе", "gpt-6.1-terra" if adapter == "codex" else "", required=True, read=read
    )
    prefix = "BI_" if docker else "RPA_"
    values = {
        prefix + "MODEL_ADAPTER": adapter,
        prefix + "MODEL": model,
        prefix + "MODEL_API_KEY": "",
        prefix + "MODEL_API_HEADERS": "{}",
        prefix + "MODEL_BASE_URL": "",
        prefix + "MODEL_API_FORMAT": "auto",
        prefix + "MODEL_API_REASONING": "",
    }
    if adapter == "codex":
        values[prefix + "REASONING"] = ask("Reasoning (none/low/medium/high/xhigh/max)", "medium", read=read)
        if values[prefix + "REASONING"] not in {"none", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError("Неизвестный reasoning")
    else:
        default_url = preset["url"]
        if docker and adapter in {"ollama", "lmstudio"}:
            default_url = default_url.replace("127.0.0.1", "host.docker.internal")
        while True:
            base = ask(
                "Base URL (до /responses, /chat/completions или /messages)",
                default_url,
                required=True,
                read=read,
            ).rstrip("/")
            u = urlsplit(base)
            if (
                u.scheme in {"http", "https"}
                and u.hostname
                and not (u.username or u.password or u.query or u.fragment)
            ):
                break
            print("Нужен HTTP(S)-адрес без встроенного пароля и query.")
        values[prefix + "MODEL_BASE_URL"] = base
        values[prefix + "MODEL_API_KEY"] = secret("API-ключ (ввод скрыт; Enter — без ключа): ").strip()
        if preset["key"] and not values[prefix + "MODEL_API_KEY"]:
            print("Ключ обязателен для облачного сервиса; можно передать его специальным заголовком ниже.")
        if adapter != "anthropic":
            values[prefix + "MODEL_API_FORMAT"] = ask(
                "Формат ответа (auto/json_schema/json_object/prompt)", "auto", read=read
            )
            if values[prefix + "MODEL_API_FORMAT"] not in {"auto", "json_schema", "json_object", "prompt"}:
                raise ValueError("Неизвестный формат ответа")
        if ask("Нужны дополнительные заголовки (например api-key)? да/нет", "нет", read=read).lower() in {
            "да",
            "yes",
            "y",
        }:
            headers = secret("Заголовки JSON, ввод скрыт: ")
            parsed = json.loads(headers)
            if not isinstance(parsed, dict) or any(
                not isinstance(k, str) or not isinstance(v, str) or any(c in k + v for c in "\r\n")
                for k, v in parsed.items()
            ):
                raise ValueError("Заголовки должны быть JSON-объектом строк")
            values[prefix + "MODEL_API_HEADERS"] = json.dumps(parsed, ensure_ascii=False)
    write_settings(path, values)
    print("Настройки сохранены приватно (0600). Ключи не выводятся и не передаются в браузер.")
    print(
        "Проверка: ./scripts/docker_start.sh"
        if docker
        else "Проверка: .venv/bin/python scripts/check_model.py"
    )
    return values


def main():
    parser = argparse.ArgumentParser(description="Настройка провайдера ИИ для BI-бота")
    parser.add_argument("--docker", action="store_true")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    try:
        configure(args.env_file or ROOT / (".env.docker" if args.docker else ".env"), args.docker)
    except (ValueError, EOFError, KeyboardInterrupt):
        raise SystemExit("Настройка отменена или параметры некорректны. Прежний файл не изменён.") from None


if __name__ == "__main__":
    main()
