export const GameStatus = Object.freeze({
    INITIAL: 0, PLAYER_THINKING: 1,
    TURN_END: 2, GAME_OVER: 3
});

export const ActionType = Object.freeze({
    HINT: 'HINT', PLAY: 'PLAY', DISCARD: 'DISCARD'
});

export const colourNames = [
    "green", "red", "orange", "purple", "blue"
];
export const colourValues = [
    "#048304", "#b52d30",
    "#cb761a", "#cb1ac8",
    "#1d72aa"
];

export class SessionContext {
    /**
     * Boggle session context, including (optional) access to the management API.
     * @param {!int} sessionId
     * @param {!string} saltToken
     * @param {!string} invToken
     * @param {?string=null} mgmtToken
     */
    constructor(sessionId, saltToken, invToken, mgmtToken=null) {
        this.sessionId = sessionId;
        this.saltToken = saltToken;
        this.invToken = invToken;
        this.mgmtToken = mgmtToken;
    }

    get endpointBase() {
        return `/session/${this.sessionId}/${this.saltToken}`;
    }

    get joinEndpoint() {
        return `${this.endpointBase}/join/${this.invToken}`;
    }

    get isManager() {
        return this.mgmtToken !== null;
    }

    get mgmtEndpoint() {
        if(!this.isManager)
            throw "Management token not present";
        return `${this.endpointBase}/manage/${this.mgmtToken}`;
    }

}

export class PlayerContext {
    /**
     * Boggle player context.
     * @param {!SessionContext} sessionContext
     * @param {int} playerId
     * @param {string} playerToken
     * @param {string} name
     */
    constructor(sessionContext, playerId, playerToken, name) {
        this.playerId = playerId;
        this.playerToken = playerToken;
        this._name = name;
        this.sessionContext = sessionContext;
    }

    get playEndpoint() {
        return `${this.sessionContext.endpointBase}/play/${this.playerId}/${this.playerToken}`;
    }

    /**
     * @returns {string}
     */
    get name() {
        return this._name;
    }
}

/**
 * @typedef {Object} HeldCard
 * @property {int} colour
 * @property {int} numValue
 */

/**
 * @typedef {Object} Player
 * @property {string} name
 * @property {int} playerId
 */

/**
 * @typedef {Object} ServerPlayerAction
 * @property {string} type - Type of action taken (one of ActionType)
 * @property {int} player_id - ID of player executing the action
 * @property {?int} colour - Colour index
 * @property {?int} num_value - Numeric value index
 * @property {?string} hint_positions - Positions to which a hint applies
 * @property {?int} hint_target - Player targeted by hint
 * @property {?int} hand_pos - Position of the card that was played
 * @property {?boolean} was_error - boolean indicating whether the last play was an error
 */


/**
 * @typedef {Object} HintAction
 * @property {int} targetPlayer
 * @property {boolean} isColourHint
 * @property {int} hintValue
 * @property {int[]} positions
 */

/**
 * @typedef {Object} CardAction
 * @property {int} colour
 * @property {int} numValue
 * @property {int} position
 * @property {boolean} wasError
 * @property {boolean} wasPlay
 */

/**
 * @typedef {Object} ParsedAction
 * @property {string} actionType
 * @property {int} actingPlayer
 * @property {!HintAction, !CardAction} action
 */

/**
 *
 * @param {ServerPlayerAction} serverPlayerAction
 * @return {?ParsedAction}
 */
function parseAction(serverPlayerAction) {
    if(!serverPlayerAction) {
        return null;
    }

    const {type: actionType, player_id: actingPlayer} = serverPlayerAction;

    /**
     * @param {string} key
     * @returns {boolean}
     */
    function keyMeaningful(key) {
        if(!serverPlayerAction.hasOwnProperty(key)) {
            return false;
        }
        return serverPlayerAction[key] !== null;
    }

    /**
     * @type {!HintAction, !CardAction}
     */
    let actionResult;
    let wasError = false;
    let wasPlay = false;
    switch(actionType) {
        case ActionType.HINT:
            /** @type {int[]} */
            let positions = [];
            if(serverPlayerAction.hint_positions) {
                positions = serverPlayerAction.hint_positions
                    .split(',').map(x => parseInt(x));
            }

            /** @type {int} */
            let value;
            let isColourHint;
            if(keyMeaningful('colour')) {
                value = serverPlayerAction.colour;
                isColourHint = true;
            } else if(keyMeaningful('num_value')) {
                value = serverPlayerAction.num_value;
                isColourHint = false;
            } else {
                throw "Invalid hint response";
            }

            actionResult = {
                isColourHint: isColourHint, hintValue: value,
                targetPlayer: serverPlayerAction.hint_target,
                positions: positions
            };
            break;
        case ActionType.PLAY:
            wasPlay = true;
            if(!keyMeaningful('was_error')) {
                throw "Validity of play not returned";
            }
            wasError = serverPlayerAction.was_error;
        case ActionType.DISCARD:
            if(!keyMeaningful('colour')
                || !keyMeaningful('num_value')
                || !keyMeaningful('hand_pos')) {
                throw "Invalid card action";
            }
            actionResult = {
                wasPlay: wasPlay, wasError: wasError,
                colour: serverPlayerAction.colour,
                numValue: serverPlayerAction.num_value,
                position: serverPlayerAction.hand_pos
            };
            break;
        default:
            throw `Invalid action type ${actionType}`
    }

    return {
        actionType: actionType, actingPlayer: actingPlayer,
        action: actionResult
    }
}

