"""Run the web server."""

import os

import uvicorn


def main():
    port = int(os.getenv("PORT", "8080"))
    reload = os.getenv("RAILWAY_ENVIRONMENT") is None
    uvicorn.run(
        "src.web.app:app",
        host="0.0.0.0",
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    main()