import enum
import logging
import secrets
import hashlib
import hmac
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import IntEnum, Enum, auto

from babel import Locale

from flask import Flask, abort, request, jsonify, render_template
from flask_babel import Babel, get_locale, format_timedelta
from sqlalchemy import UniqueConstraint, update
from flask_sqlalchemy import SQLAlchemy

import random

import config

logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='static', static_url_path='/static')


app.config.from_object(config)
# FIXME this is bad mojo with multiple workers
app.config['SECRET_KEY'] = server_key = secrets.token_bytes(32)
db = SQLAlchemy(app)
babel = Babel(app, default_domain='hanabi')

DATE_FORMAT_STR = '%Y-%m-%d %H:%M:%S'
MAX_NAME_LENGTH = 250
MAX_HELD_CARDS = 5
MAX_HINT_LENGTH = 50


def init_db():
    """
    Set up the database schema and/or truncate all sessions.
    """
    # create tables as necessary
    #  we don't use create_all because the effective score wrapper
    #  should never be created by SQLAlchemy
    bind = db.session.bind
    for Model in (HanabiSession, Player):
        Model.__table__.create(bind, checkfirst=True)

    with db.engine.connect() as con:
        with con.begin():
            # truncate all sessions on every restart
            con.execute('TRUNCATE hanabi_session RESTART IDENTITY CASCADE;')


# adding before_first_request to init_db would cause this to be run
#  for every worker, which isn't what we want.
# In prod, a a CLI command seems to involve the least amount of hassle
app.cli.command('initdb')(init_db)

app.jinja_env.auto_reload = True
app.config['TEMPLATES_AUTO_RELOAD'] = True
if __name__ == '__main__':
    init_db()
    app.run()


def json_err_handler(error_code):
    return lambda e: (jsonify(error=str(e)), error_code)


for err in (400, 403, 404, 409, 410, 501):
    app.register_error_handler(err, json_err_handler(err))


@dataclass(frozen=True)
class CardType:
    colour: int
    num_value: int


class HanabiSession(db.Model):
    __tablename__ = 'hanabi_session'

    id = db.Column(db.Integer, primary_key=True)
    # game settings
    cards_in_hand = db.Column(db.Integer, nullable=False)
    created = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    colours = db.Column(db.Integer, nullable=False, default=5)
    post_action_time_limit = db.Column(
        db.Integer, nullable=False,
        default=app.config['POST_ACTION_TIME_LIMIT_SECONDS']
    )

    # volatile data
    round_start = db.Column(db.DateTime, nullable=True)
    players_present = db.Column(db.Integer, nullable=False, default=0)
    turn = db.Column(db.Integer, nullable=False, default=0)
    tokens_remaining = db.Column(
        db.Integer, nullable=False, default=app.config['TOKEN_COUNT']
    )
    errors_remaining = db.Column(
        db.Integer, nullable=False, default=app.config['ERRORS_ALLOWED']
    )
    colour_count = db.Column(
        db.Integer, nullable=False, default=app.config['COLOUR_COUNT']
    )

    active_player_id = db.Column(
        db.Integer, db.ForeignKey('player.id', ondelete='cascade'),
        nullable=True,
    )

    # if the active player already performed an action, and we're waiting
    # for them to end their turn
    turn_action_performed = db.Column(db.DateTime, nullable=True)

    # True if the currently active player needs to draw a card when
    # their turn ends
    need_draw = db.Column(db.Boolean, nullable=False, default=False)

    # if this value is non-null, the game will stop when the selected player
    # gets their next turn
    stop_game_before = db.Column(
        db.Integer, db.ForeignKey('player.id', ondelete='cascade'),
        nullable=True
    )

    @classmethod
    def for_update(cls, session_id, *, allow_nonexistent=False):
        q = cls.query.filter(cls.id == session_id).with_for_update()
        if allow_nonexistent:
            return q.one_or_none()
        else:
            return q.one()

    def __repr__(self):
        fmt_ts = self.created.now().strftime(DATE_FORMAT_STR)
        return '<Session %s>' % fmt_ts


class Player(db.Model): 
    __tablename__ = 'player'
    __table_args__ = (
        UniqueConstraint('session_id', 'position'),
    )
    id = db.Column(db.Integer, primary_key=True)
    position = db.Column(db.Integer, nullable=False)
    session_id = db.Column(
        db.Integer, db.ForeignKey('hanabi_session.id', ondelete='cascade'),
        nullable=False,
    )
    name = db.Column(db.String(MAX_NAME_LENGTH), nullable=False)

    def __repr__(self):
        return '<Player %r (%r)>' % (self.name, self.id)


