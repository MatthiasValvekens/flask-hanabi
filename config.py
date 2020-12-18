import os
from dataclasses import MISSING


def get_env_setting(setting, default=MISSING):
    """ Get the environment setting or raise exception """

    try:
        env = os.environ[setting]
    except KeyError:
        if default is MISSING:
            error_msg = "Set the %s env variable" % setting
            raise ValueError(error_msg)
        env = default

    if isinstance(env, str):
        env = env.strip('\" ')  # strip spaces and quotes
    return env


SQLALCHEMY_DATABASE_URI = get_env_setting(
    'SQLALCHEMY_DATABASE_URI', 'postgresql://hanabi@localhost:5432/hanabi'
)
SQLALCHEMY_TRACK_MODIFICATIONS = False
BABEL_DEFAULT_LOCALE = 'nl'
BABEL_SUPPORTED_LOCALES = ['nl', 'en']
DEFAULT_COUNTDOWN_SECONDS = 15
ERRORS_ALLOWED = 3
TOKEN_COUNT = 8
COLOUR_COUNT = 5
POST_ACTION_TIME_LIMIT_SECONDS = 10
API_BASE_URL = get_env_setting('API_BASE_URL', '')
