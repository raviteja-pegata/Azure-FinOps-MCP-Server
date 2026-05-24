import os

def pytest_configure(config):
    """Set required env vars before any module imports so config.py loads cleanly."""
    os.environ.setdefault("AZURE_ALLOWED_SUBSCRIPTIONS", "test-sub-1,test-sub-2")
    os.environ.setdefault("AZURE_DEFAULT_SUBSCRIPTION", "test-sub-1")