class HeldCard(db.Model):
    __tablename__ = 'held_card'
    __table_args__ = (
        UniqueConstraint('player_id', 'card_position'),
    )

    # denormalised for easy access ("get hands of all players except me")
    session_id = db.Column(
        db.Integer, db.ForeignKey('hanabi_session.id', ondelete='cascade'),
        nullable=False, index=True
    )

    player_id = db.Column(
        db.Integer, db.ForeignKey('player.id', ondelete='cascade'),
        nullable=False
    )

    colour = db.Column(db.Integer, nullable=False)
    num_value = db.Column(db.Integer, nullable=False)

    # 0-indexed position in the player's hand
    card_position = db.Column(db.Integer, nullable=False)

    def __repr__(self):
        return '<HeldCard %d (col %d)>' % (
            self.num_value, self.colour
        )


class Fireworks(db.Model):
    __tablename__ = 'fireworks'
    __table_args__ = (
        UniqueConstraint('session_id', 'colour')
    )

    session_id = db.Column(
        db.Integer, db.ForeignKey('hanabi_session.id', ondelete='cascade'),
        nullable=False, index=True
    )
    colour = db.Column(db.Integer, nullable=False)
    current_value = db.Column(db.Integer, nullable=False, default=0)

    def __repr__(self):
        return '<Firework: col %d (currently at %d)>' % (
            self.colour, self.current_value
        )


class DeckReserve(db.Model):
    __tablename__ = 'deck_reserve'
    __table_args__ = (
        UniqueConstraint('session_id', 'colour', 'num_value')
    )

    session_id = db.Column(
        db.Integer, db.ForeignKey('hanabi_session.id', ondelete='cascade'),
        nullable=False, index=True
    )
    colour = db.Column(db.Integer, nullable=False)
    num_value = db.Column(db.Integer, nullable=False)
    cards_left = db.Column(db.Integer, nullable=False)


class ActionType(enum.Enum):
    hint = auto()
    discard = auto()
    play = auto()


class ActionLog(db.Model):
    __tablename__ = 'action_log'

    __table_args__ = (
        UniqueConstraint('session_id', 'turn')
    )

    # again, denormalised for easy indexing
    session_id = db.Column(
        db.Integer, db.ForeignKey('hanabi_session.id', ondelete='cascade'),
        nullable=False, index=True
    )

    # TODO just for fun, is there some kind of canonical way
    #  to make sorting on a filtered column as efficient as possible?
    turn = db.Column(db.Integer, nullable=False, index=True)
    action_type = db.Column(db.Enum(ActionType), nullable=False)

    player_id = db.Column(
        db.Integer, db.ForeignKey('player.id', ondelete='cascade'),
        nullable=False
    )

    # these two values have the potential to be relevant in all cases:
    #  If the action is a HINT, precisely one of these must be non-null,
    #   indicating the scope of the hint
    #  If the action is a play/discard action, both must be non-null, indicating
    #   the card that was played.

    colour = db.Column(db.Integer, nullable=True)
    num_value = db.Column(db.Integer, nullable=True)

    # relevant for card actions (play/discard), must be non-null then
    hand_pos = db.Column(db.Integer, nullable=True)

    # error flag indicating whether a 'play' error incurred the
    # wrath of the gods
    was_error = db.Column(db.Boolean, nullable=False, default=False)

    # string with comma-separated position values (only the UI cares about this)
    hint_positions = db.Column(db.String(MAX_HINT_LENGTH), nullable=True)
    hint_target = db.Column(
        db.Integer, db.ForeignKey('player.id', ondelete='cascade'),
        nullable=True
    )


def gen_salted_token(salt, *args):
    hmac_key = hashlib.sha1(salt + server_key).digest()
    token_data = ''.join(str(d) for d in args)
    salted_hmac = hmac.new(
        hmac_key, msg=token_data.encode('ascii'),
        digestmod=hashlib.sha1
    )
    token_hash = salted_hmac.hexdigest()[::2]
    assert len(token_hash) == 20
    return token_hash


def gen_session_mgmt_token(session_id, pepper):
    return gen_salted_token(b'sessman', session_id, pepper)


def gen_session_inv_token(session_id, pepper):
    return gen_salted_token(b'session', session_id, pepper)


