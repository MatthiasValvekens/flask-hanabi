import enum
import logging
import secrets
import hashlib
import hmac
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple

from babel import Locale

from flask import Flask, abort, request, jsonify, render_template
from flask_babel import Babel, get_locale, format_timedelta
from sqlalchemy import UniqueConstraint, update, func, desc, and_, or_
from flask_sqlalchemy import SQLAlchemy

import random

from sqlalchemy.orm.exc import NoResultFound

import config

logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder='static', static_url_path='/static')


app.config.from_object(config)
db = SQLAlchemy(app)
babel = Babel(app, default_domain='hanabi')

DATE_FORMAT_STR = '%Y-%m-%d %H:%M:%S'
MAX_NAME_LENGTH = 250
MAX_HINT_LENGTH = 50
MAX_PLAYERS = 5
CARD_DIST_PER_COLOUR = [3, 2, 2, 2, 1]
MAX_NUM_VALUE = len(CARD_DIST_PER_COLOUR)


def init_db():
    """
    Set up the database schema and/or truncate all sessions.
    """
    # create tables as necessary
    #  we don't use create_all because the effective score wrapper
    #  should never be created by SQLAlchemy
    bind = db.session.bind
    models = (
        HanabiSession, Player, HeldCard, Fireworks, DeckReserve, ActionLog
    )
    for Model in models:
        # noinspection PyUnresolvedReferences
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

    def as_json(self):
        return {
            'colour': self.colour, 'num_value': self.num_value
        }


class HanabiSession(db.Model):
    __tablename__ = 'hanabi_session'

    id = db.Column(db.Integer, primary_key=True)
    # game settings
    cards_in_hand = db.Column(db.Integer, nullable=True)
    created = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    post_action_time_limit = db.Column(
        db.Integer, nullable=False,
        default=app.config['POST_ACTION_TIME_LIMIT_SECONDS']
    )
    post_action_min_time = db.Column(
        db.Integer, nullable=False,
        default=app.config['POST_ACTION_MINIMAL_TIME_SECONDS']
    )
    max_tokens = db.Column(
        db.Integer, nullable=False, default=app.config['TOKEN_COUNT']
    )

    players_present = db.Column(db.Integer, nullable=False, default=0)
    turn = db.Column(db.Integer, nullable=True)
    tokens_remaining = db.Column(db.Integer, nullable=False, default=0)
    errors_remaining = db.Column(
        db.Integer, nullable=False, default=app.config['ERRORS_ALLOWED']
    )
    colour_count = db.Column(
        db.Integer, nullable=False, default=app.config['COLOUR_COUNT']
    )

    active_player_id = db.Column(
        db.Integer, db.ForeignKey(
            'player.id', ondelete='cascade', use_alter=True
        ), nullable=True
    )

    # if the active player already performed an action, and we're waiting
    # for them to end their turn
    end_turn_at = db.Column(db.DateTime, nullable=True)

    # True if the currently active player needs to draw a card when
    # their turn ends
    need_draw = db.Column(db.Boolean, nullable=False, default=False)

    # if this value is non-null, the game will stop when the selected player
    # gets their next turn
    stop_game_after = db.Column(
        db.Integer, db.ForeignKey(
            'player.id', ondelete='cascade', use_alter=True
        ), nullable=True
    )

    final_score = db.Column(db.Integer, nullable=True)

    @classmethod
    def for_update(cls, session_id, *, allow_nonexistent=False) \
            -> 'HanabiSession':
        q = cls.query.filter(cls.id == session_id).with_for_update()
        result = q.one_or_none()
        if result is None and not allow_nonexistent:
            abort(410, description="Session has ended")
        return result

    @property
    def game_running(self) -> bool:
        return self.active_player_id is not None

    @property
    def cards_left(self) -> int:
        return db.session.query(
            func.coalesce(func.sum(DeckReserve.cards_left), 0)
        ).filter(DeckReserve.session_id == self.id).scalar()

    def current_seed(self, pepper):
        if app.config.get('TESTING', False):
            seed_suffix = app.config['TESTING_SEED']
        else:
            seed_suffix = pepper + app.config['SECRET_KEY'].hex()
        return str(self.turn) + seed_suffix

    def current_action(self) -> 'ActionLog':
        try:
            return ActionLog.query.filter(
                and_(
                    ActionLog.session_id == self.id,
                    ActionLog.turn == self.turn
                )
            ).one()
        except NoResultFound:
            raise GameStateError("no current action available")

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

    # denormalised for easy access ("get hands of all players except me")
    session_id = db.Column(
        db.Integer, db.ForeignKey('hanabi_session.id', ondelete='cascade'),
        nullable=False, index=True
    )

    player_id = db.Column(
        db.Integer, db.ForeignKey('player.id', ondelete='cascade'),
        primary_key=True
    )

    colour = db.Column(db.Integer, nullable=False)
    num_value = db.Column(db.Integer, nullable=False)

    # 0-indexed position in the player's hand
    card_position = db.Column(db.Integer, primary_key=True)

    def get_type(self):
        return CardType(colour=self.colour, num_value=self.num_value)

    def __repr__(self):
        return '<HeldCard %d (col %d)>' % (
            self.num_value, self.colour
        )


