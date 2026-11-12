import uvicorn
from shared.config import Settings

s = Settings()
uvicorn.run("MY.app:app", host=s.host, port=s.port)
