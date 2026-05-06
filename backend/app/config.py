"""
Configuration management
Loads all settings from the project root .env file
"""

import os
from dotenv import load_dotenv

# Load .env from project root (relative path from backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    # Fall back to environment variables (production)
    load_dotenv(override=True)


class Config:
    """Flask configuration"""

    # Flask
    SECRET_KEY = os.environ.get('SECRET_KEY', 'phoenixtrade-secret-key')
    DEBUG = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'

    # Disable ASCII escaping in JSON responses so non-ASCII characters render correctly
    JSON_AS_ASCII = False

    # LLM — two modes:
    #   'bedrock' → uses litellm + AWS credentials directly (no proxy)
    #   anything else → OpenAI-compatible API (api_key + base_url + model)
    LLM_PROVIDER    = os.environ.get('LLM_PROVIDER', 'openai').lower()
    LLM_API_KEY     = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL    = os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1')
    LLM_MODEL_NAME  = os.environ.get('LLM_MODEL_NAME', 'gpt-4o-mini')

    # AWS Bedrock (used when LLM_PROVIDER=bedrock)
    AWS_ACCESS_KEY_ID     = os.environ.get('AWS_ACCESS_KEY_ID')
    AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
    AWS_REGION            = os.environ.get('AWS_REGION', 'us-east-1')
    BEDROCK_MODEL_ID      = os.environ.get('BEDROCK_MODEL_ID', 'us.anthropic.claude-sonnet-4-6-20251001-v1:0')

    # Zep
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY')

    # File uploads
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}

    # Text processing
    DEFAULT_CHUNK_SIZE = 500
    DEFAULT_CHUNK_OVERLAP = 50

    # OASIS simulation configuration
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')

    # Available actions per platform
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]

    # Report agent configuration
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))

    @classmethod
    def validate(cls):
        errors = []
        if cls.LLM_PROVIDER == 'bedrock':
            if not cls.AWS_ACCESS_KEY_ID or not cls.AWS_SECRET_ACCESS_KEY:
                errors.append("LLM_PROVIDER=bedrock requires AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY")
        else:
            if not cls.LLM_API_KEY:
                errors.append("LLM_API_KEY is not configured")
        return errors

