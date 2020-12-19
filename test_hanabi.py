import json
from collections import namedtuple

import flask
import pytest
import hanabi


@pytest.fixture
def client():
    hanabi.app.config['TESTING'] = True
    hanabi.app.config['TESTING_SEED'] = 5
    hanabi.app.config['SERVER_NAME'] = 'localhost.localdomain'

    with hanabi.app.test_client() as client:
        with hanabi.app.app_context():
            hanabi.init_db()
        yield client


SessionData = namedtuple(
    'SessionData',
    ['session_id', 'pepper', 'mgmt_token', 'session_token',
     'manage_url', 'join_url']
)

GameContext = namedtuple(
    'GameContext', ['session', 'player_token', 'player_id', 'name', 'play_url']
)


def request_json(client, method, url, *args, data, headers=None, **kwargs):
    if method not in ('get', 'post', 'put', 'delete', 'patch'):
        raise ValueError("That's probably not what you meant")

    req_headers = {'content-type': 'application/json'}

    if headers is not None:
        req_headers.update(headers)

    req = getattr(client, method)
    return req(url, *args, data=json.dumps(data), headers=req_headers, **kwargs)


def create_session(client) -> SessionData:
    with hanabi.app.app_context():
        spawn_url = flask.url_for('spawn_session')
    response = request_json(client, 'post', spawn_url, data={})
    rdata = response.get_json()
    assert response.status_code == 201, rdata
    session_id = rdata['session_id']
    pepper = rdata['pepper']
    mgmt_token = rdata['session_mgmt_token']
    session_token = rdata['session_token']
    with hanabi.app.app_context():
        manage_url = flask.url_for(
            'manage_session', session_id=session_id, pepper=pepper,
            mgmt_token=mgmt_token
        )
        join_url = flask.url_for(
            'session_join', session_id=session_id, pepper=pepper,
            inv_token=session_token
        )
    return SessionData(
        session_id=session_id, pepper=pepper, session_token=session_token,
        mgmt_token=mgmt_token, manage_url=manage_url, join_url=join_url,
    )


def create_player_in_session(client, sess: SessionData = None, name='tester') \
        -> GameContext:
    if sess is None:
        sess = create_session(client)
    response = request_json(client, 'post', sess.join_url, data={'name': name})
    rdata = response.get_json()
    assert response.status_code == 201, rdata
    assert rdata['name'] == name
    player_id, player_token = rdata['player_id'], rdata['player_token']
    with hanabi.app.app_context():
        play_url = flask.url_for(
            'play', session_id=sess.session_id, pepper=sess.pepper,
            player_id=player_id, player_token=player_token
        )
    return GameContext(
        session=sess, player_id=player_id, player_token=player_token,
        name=name, play_url=play_url
    )


def test_create_destroy_session(client):
    sess = create_session(client)
    exists_q = hanabi.HanabiSession.query.filter(
        hanabi.HanabiSession.id == sess.session_id
    ).exists()
    with hanabi.app.app_context():
        assert hanabi.db.session.query(exists_q).scalar()
    response = client.get(sess.manage_url)
    rdata = response.get_json()
    assert rdata['players'] == [], rdata['players']
    assert rdata['status'] == hanabi.Status.INITIAL

    # we shouldn't be able to start a game without first adding players
    response = client.post(sess.manage_url)
    assert response.status_code == 409, response.get_json()

    response = client.delete(sess.manage_url)
    assert response.status_code == 200, response.get_json()
    with hanabi.app.app_context():
        assert not hanabi.db.session.query(exists_q).scalar()

    # ... and we shouldn't be able to operate on the session
    # after it's been disposed
    response = client.post(sess.manage_url)
    assert response.status_code == 410, response.get_json()
    response = client.get(sess.manage_url)
    assert response.status_code == 410, response.get_json()
