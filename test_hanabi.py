import json
from collections import namedtuple

import flask
import pytest
import hanabi


@pytest.fixture
def client():
    hanabi.app.config['TESTING'] = True
    hanabi.app.config['TESTING_SEED'] = "abcdefg"
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


def two_players(client):

    sess = create_session(client)
    gc1 = create_player_in_session(client, sess, name='tester1')
    gc2 = create_player_in_session(client, sess, name='tester2')
    response = client.post(sess.manage_url)
    assert response.status_code == 200, response.get_json()
    return sess, gc1, gc2


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


# with the test seeds, this is how things are
P1_INITIAL_HAND = [
    {'colour': 4, 'num_value': 1}, {'colour': 3, 'num_value': 2},
    {'colour': 4, 'num_value': 1}, {'colour': 4, 'num_value': 3}
]
P2_INITIAL_HAND = [
    {'colour': 1, 'num_value': 1}, {'colour': 1, 'num_value': 5},
    {'colour': 1, 'num_value': 3}, {'colour': 4, 'num_value': 5}
]

def test_create_join(client):
    sess = create_session(client)
    gc1 = create_player_in_session(client, sess, name='tester1')

    response = client.get(sess.manage_url)
    rdata = response.get_json()
    assert len(rdata['players']) == 1, rdata['players']
    assert rdata['status'] == hanabi.Status.INITIAL

    # we shouldn't be able to start a game yet
    response = client.post(sess.manage_url)
    assert response.status_code == 409, response.get_json()

    gc2 = create_player_in_session(client, sess, name='tester2')
    response = client.get(sess.manage_url)
    rdata = response.get_json()
    assert len(rdata['players']) == 2, rdata['players']

    response = client.post(sess.manage_url)
    assert response.status_code == 200, response.get_json()

    # check if we can query the game state
    response = client.get(gc1.play_url)
    assert response.status_code == 200, response.get_json()
    rdata = response.get_json()
    status = rdata['status']
    assert status == hanabi.Status.PLAYER_THINKING, status
    for pdata in rdata['players']:
        if pdata['player_id'] == gc1.player_id:
            assert 'hand' not in pdata
        else:
            hand = pdata['hand']
            assert hand == P2_INITIAL_HAND, hand


def test_play_one_playable_card(client):
    sess, gc1, gc2 = two_players(client)
    response = request_json(
        client, 'post',
        gc2.play_url, data={'type': 'PLAY', 'position': 0}
    )
    assert response.status_code == 409, response.get_json()

    # can't advance yet either
    response = client.post(gc1.play_url + '/advance')
    assert response.status_code == 409, response.get_json()

    response = request_json(
        client, 'post',
        gc1.play_url, data={'type': 'PLAY', 'position': 0}
    )
    assert response.status_code == 200, response.get_json()
    rdata = response.get_json()
    assert rdata['current_fireworks'] == [0, 0, 0, 0, 1]

    # check from the other player's point of view
    response = client.get(gc2.play_url)
    assert response.status_code == 200, response.get_json()
    rdata = response.get_json()

    assert rdata['players'][0]['hand'][0] is None
    assert rdata['current_fireworks'] == [0, 0, 0, 0, 1]
    assert rdata['errors_remaining'] == 3
    assert rdata['tokens_remaining'] == 8
    assert rdata['active_player'] == gc1.player_id
    action = rdata['last_action']
    assert action['type'] == 'PLAY'
    assert action['colour'] == P1_INITIAL_HAND[0]['colour']
    assert action['num_value'] == P1_INITIAL_HAND[0]['num_value']
    assert action['hand_pos'] == 0
    assert action['was_error'] is False

    # trigger end-of-turn
    client.post(gc1.play_url + '/advance')
    response = client.get(gc2.play_url)
    rdata = response.get_json()
    assert rdata['players'][0]['hand'][0] is not None
    assert rdata['active_player'] == gc2.player_id


