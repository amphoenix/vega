"""
PhoenixTrade Backend - Flask application factory
"""

import os
import warnings

# Suppress multiprocessing resource_tracker warnings from third-party libraries (e.g. transformers)
# Must be set before any other imports
warnings.filterwarnings("ignore", message=".*resource_tracker.*")

from flask import Flask, request
from flask_cors import CORS

from .config import Config
from .utils.logger import setup_logger, get_logger


def create_app(config_class=Config):
    """Flask application factory"""
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Flask >= 2.3 uses app.json.ensure_ascii; older versions use JSON_AS_ASCII config
    if hasattr(app, 'json') and hasattr(app.json, 'ensure_ascii'):
        app.json.ensure_ascii = False

    logger = setup_logger('phoenixtrade')

    # Only log startup in the reloader subprocess to avoid printing twice in debug mode
    is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
    debug_mode = app.config.get('DEBUG', False)
    should_log_startup = not debug_mode or is_reloader_process

    if should_log_startup:
        logger.info("=" * 50)
        logger.info("PhoenixTrade Backend starting...")
        logger.info("=" * 50)

    # Enable CORS
    CORS(app, resources={r"/api/*": {"origins": "*"}})

    # Register simulation process cleanup to terminate all sim processes on shutdown
    from .services.simulation_runner import SimulationRunner
    SimulationRunner.register_cleanup()
    if should_log_startup:
        logger.info("Simulation process cleanup registered")

    # Start background position monitor (watches SL/targets, auto-exits positions)
    from .services.position_monitor import start as _start_monitor
    _start_monitor()
    if should_log_startup:
        logger.info("Position monitor started")

    # Auto-start the F&O scanner — runs continuously for as long as the
    # backend is up. User has explicitly asked for "always on" behaviour;
    # the manual start/stop toggle is no longer in the UI.
    try:
        from .services.fo_scanner import start as _start_fo_scanner
        _start_fo_scanner()
        if should_log_startup:
            logger.info("F&O scanner auto-started")
    except Exception as _e:
        logger.warning(f"F&O scanner auto-start failed: {_e}")

    # Request logging middleware
    @app.before_request
    def log_request():
        logger = get_logger('phoenixtrade.request')
        logger.debug(f"Request: {request.method} {request.path}")
        if request.content_type and 'json' in request.content_type:
            logger.debug(f"Body: {request.get_json(silent=True)}")

    @app.after_request
    def log_response(response):
        logger = get_logger('phoenixtrade.request')
        logger.debug(f"Response: {response.status_code}")
        return response

    # Register blueprints
    from .api import graph_bp, simulation_bp, report_bp, market_bp, trade_bp, indmoney_bp
    app.register_blueprint(graph_bp,      url_prefix='/api/graph')
    app.register_blueprint(simulation_bp, url_prefix='/api/simulation')
    app.register_blueprint(report_bp,     url_prefix='/api/report')
    app.register_blueprint(market_bp,     url_prefix='/api/market')
    app.register_blueprint(trade_bp,      url_prefix='/api/trade')
    app.register_blueprint(indmoney_bp,   url_prefix='/api/indmoney')

    # Health check
    @app.route('/health')
    def health():
        return {'status': 'ok', 'service': 'PhoenixTrade Backend'}

    # Auto-start the tracked-position watcher for any positions already pinned
    # before this server boot. Without this, alerts would only kick in after
    # the user adds/removes a position post-restart.
    try:
        from .services import tracked_monitor as _tm
        _tm.sync()
    except Exception as _e:
        logger.warning(f"tracked_monitor.sync() at boot failed: {_e}")

    if should_log_startup:
        logger.info("PhoenixTrade Backend started")

    return app

