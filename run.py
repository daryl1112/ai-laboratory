"""Entry point: python run.py"""
import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    uvicorn.run(
        "server.main:app",
        host=os.environ.get("AILAB_HOST", "127.0.0.1"),
        port=int(os.environ.get("AILAB_PORT", "8080")),
        reload=False,
    )