/**
 * @typedef {Object} ServerCard
 * @property {int} colour
 * @property {int} num_value
 */
/**
 * @typedef {Object} ServerPlayer
 * @property {string} name
 * @property {int} player_id
 * @property {(?ServerCard)[]} hand
 */

/**
 * @typedef {Object} ServerGameState
 * @property {string} created - Time when session was created
 * @property {ServerPlayer[]} players - List of players
 * @property {int} active_player - Currently active player
 * @property {int} status - State of the session
 * @property {int} cards_in_hand - No. of cards that players are allowed to hold
 * @property {int} errors_remaining - Errors remaining
 * @property {int} tokens_remaining - Tokens remaining
 * @property {int[]} current_fireworks - Current state of the fireworks
 * @property {boolean[]} used_hand_slots - Used slots in player's hand
 * @property {?ServerPlayerAction} last_action - Action taken by current player
 */

/**
 * @typedef {Object} GameStateUpdate
 * @property {boolean} gameStateAdvanced
 * @property {Player[]} playersJoining
 * @property {boolean} activePlayerChanged
 */
export class GameState {
    /**
     * @param {PlayerContext} playerContext
     */
    constructor(playerContext) {
        this._status = GameStatus.INITIAL;
        this._playerContext = playerContext;
        /** @type {Player[]} */
        this._playerList = [];

        /**
         * @type {Object.<int,(?HeldCard)[]>}
         * @private
         */
        this._handsByPlayerId = Object();
        /** @type {int[]} */
        this._currentFireworks = [];
        /** @type {boolean[]} */
        this._slotsInUse = [];
        /** @type {?int} */
        this._activePlayerId = null;

        /** @type {?ParsedAction} */
        this._currentAction = null;

        /** @type {int} */
        this._errorsRemaining = 0;
        /** @type {int} */
        this._tokensRemaining = 0;

        /** @type {int} */
        this._cardsInHand = 0;
    }

    /**
     * Update the game state with a response from the server.
     * @param {ServerGameState} serverUpdate
     * @return {GameStateUpdate}
     */
    updateState(serverUpdate) {
        let { status } = serverUpdate;
        let handsByPlayerId = this._handsByPlayerId;
        const newPlayerList = serverUpdate.players.map(
            function(serverPlayer) {
                let {name, player_id} = serverPlayer
                let result = {name: name, playerId: player_id};
                // update the player's hands
                if(serverPlayer.hand) {
                    handsByPlayerId[player_id] = serverPlayer.hand.map(
                        function(theCard) {
                            if(theCard === null) {
                                return null;
                            } else {
                                return {colour: theCard.colour, numValue: theCard.num_value};
                            }
                        }
                    );
                }
                return result;

            }
        );
        const oldIdSet = new Set(this._playerList.map(({playerId}) => playerId));
        const joining = newPlayerList.filter(({playerId}) => !oldIdSet.has(playerId));
        this._playerList = newPlayerList;

        let gameStateAdvanced = this._status !== status;
        let activePlayerChanged = false;
        switch(status) {
            case GameStatus.INITIAL:
                break;
            case GameStatus.GAME_OVER:
            case GameStatus.TURN_END:
                this._currentAction = parseAction(serverUpdate.last_action);
            case GameStatus.PLAYER_THINKING:
                this._currentFireworks = serverUpdate.current_fireworks;
                this._slotsInUse = serverUpdate.used_hand_slots;
                if(this._activePlayerId !== serverUpdate.active_player) {
                    activePlayerChanged = true;
                    this._activePlayerId = serverUpdate.active_player;
                }
                this._errorsRemaining = serverUpdate.errors_remaining;
                this._tokensRemaining = serverUpdate.tokens_remaining;
                this._cardsInHand = serverUpdate.cards_in_hand;

        }
        this._status = status;
        return {
            gameStateAdvanced: gameStateAdvanced, playersJoining: joining,
            activePlayerChanged: activePlayerChanged
        };
    }

    /**
     * @returns {Player[]}
     */
    get playerList() {
        return this._playerList;
    }

    /**
     * @returns {int}
     */
    get status() {
        return this._status;
    }

    /**
     * @returns {int}
     */
    get activePlayerId() {
        return this._activePlayerId;
    }

    /**
     * @returns {boolean}
     */
    get isCurrentlyActive() {
        return this._activePlayerId === this._playerContext.playerId;
    }

    /**
     * @return {int}
     */
    get tokensRemaining() {
        return this._tokensRemaining;
    }

    /**
     * @return {int}
     */
    get errorsRemaining() {
        return this._errorsRemaining;
    }

    /**
     * @return {int[]}
     */
    get currentFireworks() {
        return this._currentFireworks;
    }

    /**
     * @return {?ParsedAction}
     */
    get currentAction() {
        return this._currentAction;
    }

    /**
     * @return {boolean[]}
     */
    get slotsInUse() {
        return this._slotsInUse;
    }

    /**
     * @return {int}
     */
    get numCardsInHand() {
        return this._cardsInHand;
    }

    /**
     * @param {int} playerId
     * @return {(?HeldCard)[]}
     */
    cardsHeldBy(playerId) {
        if(!this._handsByPlayerId.hasOwnProperty(playerId)) {
            console.log(`Hand of ${playerId} not found`);
            return [];
        }
        return this._handsByPlayerId[playerId];
    }
}