class Fireworks(db.Model):
    __tablename__ = 'fireworks'

    session_id = db.Column(
        db.Integer, db.ForeignKey('hanabi_session.id', ondelete='cascade'),
        index=True, primary_key=True
    )
    colour = db.Column(db.Integer, primary_key=True)
    current_value = db.Column(db.Integer, nullable=False, default=0)

    def __repr__(self):
        return '<Firework: col %d (currently at %d)>' % (
            self.colour, self.current_value
        )


class DeckReserve(db.Model):
    __tablename__ = 'deck_reserve'
    __table_args__ = (
        UniqueConstraint('session_id', 'colour', 'num_value'),
    )

    session_id = db.Column(
        db.Integer, db.ForeignKey('hanabi_session.id', ondelete='cascade'),
        index=True, primary_key=True
    )
    colour = db.Column(db.Integer, primary_key=True)
    num_value = db.Column(db.Integer, primary_key=True)
    cards_left = db.Column(db.Integer, nullable=False)


class ActionType(enum.Enum):
    HINT = 'HINT'
    DISCARD = 'DISCARD'
    PLAY = 'PLAY'


class ActionLog(db.Model):
    __tablename__ = 'action_log'

    # again, denormalised for easy indexing
    session_id = db.Column(
        db.Integer, db.ForeignKey('hanabi_session.id', ondelete='cascade'),
        index=True, primary_key=True
    )

    turn = db.Column(db.Integer, primary_key=True)
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

    def as_json(self):
        action_type: ActionType = self.action_type
        result = {
            'type': action_type.name,
            'colour': self.colour,
            'num_value': self.num_value,
            'player_id': self.player_id,
        }
        if action_type == ActionType.HINT:
            result['hint_positions'] = self.hint_positions
            result['hint_target'] = self.hint_target
        else:
            result['hand_pos'] = self.hand_pos
            if action_type == ActionType.PLAY:
                result['was_error'] = self.was_error
        return result


def gen_salted_token(salt, *args):
    hmac_key = hashlib.sha1(salt + app.config['SECRET_KEY']).digest()
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


class Status(enum.IntEnum):
    # waiting for game to start
    INITIAL = 0
    # waiting for player action
    PLAYER_THINKING = 1
    # waiting for turn to end
    TURN_END = 2
    # game scored
    GAME_OVER = 3


