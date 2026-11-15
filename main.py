"""Main ASGI entrypoint for Meta AI Chatbot application.

Provides direct access to the FastAPI ASGI application instance and CLI launcher.
"""

from app.main import app, create_app

__all__ = ["app", "create_app"]

if __name__ == "__main__":
    import uvicorn
    from app.core.config import settings

    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=False,
    )
