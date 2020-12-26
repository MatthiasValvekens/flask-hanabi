Summary
=======

Fairly simple online clone of the famous card game [Hanabi](https://en.wikipedia.org/wiki/Hanabi_(card_game)). It runs in the player's respective browsers as a single-page application that talks to a Flask server over HTTP.

The UI doesn't explain the rules of the game, but if you're familiar with the game, it should be mostly self-explanatory.


Deployment
==========

I wrote this for my friends and family, so while you're free to deploy the software on your own server, I'll be brief.

* The tech stack is simple. You'll need a web server (I use `nginx`), `uwsgi` with Python 3.7+ support and a PostgreSQL database server to persist state. In particular, there are no JS dependencies.
* Clone the repository and install the Python dependencies in `requirements.txt` in a virtualenv.
* Put some environment variables into a file (say `config.env`). At the very least, you'll want to set `SQLALCHEMY_DATABASE_URI` (the URI to access the PostgreSQL database, incl. credentials), `API_BASE_URL` (the URL relative to which all API endpoints will be registered) and `SECRET_KEY` (key used to sign HMAC tokens).

For `SECRET_KEY`, any sufficiently random string will do. Best practice dictates that the environment file should be readable only to the user running `uwsgi`.

Here's an example `uwsgi` vassal config file.

```ini
[uwsgi]
chdir = /srv/http/hanabi
module = hanabi:app
home = /srv/http/hanabi/venv
master = true
need-app = true
need-plugin = python3
plugins = logfile
processes = 10
socket = /srv/http/hanabi/socket/hanabi.sock
req-logger = file:/srv/http/hanabi/logs/uwsgi-req.log
logger = file:/srv/http/hanabi/logs/uwsgi-err.log
for-readline = /srv/http/hanabi/config.env
  env = %(_)
endfor =
```

The nginx config isn't anything special either; see [here](https://flask.palletsprojects.com/en/1.1.x/deploying/uwsgi/) for example. You may want to set the default content type to `application/json`.

License
=======

The source is licensed under the GNU GPL (v3), see [COPYING](COPYING).