def session_state(session_id: int, calling_player: int = None):

    sess: HanabiSession = HanabiSession.query\
        .filter(HanabiSession.id == session_id).one_or_none()
    if sess is None:
        return abort(410, description="Session has ended")

    players = Player.query.filter(Player.session_id == session_id)
    player_json_objects = {
        p.id: {'player_id': p.id, 'name': p.name, 'position': p.position}
        for p in players
    }
    response = {
        'created': sess.created,
        'players': list(player_json_objects.values()),
        'colour_count': sess.colour_count,
        'turn': sess.turn,
        'max_tokens': sess.max_tokens
    }
    score = sess.final_score
    if not sess.game_running:
        if score is None:
            response['status'] = Status.INITIAL
            return response
        else:
            response['status'] = Status.GAME_OVER
            response['score'] = sess.final_score
            return response

    # if we're here, the game is actually running

    response['active_player'] = sess.active_player_id
    # grab the status of the fireworks as they are now
    response['current_fireworks'] = query_fireworks_status(sess)
    response['errors_remaining'] = sess.errors_remaining
    response['tokens_remaining'] = sess.tokens_remaining
    response['cards_remaining'] = sess.cards_left
    response['cards_in_hand'] = sess.cards_in_hand

    # tweak player json objects to include their hands,
    # unless called with calling_player None, which is the management API.
    # The latter should only reveal the public parts of the game state
    if calling_player is not None:
        hands, calling_player_used_slots = query_hands_for_others(
            sess, calling_player
        )
        for player_id, player_json in player_json_objects.items():
            if player_id == calling_player:
                response['used_hand_slots'] = calling_player_used_slots
                continue
            player_json['hand'] = [
                card.get_type().as_json() if card is not None else None
                for card in hands[player_id]
            ]

        # ... and regenerate the corresponding response entry
        response['players'] = list(player_json_objects.values())

    if sess.end_turn_at is None:
        response['status'] = Status.PLAYER_THINKING
    else:
        response['last_action'] = sess.current_action().as_json()
        response['end_turn_at'] = sess.end_turn_at.strftime(DATE_FORMAT_STR)
        response['status'] = Status.TURN_END

    return response


class GameStateError(ValueError):
    """
    Non-recoverable game state corruption
    """
    pass


app.register_error_handler(GameStateError, json_err_handler(500))


class ActionNotValid(ValueError):
    """
    Raised when user/server attempts to do something that's not
    allowed and/or impossible at this point in the game.
    """
    pass


app.register_error_handler(ActionNotValid, json_err_handler(400))


def query_fireworks_status(session: HanabiSession, for_update=False) \
        -> List[int]:
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


def query_hands_for_others(session: HanabiSession, player_id: int)\
        -> Tuple[Dict[int, List[Optional[HeldCard]]], List[bool]]:
    """
    Query the hands of all players except the calling one.
    For the latter, we only report on the card slots that are in use.
    """
    hands_q = HeldCard.query.filter(HeldCard.session_id == session.id)

    calling_player_used_slots = [False] * session.cards_in_hand
    result = defaultdict(lambda: [None] * session.cards_in_hand)
    card: HeldCard
    for card in hands_q.all():
        if not (0 <= card.card_position < session.cards_in_hand):
            raise GameStateError(f"Illegal card position: {card.card_position}")
        if card.player_id == player_id:
            calling_player_used_slots[card.card_position] = True
        else:
            hand: list = result[card.player_id]
            hand[card.card_position] = card

    return dict(result), calling_player_used_slots


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


class Deck:
    """
    Convenience wrappers to draw cards from a deck.

    Note: these are not thread-safe w.r.t. the database state!
    """

    def __init__(self, pairs):
        cards = {}
        total_left = 0

        for card_type, count in pairs:
            count = max(count, 0)
            total_left += count
            cards[card_type] = count

        self.cards = cards
        self.total_left = total_left
        self.drawn = set()

    def __getitem__(self, item):
        return self.cards.get(item, 0)

    def draw(self, rng: random.Random):
        selected_ix = rng.randrange(self.total_left)
        cur_ix = 0
        card_type: CardType
        for card_type, cards_left in self.cards.items():
            next_ix = cur_ix + cards_left
            if selected_ix < next_ix:
                break
            cur_ix = next_ix
        else:
            raise GameStateError("Failed to draw card")

        self.cards[card_type] -= 1
        self.total_left -= 1
        self.drawn.add(card_type)

        return card_type

    def update_reserve_queries(self):
        # assumes that the DB state for this session
        # didn't change in between requests, and doesn't
        # add the where clause for sessions

        # register update query
        for card_type in self.drawn:
            yield update(DeckReserve).values(
                {'cards_left': self.cards[card_type]}
            ).where(and_(
                DeckReserve.colour == card_type.colour,
                DeckReserve.num_value == card_type.num_value
            ))

    def execute_update(self, session_id):
        for upd in self.update_reserve_queries():
            db.session.execute(
                upd.where(DeckReserve.session_id == session_id)
            )

    def __iter__(self):
        yield from self.cards.items()


