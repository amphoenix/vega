"""
API route modules
"""

from flask import Blueprint

market_bp     = Blueprint('market',     __name__)
trade_bp      = Blueprint('trade',      __name__)
indmoney_bp   = Blueprint('indmoney',   __name__)

from . import market      # noqa: E402, F401
from . import trade       # noqa: E402, F401
from . import indmoney    # noqa: E402, F401