def test_play_one_unplayable_card(client):
    sess, gc1, gc2 = two_players(client)
    response = request_json(
        client, 'post',
        gc1.play_url, data={'type': 'PLAY', 'position': 1}
    )
    assert response.status_code == 200, response.get_json()
    rdata = response.get_json()
    assert rdata['current_fireworks'] == [0, 0, 0, 0, 0]

    # check from the other player's point of view
    response = client.get(gc2.play_url)
    assert response.status_code == 200, response.get_json()
    rdata = response.get_json()

    assert rdata['players'][0]['hand'][1] is None
    assert rdata['current_fireworks'] == [0, 0, 0, 0, 0]
    assert rdata['errors_remaining'] == 2
    assert rdata['active_player'] == gc1.player_id
    action = rdata['last_action']
    assert action['type'] == 'PLAY'
    assert action['colour'] == P1_INITIAL_HAND[1]['colour']
    assert action['num_value'] == P1_INITIAL_HAND[1]['num_value']
    assert action['hand_pos'] == 1
    assert action['was_error'] is True

    # trigger end-of-turn
    client.post(gc1.play_url + '/advance')
    response = client.get(gc2.play_url)
    rdata = response.get_json()
    assert rdata['players'][0]['hand'][1] is not None
    assert rdata['active_player'] == gc2.player_id

    response = client.get(gc1.play_url + '/discarded')
    rdata = response.get_json()
    assert rdata['discarded'] == [P1_INITIAL_HAND[1]]


def test_discard_one_card(client):
    sess, gc1, gc2 = two_players(client)
    response = request_json(
        client, 'post',
        gc1.play_url, data={'type': 'DISCARD', 'position': 1}
    )
    assert response.status_code == 200, response.get_json()
    rdata = response.get_json()
    assert rdata['current_fireworks'] == [0, 0, 0, 0, 0]

    # check from the other player's point of view
    response = client.get(gc2.play_url)
    assert response.status_code == 200, response.get_json()
    rdata = response.get_json()

    assert rdata['players'][0]['hand'][1] is None
    assert rdata['current_fireworks'] == [0, 0, 0, 0, 0]
    assert rdata['errors_remaining'] == 3
    assert rdata['active_player'] == gc1.player_id
    action = rdata['last_action']
    assert action['type'] == 'DISCARD'
    assert action['colour'] == P1_INITIAL_HAND[1]['colour']
    assert action['num_value'] == P1_INITIAL_HAND[1]['num_value']
    assert action['hand_pos'] == 1
    assert 'was_error' not in action

    # trigger end-of-turn
    client.post(gc1.play_url + '/advance')
    response = client.get(gc2.play_url)
    rdata = response.get_json()
    assert rdata['players'][0]['hand'][1] is not None
    assert rdata['active_player'] == gc2.player_id

    response = client.get(gc1.play_url + '/discarded')
    rdata = response.get_json()
    assert rdata['discarded'] == [P1_INITIAL_HAND[1]]


def test_give_hint(client):
    sess, gc1, gc2 = two_players(client)
    response = request_json(
        client, 'post',
        gc1.play_url, data={
            'type': 'HINT', 'num_value': 5, 'hint_target': gc2.player_id
        }
    )
    assert response.status_code == 200, response.get_json()
    rdata = response.get_json()
    assert rdata['current_fireworks'] == [0, 0, 0, 0, 0]

    # check from the other player's point of view
    response = client.get(gc2.play_url)
    assert response.status_code == 200, response.get_json()
    rdata = response.get_json()

    assert None not in rdata['players'][0]['hand'][1]
    assert rdata['current_fireworks'] == [0, 0, 0, 0, 0]
    assert rdata['errors_remaining'] == 3
    assert rdata['active_player'] == gc1.player_id
    action = rdata['last_action']
    assert action['type'] == 'HINT'
    assert action.get('colour', None) is None
    assert action['num_value'] == 5
    assert action['hint_target'] == gc2.player_id
    assert action['hint_positions'] == '1,3'
    assert 'was_error' not in action

    # trigger end-of-turn
    client.post(gc1.play_url + '/advance')
    response = client.get(gc2.play_url)
    rdata = response.get_json()
    assert rdata['active_player'] == gc2.player_id


def test_no_self_hints(client):
    sess, gc1, gc2 = two_players(client)
    response = request_json(
        client, 'post',
        gc1.play_url, data={
            'type': 'HINT', 'num_value': 5, 'hint_target': gc1.player_id
        }
    )
    assert response.status_code == 400, response.get_json()


def test_no_specific_hints(client):
    sess, gc1, gc2 = two_players(client)
    response = request_json(
        client, 'post',
        gc1.play_url, data={
            'type': 'HINT', 'num_value': 5, 'colour': 1,
            'hint_target': gc1.player_id
        }
    )
    assert response.status_code == 400, response.get_json()