def gen_player_token(session_id, player_id, pepper):
    return gen_salted_token(b'player', session_id, pepper, player_id)


supported_locales = [
    Locale.parse(locale) for locale in app.config['BABEL_SUPPORTED_LOCALES']
]

app.add_template_filter(format_timedelta)


@app.route('/', methods=['GET'])
def index():
    return render_template(
        'hanabi.html', api_base_url=app.config['API_BASE_URL'],
        default_countdown=app.config['DEFAULT_COUNTDOWN_SECONDS'],
        active_locale=get_locale(),
        available_locales=supported_locales,
    )


@babel.localeselector
def select_locale():
    try:
        return request.args['lang']
    except KeyError:
        return request.accept_languages.best_match(
            app.config['BABEL_SUPPORTED_LOCALES']
        )


@app.route('/session', methods=['POST'])
def spawn_session():
    new_session = HanabiSession()
    db.session.add(new_session)
    db.session.commit()
    pepper = secrets.token_bytes(8).hex()
    sess_id = new_session.id

    return {
        'session_id': sess_id,
        'pepper': pepper,
        'session_mgmt_token': gen_session_mgmt_token(sess_id, pepper),
        'session_token': gen_session_inv_token(sess_id, pepper)
    }, 201


session_url_base = '/session/<int:session_id>/<pepper>'
mgmt_url = session_url_base + '/manage/<mgmt_token>'
play_url = session_url_base + '/play/<int:player_id>/<player_token>'


def check_mgmt_token(session_id, pepper, mgmt_token):
    true_token = gen_session_mgmt_token(session_id, pepper)
    if mgmt_token != true_token:
        abort(403, description="Bad session management token")


def check_inv_token(session_id, pepper, inv_token):
    true_token = gen_session_inv_token(session_id, pepper)
    if inv_token != true_token:
        abort(403, description="Bad session token")


def check_player_token(session_id, pepper, player_id, player_token):
    true_token = gen_player_token(session_id, player_id, pepper)
    if player_token != true_token:
        abort(403, description="Bad player token")


def session_state(session_id, pepper):
    pass  # TODO implement


class GameStateError(ValueError):
    """
    Non-recoverable game state corruption
    """
    pass


class ActionNotValidNow(ValueError):
    """
    Raised when user/server attempts to do something that's not
    allowed and/or impossible at this point in the game.
    """
    pass


def query_fireworks_status(session: HanabiSession, for_update=False):
    fireworks = [0] * session.colour_count
    fireworks_q = Fireworks.query.filter(
        Fireworks.session_id == session.id
    )
    if for_update:
        fireworks_q = fireworks_q.with_for_update()
    fw: Fireworks
    for fw in fireworks_q.all():
        try:
            fireworks[fw.colour] = fw.current_value
        except IndexError as e:
            raise GameStateError("Illegal colour value", e)

    return fireworks


def query_hands_for_others(session: HanabiSession, player: Player):
    hands_q = HeldCard.query.filter(
        HeldCard.session_id == session.id
        and HeldCard.player_id != player.id
    )
    result = defaultdict(lambda: [None] * session.cards_in_hand)
    card: HeldCard
    for card in hands_q.all():
        hand: list = result[card.player_id]
        try:
            hand[card.card_position] = card
        except IndexError as e:
            raise GameStateError("Illegal card position", e)

    return result


def query_hand_for_current_player(session: HanabiSession, player_id,
                                  for_update=False):
    hands_q = HeldCard.query.filter(HeldCard.player_id == player_id)
    if for_update:
        hands_q = hands_q.with_for_update()

    hand = [None] * session.cards_in_hand
    for card in hands_q.all():
        try:
            hand[card.card_position] = card
        except IndexError as e:
            raise GameStateError("Illegal card position", e)
    return hand


def query_deck_status(session: HanabiSession, for_update=False):
    reserve_q = DeckReserve.query.filter(
        DeckReserve.session_id == session.id
    )
    if for_update:
        reserve_q = reserve_q.with_for_update()

    deck = defaultdict(int)

    total_left = 0
    reserve: DeckReserve
    for reserve in reserve_q.all():
        count = max(reserve.cards_left, 0)
        total_left += count
        deck[CardType(reserve.colour, reserve.num_value)] = count

    return total_left, deck


