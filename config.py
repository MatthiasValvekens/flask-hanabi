import os
import secrets
from binascii import unhexlify
from dataclasses import MISSING


class EnvMissingError(ValueError):
    pass


def get_env_setting(setting, default=MISSING):
    """ Get the environment setting or raise exception """

    try:
        env = os.environ[setting]
    except KeyError:
        if default is MISSING:
            error_msg = "Set the %s env variable" % setting
            raise EnvMissingError(error_msg)
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
# consider a session stale after x minutes of inactivity
SESSION_STALE_MINUTES = 60
POST_ACTION_TIME_LIMIT_SECONDS = 30
POST_ACTION_MINIMAL_TIME_SECONDS = 10
API_BASE_URL = get_env_setting('API_BASE_URL', '')

try:
    SECRET_KEY = unhexlify(get_env_setting('SECRET_KEY'))
    # if SECRET_KEY is present, assume prod mode
    TEMPLATES_AUTO_RELOAD = False
except EnvMissingError:
    SECRET_KEY = secrets.token_bytes(32)
    TEMPLATES_AUTO_RELOAD = True
