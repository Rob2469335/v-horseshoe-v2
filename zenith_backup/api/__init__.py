"""
api/__init__.py - API Package
"""
from .server import start_server


def run_server(port: int = 8000):
    start_server(port)
