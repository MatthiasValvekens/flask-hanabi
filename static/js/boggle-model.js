export const RoundState = Object.freeze({
    INITIAL: 0, PRE_START: 1,
    PLAYING: 2, SCORING: 3,
    SCORED: 4
});

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

    get statisticsEndpoint() {
        return `${this.endpointBase}/stats/${this.invToken}`;
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
 * @typedef {Object} Player
 * @property {string} name
 * @property {int} playerId
 */

/**
 * @typedef {Object} BoardSpec
 * @property {int} rows
 * @property {int} cols
 * @property {string[][]} dice
 */

/**
 * @typedef {Object} WordScore
 * @property {int} score - The point value of this word
 * @property {string} word - The word itself
 * @property {boolean} longest_bonus - Indicates whether the word receives a bonus for being the longest
 * @property {boolean} in_grid - Indicates whether the word appears in the grid
 * @property {boolean} duplicate - Indicates whether this word was submitted by multiple players
 * @property {boolean} dictionary_valid - Indicates whether this word appears in the dictionary
 * @property {int[][]} path - Path representing this word on the grid
 */

/**
 * @typedef {Object} PlayerScore
 * @property {{name: string, player_id: int}} player - Player being scored
 * @property {WordScore[]} words - Scores for each submitted word
 */

/**
 * @typedef {Object} ServerGameState
 * @property {string} created - Time when session was created
 * @property {{name: string, player_id: int}[]} players - List of players
 * @property {int} status - State of the session
 * @property {string} [round_start] - Start of current round
 * @property {string} [round_end] - End of current round
 * @property {BoardSpec} [board] - State of the current Boggle board
 * @property {PlayerScore[]} [scores] - Scores for the current round
 */

/**
 * @typedef {Object} GameStateUpdate
 * @property {boolean} gameStateAdvanced
 * @property {Player[]} playersJoining
 * @property {Player[]} playersLeaving
 */

export class GameState {
    /**
     * @param {PlayerContext} playerContext
     */
    constructor(playerContext) {
        this._status = RoundState.INITIAL;
        this._roundNo = 0;
        this._roundSubmitted = false;
        this._roundStart = null;
        this._roundEnd = null;
        this._boardCols = null;
        this._boardRows = null;
        this._boardState = null;
        this._scores = null;
        /** @type {Player[]} */
        this._playerList = [];
    }

    /**
     * Update the game state with a response from the server.
     * @param {ServerGameState} serverUpdate
     * @return {GameStateUpdate}
     */
    updateState(serverUpdate) {
        let { status, round_no: roundNo } = serverUpdate;
        // we need to track changes to the player list to avoid clobbering the statistics view
        //  at inopportune times
        const newPlayerList = serverUpdate.players.map(
            ({name, player_id}) => ({name: name, playerId: player_id})
        );
        const newIdSet = new Set(newPlayerList.map(({playerId}) => playerId));
        const oldIdSet = new Set(this._playerList.map(({playerId}) => playerId));
        const joining = newPlayerList.filter(({playerId}) => !oldIdSet.has(playerId));
        const leaving = this._playerList.filter(({playerId}) => !newIdSet.has(playerId));
        this._playerList = newPlayerList;

        let gameStateAdvanced = this._status !== status;
        if(this._roundNo !== roundNo) {
            this._roundSubmitted = false;
            gameStateAdvanced = true;
        }
        this._roundNo = roundNo;
        switch(status) {
            case RoundState.SCORED:
                this._scores = new RoundScoreSummary(roundNo, serverUpdate.scores);
            case RoundState.SCORING:
            case RoundState.PLAYING:
                this._boardCols = serverUpdate.board.cols;
                this._boardRows = serverUpdate.board.rows;
                this._boardState = serverUpdate.board.dice;
                this._roundEnd = moment.utc(serverUpdate.round_end).valueOf();
            case RoundState.PRE_START:
                this._roundStart = moment.utc(serverUpdate.round_start).valueOf();
            case RoundState.INITIAL:
                break;
        }
        this._status = status;
        return {gameStateAdvanced: gameStateAdvanced, playersLeaving: leaving, playersJoining: joining};
    }

    /**
     * @returns {Player[]}
     */
    get playerList() {
        return this._playerList;
    }

    /**
     * @returns {?string[][]}
     */
    get boardState() {
        return this._boardState;
    }

    /**
     * @returns {int}
     */
    get boardRows() {
        return this._boardRows;
    }

    /**
     * @returns {int}
     */
    get boardCols() {
        return this._boardCols;
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
    get roundNo() {
        return this._roundNo;
    }

    /**
     * @returns {int}
     */
    get roundStart() {
        return this._roundStart;
    }

    /**
     * @returns {int}
     */
    get roundEnd() {
        return this._roundEnd;
    }

    /**
     * @returns {boolean}
     */
    get roundSubmitted() {
        return this._roundSubmitted;
    }

    markSubmitted() {
        this._roundSubmitted = true;
    }

    /** @param {PlayerScore[]}scores */
    updateScores(scores) {
        this._scores = new RoundScoreSummary(this._roundNo, scores);
    }


    get scores() {
        return this._scores
    }
}

/**
 * @param {WordScore[]} scores
 * @returns {int}
 */
function sumScores(scores) {
    return scores.map(({score}) => score).reduce(
        (x, y) => x + y, 0
    )
}

export class RoundScoreSummary {
    /**
     * @param {int} roundNo
     * @param {PlayerScore[]} scores
     */
    constructor(roundNo, scores) {
        this._roundNo = roundNo;
        /** @type {Map<int, {total: int, words: WordScore[]}>} */
        this._wordsByPlayer = new Map(
            scores.map(({player: {player_id}, words }) =>
                [player_id, {total: sumScores(words), words: words}])
        );
        /** @type {Set<string>} */
        this._duplicates = new Set();

        // these are candidates to be approved manually
        /** @type {Set<string>} */
        this._dictInvalidWords = new Set();

        for(const {words} of scores) {
            for(const wordScore of words) {
                if(!wordScore.in_grid)
                    continue;
                if(wordScore.duplicate) {
                    this._duplicates.add(wordScore.word);
                }
                if(!wordScore.dictionary_valid) {
                    this._dictInvalidWords.add(wordScore.word);
                }
            }
        }


    }

    /** @returns {Set<string>} */
    get duplicates() {
        return this._duplicates;
    }

    /** @returns {Set<string>} */
    get dictInvalidWords() {
        return this._dictInvalidWords;
    }

    /**
     * @param {int} playerId
     * @returns {{total: int, words: WordScore[]}}
     */
    wordsByPlayer(playerId) {
        let result = this._wordsByPlayer.get(playerId);
        if (result === undefined || result === null) {
            return {total: 0, words: []};
        } else {
            return result;
        }

    }

    /** @returns {int} */
    get roundNo() {
        return this._roundNo;
    }
}