"""
API route modules
"""

from flask import Blueprint

graph_bp      = Blueprint('graph',      __name__)
simulation_bp = Blueprint('simulation', __name__)
report_bp     = Blueprint('report',     __name__)
market_bp     = Blueprint('market',     __name__)
trade_bp      = Blueprint('trade',      __name__)
indmoney_bp   = Blueprint('indmoney',   __name__)

from . import graph       # noqa: E402, F401
from . import simulation  # noqa: E402, F401
from . import report      # noqa: E402, F401
from . import market      # noqa: E402, F401
from . import trade       # noqa: E402, F401
from . import indmoney    # noqa: E402, F401