def query_deck_status(session: HanabiSession, for_update=False) -> Deck:
    reserve_q = DeckReserve.query.filter(
        DeckReserve.session_id == session.id
    )
    if for_update:
        reserve_q = reserve_q.with_for_update()

    return Deck(
        (CardType(reserve.colour, reserve.num_value), reserve.cards_left)
        for reserve in reserve_q.all()
    )


def _draw_cards(positions, deck: Deck, rng: random.Random,
                session_id, player_id):
    if deck.total_left < len(positions):
        raise ActionNotValid("Not enough cards left to draw")

    for pos in positions:
        card_type = deck.draw(rng)
        yield HeldCard(
            session_id=session_id, player_id=player_id,
            colour=card_type.colour, num_value=card_type.num_value,
            card_position=pos,
        )


def draw_card(session: HanabiSession, player_id, pepper):
    deck = query_deck_status(session, for_update=True)

    cur_hand = query_hand_for_current_player(
        session, player_id, for_update=True
    )

    for pos, card in enumerate(cur_hand):
        if card is None:
            break
    else:
        raise ActionNotValid("No free slots in hand")

    rng = random.Random(session.current_seed(pepper))

    drawn_card, = _draw_cards((pos,), deck, rng, session.id, player_id)
    db.session.add(drawn_card)
    deck.execute_update(session.id)

    return deck.total_left


def stop_game(session: HanabiSession, score):

    session.active_player_id = None
    session.stop_game_after = None
    session.end_turn_at = None
    session.need_draw = False
    session.final_score = score
    db.session.commit()


def end_turn(session: HanabiSession, pepper):
    # invoked when the end-of-turn timer is triggered, or
    # through the end-of-turn endpoint

    if not session.end_turn_at:
        # This may be None because of a race condition
        #  (i.e. us getting the lock after someone else already
        #  triggered end_turn)
        #  -> no biggie, just bail
        return

    if not session.errors_remaining:
        # too many mistakes -> insta-loss
        stop_game(session, score=0)
        return

    # compute score
    score = db.session.query(
        func.sum(Fireworks.current_value)
    ).filter(Fireworks.session_id == session.id).scalar()
    # check if the score is maxed out or if the last round is over
    if score == session.colour_count * MAX_NUM_VALUE or \
            session.active_player_id == session.stop_game_after:
        stop_game(session, score=score)
        return

    player = Player.query.get(session.active_player_id)
    if session.need_draw:
        try:
            total_left = draw_card(session, player.id, pepper)

            # no cards left in the deck --> final round time
            if not total_left:
                session.stop_game_after = player.id
        except ActionNotValid:
            # no cards left in the deck, that's OK
            pass

    # go to the next player
    next_player_pos = (player.position + 1) % session.players_present
    next_player = Player.query.filter(
        and_(
            Player.session_id == session.id, Player.position == next_player_pos
        )
    ).one()

    # reset stuff for next round
    session.need_draw = False
    session.end_turn_at = None
    session.active_player_id = next_player.id
    session.turn = HanabiSession.turn + 1

    db.session.commit()


def use_card(session: HanabiSession, pos: int) -> CardType:

    if not (0 <= pos < session.cards_in_hand):
        raise ActionNotValid(
            f"Position {pos} is not a valid card position"
        )

    player_id = session.active_player_id

    filter_expr = and_(
        HeldCard.player_id == player_id, HeldCard.card_position == pos
    )
    held: HeldCard = HeldCard.query.filter(filter_expr).one_or_none()

    # can happen during last round, in some variants
    # (not relevant yet, though)
    if held is None:
        raise ActionNotValid(
            f"There\'s no card at position {pos}."
        )

    card_type = held.get_type()

    # clear the card slot
    HeldCard.query.filter(filter_expr).delete()

    return card_type


