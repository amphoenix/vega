"""
Shared fixtures for phoenix-trade tests.

Tests are designed to run WITHOUT a running server or real market data.
All external calls (IndMoney API, broker, WebSocket) are mocked.
"""
import os, sys

# Ensure the backend package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
