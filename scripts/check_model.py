"""Verify an actual, schema-valid model turn without database or application data."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError
from shared.config import Settings
from shared.model import ModelError, model_adapter
from shared.model_providers import provider_info


def check(settings):
    if settings.model_adapter == "demo":
        return {"ok": True, "real_model": False, "provider": "Демо без ИИ"}
    reply, metadata = model_adapter(settings).step(
        json.dumps(
            {
                "request": "Проверка подключения. Верни kind=final, answer=Подключение работает, intent=text. calls, charts, exports и controls — пустые массивы, table=false. Не используй инструменты и не запрашивай данные.",
                "context": {},
                "observations": [],
            },
            ensure_ascii=False,
        )
    )
    if reply.kind != "final" or reply.calls or not reply.answer.strip():
        raise ModelError("Соединение установлено, но модель не выполнила проверку JSON-протокола агента.")
    return {
        "ok": True,
        "real_model": True,
        **provider_info(settings),
        "model": settings.model,
        "model_ms": metadata["model_ms"],
    }


def main():
    try:
        result = check(Settings())
    except (ModelError, ValidationError):
        # Validation errors may include configuration values; only ModelError is safe to show.
        error = sys.exc_info()[1]
        print(
            str(error)
            if isinstance(error, ModelError)
            else "Некорректные настройки модели. Запустите мастер подключения.",
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