def finish_action(session: HanabiSession, log: ActionLog):

    # end-of-turn preparation
    db.session.add(log)

    session.need_draw = True
    session.end_turn_at = datetime.utcnow() + timedelta(
        seconds=session.post_action_time_limit
    )
    db.session.commit()


def play_card(session: HanabiSession, pos: int):

    # take the card from the player's hand
    card_type = use_card(session, pos)

    # now, we need to figure out whether the card is playable

    fw: Fireworks
    try:
        fw = Fireworks.query.filter(and_(
            Fireworks.session_id == session.id,
            Fireworks.colour == card_type.colour
        )).one()
    except NoResultFound:
        raise GameStateError("Fireworks not instantiated properly")

    playable = fw.current_value == card_type.num_value - 1

    if playable:
        fw.current_value = card_type.num_value

        # give back a token as a reward for completing a series
        if card_type.num_value == 5 \
                and session.tokens_remaining < session.max_tokens:
            session.tokens_remaining += 1
    else:
        session.errors_remaining -= 1

    # log action for consumption by other users
    log = ActionLog(
        session_id=session.id, turn=session.turn,
        action_type=ActionType.PLAY, player_id=session.active_player_id,
        colour=card_type.colour, num_value=card_type.num_value,
        hand_pos=pos, was_error=not playable
    )

    finish_action(session, log)


def discard_card(session: HanabiSession, pos: int):
    # first, figure out if the player is even allowed to discard
    if session.tokens_remaining == session.max_tokens:
        raise ActionNotValid("Can't discard, must give hint")

    # take the card from the user's hand
    card_type = use_card(session, pos)

    # award a token
    session.tokens_remaining += 1

    # log action for consumption by other users
    log = ActionLog(
        session_id=session.id, turn=session.turn,
        action_type=ActionType.DISCARD, player_id=session.active_player_id,
        colour=card_type.colour, num_value=card_type.num_value,
        hand_pos=pos
    )

    finish_action(session, log)


def give_hint(session: HanabiSession, target_player_id: int,
              colour: int = None, num_value: int = None):

    if (colour is None) == (num_value is None):
        raise ActionNotValid(
            "Exactly one of colour or num_value must be specified."
        )

    if target_player_id == session.active_player_id:
        raise ActionNotValid("Self-hints are not allowed, silly.")

    if not session.tokens_remaining:
        raise ActionNotValid("No hint tokens remaining")

    player_q = Player.query.filter(
        and_(Player.id == target_player_id, Player.session_id == session.id)
    )
    if db.session.query(~player_q.exists()).scalar():
        raise ActionNotValid(
            "No such player in session."
        )

    # find out the positions of the cards
    card_q = db.session.query(HeldCard.card_position).filter(
        HeldCard.player_id == target_player_id
    ).order_by(HeldCard.card_position)

    if colour is not None:
        card_q = card_q.filter(HeldCard.colour == colour)
    else:
        card_q = card_q.filter(HeldCard.num_value == num_value)

    # ... and format them into a nice, parsable format for the
    #  UI to use
    pos_string = ','.join(str(pos) for pos, in card_q)

    log = ActionLog(
        session_id=session.id, turn=session.turn,
        action_type=ActionType.HINT, player_id=session.active_player_id,
        colour=colour, num_value=num_value,
        hint_positions=pos_string, hint_target=target_player_id,
    )

    session.tokens_remaining -= 1

    finish_action(session, log)


