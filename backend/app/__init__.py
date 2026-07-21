__all__ = ["app"]


def __getattr__(name: str):
    if name != "app":
        raise AttributeError(name)
    from backend.app.main import app

    return app