def draw_card(session: HanabiSession, player_id):
    total_left, deck = query_deck_status(session, for_update=True)

    if not total_left:
        raise ActionNotValidNow("No cards left to draw")

    cur_hand = query_hand_for_current_player(session, player_id, for_update=True)

    for pos, card in enumerate(cur_hand):
        if card is None:
            break
    else:
        raise ActionNotValidNow("No free slots in hand")

    selected_ix = random.randint(0, total_left - 1)
    cur_ix = 0
    card_type: CardType
    for card_type, cards_left in deck.items():
        next_ix = cur_ix + cards_left
        if selected_ix < next_ix:
            break
    else:
        raise GameStateError("Failed to draw card")

    deck_update_q = update(DeckReserve).values(
        {'cards_left': DeckReserve.cards_left - 1}
    ).where(
        DeckReserve.session_id == session.id
        and DeckReserve.colour == card_type.colour
        and DeckReserve.num_value == card_type.num_value
    )

    drawn_card = HeldCard(
        session_id=session.id, player_id=player_id,
        colour=card_type.colour, num_value=card_type.num_value,
        card_position=pos
    )

    db.session.execute(deck_update_q)
    db.session.add(drawn_card)

    return total_left - 1


def end_turn(session: HanabiSession):
    # invoked when the end-of-turn timer is triggered, or
    # through the end-of-turn endpoint

    if not session.turn_action_performed:
        raise ActionNotValidNow(
            "The player needs to perform an action first"
        )

    player = Player.query.get(session.active_player_id)
    if session.need_draw:
        try:
            total_left = draw_card(session, player.id)

            # no cards left in the deck --> final round time
            if not total_left:
                session.stop_game_before = player.id
        except ActionNotValidNow:
            # no cards left in the deck, that's OK
            pass

    # go to the next player
    next_player_pos = (player.position + 1) % session.players_present
    next_player = Player.query.filter(
        Player.session_id == session.id
        and Player.position == next_player_pos
    ).one()

    # reset stuff for next round
    session.need_draw = False
    session.turn_action_performed = None
    session.active_player_id = next_player.id
    session.turn = HanabiSession.turn + 1

    db.session.commit()


@app.route(mgmt_url, methods=['GET', 'POST', 'DELETE'])
def manage_session(session_id, pepper, mgmt_token):
    check_mgmt_token(session_id, pepper, mgmt_token)

    if request.method == 'GET':
        return session_state(session_id, pepper)

    if request.method == 'DELETE':
        HanabiSession.query.filter(HanabiSession.id == session_id).delete()
        db.session.commit()
        return jsonify({}), 204

    if request.method == 'POST':
        # prepare a new round
        sess: HanabiSession = HanabiSession.for_update(
            session_id, allow_nonexistent=True
        )
        if sess is None:
            abort(410, "Session has ended")
        player_q = Player.query.filter(Player.session_id == session_id)
        if db.session.query(~player_q.exists()).scalar():
            return abort(409, "Cannot advance round without players")
        # TODO delete scores if we're skipping ahead

        json_data = request.get_json()
        until_start = app.config['DEFAULT_COUNTDOWN_SECONDS']
        if json_data is not None:
            until_start = json_data.get('until_start', until_start)
        sess.round_start = datetime.utcnow() + timedelta(seconds=until_start)
        return {
            'round_start': sess.round_start.strftime(DATE_FORMAT_STR)
        }


@app.route(session_url_base + '/join/<inv_token>', methods=['POST'])
def session_join(session_id, pepper, inv_token):
    check_inv_token(session_id, pepper, inv_token)

    sess: HanabiSession = HanabiSession.query.get(session_id)

    if sess.round_start is not None:
        return abort(409, description="This session is not accepting players.")

    submission_json = request.get_json()
    if submission_json is None:
        return abort(400, description="Malformed submission data")
    try:
        name = submission_json['name'][:MAX_NAME_LENGTH]
    except KeyError:
        return abort(400, description="'Name' is required")

    p = Player(name=name)
    sess.players.append(p)
    db.session.commit()
    return {
        'player_id': p.id,
        'player_token': gen_player_token(session_id, p.id, pepper),
        'name': name
    }, 201


def play(session_id, pepper, player_id, player_token):
    check_player_token(session_id, pepper, player_id, player_token)

    # the existence check happens later, so in principle players who
    #  left the session can still watch
    if request.method == 'GET':
        return session_state(session_id, pepper)

    sess = HanabiSession.for_update(session_id, allow_nonexistent=True)
    if sess is None:
        return abort(410, description="Session has ended")

    round_start = sess.round_start
    if round_start is None:
        return abort(409, description="Round not started")


    return jsonify({}), 201