def init_session(session: HanabiSession, pepper):
    if session.active_player_id is not None:
        # this (probably) points towards a select for update lock
        # doing its job.
        return

    # clear old data
    for model in (DeckReserve, Fireworks, ActionLog, HeldCard):
        model.query.filter(model.session_id == session.id).delete()

    hand_size = 5 if session.players_present in (2, 3) else 4
    session.tokens_remaining = session.max_tokens
    session.errors_remaining = app.config['ERRORS_ALLOWED']
    session.final_score = None
    session.turn = 0
    session.cards_in_hand = hand_size

    # insert initial fireworks
    db.session.bulk_save_objects(
        Fireworks(session_id=session.id, colour=col, current_value=0)
        for col in range(session.colour_count)
    )

    rng = random.Random(session.current_seed(pepper) + 'init')
    deck = Deck(
        (CardType(colour=col, num_value=ix + 1), count)
        for col in range(session.colour_count)
        for ix, count in enumerate(CARD_DIST_PER_COLOUR)
    )

    player_id_q = db.session.query(Player.id).filter(
        Player.session_id == session.id
    ).order_by(Player.position)
    player_ids = list(pid for pid, in player_id_q)

    if len(player_ids) < 2:
        raise GameStateError("Too few players found")

    session.active_player_id = player_ids[0]

    # draw all hand cards
    def _hands_gen():
        for player_id in player_ids:
            yield from _draw_cards(
                set(range(hand_size)), deck, rng,
                session.id, player_id
            )

    # insert hand cards
    db.session.bulk_save_objects(_hands_gen())

    # initialise the deck reserves
    db.session.bulk_save_objects(
        DeckReserve(
            session_id=session.id, colour=card_type.colour,
            num_value=card_type.num_value, cards_left=cards_left
        ) for card_type, cards_left in deck
    )


# update the DB if an end-of-turn timer is triggered
def _eot_heartbeat_tasks(session_id, pepper):
    # NOTE: this routine runs on GET requests as well!
    # This is OK, since from the perspective of the client, the GET-request
    #  still doesn't modify any state. If you want, this routine merely brings
    #  the DB state in line with the "virtual" state of the session, which
    #  exists independently of the request.

    # first, query without special locks for better throughput
    # (the vast majority of requests won't trigger an end-of-turn timer, and we
    # don't want those to be stalled by locks)

    sess: HanabiSession = HanabiSession.query \
        .filter(HanabiSession.id == session_id).one_or_none()
    if sess is None:
        return

    now = datetime.utcnow()
    if sess.end_turn_at is None or sess.end_turn_at >= now:
        # nothing to do
        return

    # re-fetch with FOR UPDATE
    sess = HanabiSession.for_update(session_id)
    # re-check condition to prevent concurrency shenanigans
    if sess.end_turn_at is None or sess.end_turn_at >= now:
        return

    # run the end_turn logic (which also commits the current transaction)
    end_turn(sess, pepper)


@app.route(mgmt_url, methods=['GET', 'POST', 'DELETE'])
def manage_session(session_id, pepper, mgmt_token):
    check_mgmt_token(session_id, pepper, mgmt_token)
    if request.method == 'GET':
        _eot_heartbeat_tasks(session_id, pepper)
        return session_state(session_id)
    elif request.method == 'DELETE':
        # give up
        sess: HanabiSession = HanabiSession.for_update(session_id)
        stop_game(sess, 0)
        return jsonify({}), 200

    if request.method == 'POST':
        # game initialisation logic

        sess: HanabiSession = HanabiSession.for_update(session_id)
        # if the score is not None, treat it as a session reset
        if sess.game_running and sess.final_score is None:
            # already initialised, nothing to do
            return jsonify({}), 200

        if sess.players_present < 2:
            return abort(
                409,
                description="Cannot start game without at least two players"
            )

        # initialise the session
        init_session(sess, pepper)

        db.session.commit()

        return jsonify({}), 200


@app.route(session_url_base + '/join/<inv_token>', methods=['POST'])
def session_join(session_id, pepper, inv_token):
    check_inv_token(session_id, pepper, inv_token)

    sess: HanabiSession = HanabiSession.for_update(session_id)

    if sess.game_running or sess.players_present > MAX_PLAYERS:
        return abort(409, description="This session is not accepting players.")

    submission_json = request.get_json()
    if submission_json is None:
        return abort(400, description="Malformed submission data")
    try:
        name = submission_json['name'][:MAX_NAME_LENGTH]
    except KeyError:
        return abort(400, description="'Name' is required")

    position = sess.players_present
    p = Player(name=name, session_id=session_id, position=position)
    db.session.add(p)
    sess.players_present += 1
    db.session.commit()
    return {
        'player_id': p.id,
        'player_token': gen_player_token(session_id, p.id, pepper),
        'position': position,
        'name': name
    }, 201


def _ensure_active_player(session_id, player_id):

    sess = HanabiSession.for_update(session_id)

    if not sess.game_running:
        abort(409, description="Round not started")

    if sess.active_player_id != player_id:
        abort(409, description="Player acting out of turn")

    return sess


def _get_int_or_none(json: dict, key):
    val = json.get(key, None)
    if val is not None:
        try:
            val = int(val)
        except (TypeError, ValueError):
            return abort(400, f"Improper value for {key}")
    return val


@app.route(play_url, methods=['GET', 'POST'])
def play(session_id, pepper, player_id, player_token):
    check_player_token(session_id, pepper, player_id, player_token)
    _eot_heartbeat_tasks(session_id, pepper)

    if request.method == 'GET':
        return session_state(session_id, player_id)

    sess = _ensure_active_player(session_id, player_id)

    if sess.end_turn_at is not None:
        return abort(409, description="Action already submitted")

    request_data = request.get_json()
    if request_data is None:
        return abort(400, description="No request data")
    try:
        action_type = ActionType(request_data['type'])
    except (TypeError, KeyError, ValueError):
        return abort(400, description="Improper action type specification.")

    colour = _get_int_or_none(request_data, 'colour')
    num_value = _get_int_or_none(request_data, 'num_value')
    position = _get_int_or_none(request_data, 'position')
    if action_type == ActionType.HINT:
        try:
            hint_target = int(request_data['hint_target'])
        except (TypeError, KeyError, ValueError):
            return abort(400, description="Improper hint_target specification.")

        give_hint(
            sess, target_player_id=hint_target, colour=colour,
            num_value=num_value
        )
    else:
        if position is None:
            raise abort(400, description="Position is a mandatory parameter")
        if action_type == ActionType.DISCARD:
            discard_card(sess, position)
        else:
            play_card(sess, position)

    return session_state(session_id, player_id)


@app.route(play_url + '/advance', methods=['POST'])
def advance(session_id, pepper, player_id, player_token):
    check_player_token(session_id, pepper, player_id, player_token)
    sess = _ensure_active_player(session_id, player_id)
    if sess.end_turn_at is None:
        return abort(409, description="Action required")

    # there are two things at play: end_turn_at is a "dead man's switch"
    # that triggers the EOT logic automatically if the acting player forgets
    # to perform an action.
    # If the player clicks the "advance" button manually, they want to end their
    # turn ASAP, but we should allow the other players to evaluate the last
    # action. If the minimal post-action time hasn't passed by this point,
    # we reschedule end_turn_at to the earliest possible point,
    # otherwise we end the turn right there.

    now = datetime.utcnow()
    player_acted_at = sess.end_turn_at - timedelta(
        seconds=sess.post_action_time_limit
    )
    earliest_possible_eot = player_acted_at + timedelta(
        seconds=sess.post_action_min_time
    )
    if now >= earliest_possible_eot:
        # minimal time has elapsed, end the turn.
        end_turn(sess, pepper)
        status = 200
    else:
        # reschedule EOT
        sess.end_turn_at = earliest_possible_eot
        status = 425  # 425 Too Early
        db.session.commit()

    return jsonify({}), status


@app.route(play_url + '/discarded', methods=['GET'])
def discarded(session_id, pepper, player_id, player_token):
    check_player_token(session_id, pepper, player_id, player_token)

    sess: HanabiSession = HanabiSession.query \
        .filter(HanabiSession.id == session_id).one_or_none()
    if sess is None:
        return abort(410, "Session has ended")
    if not sess.game_running:
        return abort(409, "Game is currently not running")

    discarded_cards = db.session.query(
        ActionLog.colour, ActionLog.num_value
    ).filter(
        and_(
            ActionLog.session_id == session_id,
            or_(
                ActionLog.action_type == ActionType.DISCARD,
                ActionLog.was_error == True
            )
        )
    ).order_by(desc(ActionLog.turn))
    return {
        'discarded': [
            {'colour': colour, 'num_value': num_value}
            for colour, num_value in discarded_cards
        ]
    }
