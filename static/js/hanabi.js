import * as hanabiModel from './hanabi-model.js';
export {GameStatus} from './hanabi-model.js';

/**
 * @typedef GUIStrings
 * @property {function} statusString - Return status text for the given round state
 * @property {string} cardPlayed
 * @property {string} cardDiscarded
 */

/**
 * Hanabi configuration parameters.
 *
 * @type {Object}
 * @property {string} apiBaseURL - Base URL for the Hanabi API
 * @property {int} heartbeatTimeout - Timeout in milliseconds between state polls.
 * @property {GUIStrings} guiStrings - GUI string functions
 */
export const HANABI_CONFIG = {
    apiBaseURL: "",
    heartbeatTimeout: 3000,
    guiStrings: null
};

export const hanabiController = function () {
    const GameStatus = hanabiModel.GameStatus;
    const ActionType = hanabiModel.ActionType;

    const emptySlot = "<div class=\"hanabi-card hanabi-state hanabi-empty-slot\"></div>";

    /** @type {SessionContext} */
    let _sessionContext = null;

    /** @returns {!SessionContext} */
    function sessionContext() {
        if (_sessionContext === null)
            throw "No session context";
        return _sessionContext;
    }

    /**
     * @param {Player} player
     * @return {string}
     */
    function formatOtherPlayerView(player) {
        if(player.playerId === playerContext().playerId) {
            return "";
        }
        return `<div class="tile is-parent">
            <div class="tile is-child box hanabi-player-box">
                <div class="hanabi-player-box" data-player-id="${player.playerId}">
                    <p class="title is-6">${player.name}</p>
                    <div class="hanabi-card-list">
                    </div>
                </div>
            </div>
        </div>`;
    }

    /** @type {PlayerContext} */
    let _playerContext = null;

    /** @returns {!PlayerContext} */
    function playerContext() {
        if (_playerContext === null)
            throw "No player context";
        return _playerContext;
    }

    function ajaxErrorHandler(response) {
        if (!('responseJSON' in response)) {
            console.log("Unknown error on API call")
        }
        let {responseJSON: {error}, status} = response;
        console.log(`API Error ${status}: ${error}`);
    }

    /**
     * Call the Hanabi API.
     * @callback callback
     * @param {!string} method - HTTP method to use
     * @param {!string} endpoint - Endpoint URL (relative to {@link HANABI_CONFIG.apiBaseURL})
     * @param {!object} data - Data to send in request body (will be JSONified)
     * @param callback - Response callback
     * @param errorHandler - Error callback
     * @returns {*}
     */
    function callHanabiApi(method, endpoint, data,
                           callback, errorHandler=ajaxErrorHandler) {
        return $.ajax({
            url: HANABI_CONFIG.apiBaseURL + endpoint,
            type: method,
            data: JSON.stringify(data),
            contentType: "application/json"
        }).done(callback).fail(errorHandler);
    }

    /**
     * Simpler Hanabi API call for GET requests
     * @callback callback
     * @param {!string} endpoint
     * @param callback
     */
    function hanabiAPIGet(endpoint, callback) {
        return $.getJSON(HANABI_CONFIG.apiBaseURL + endpoint, null, callback);
    }

    /**
     * Join the session specified in the session context.
     * @param {!string} name
     */
    function requestJoin(name) {

        function playerSetupCallback({player_id, player_token, name}) {
            let sess = sessionContext();
            _playerContext = new hanabiModel.PlayerContext(
                sess, player_id, player_token, name
            )

            if(sess.isManager) {
                $('#manager-controls').show();
                $('#inv-token-display').val(
                    `${sess.sessionId}:${sess.saltToken}:${sess.invToken}`
                );
            }
            $('#start-section').hide();
            $('#game-section').show();

            gameState = new hanabiModel.GameState(_playerContext);
            heartbeat();
        }
        return callHanabiApi(
            'post', sessionContext().joinEndpoint,
            {'name': name}, playerSetupCallback
        );
    }

    /** @returns {?string} */
    function retrievePlayerName() {
        const input = $('#player-name-input');
        if(!input.get(0).reportValidity()) {
            input.addClass("is-danger");
            return null;
        }
        input.removeClass("is-danger");
        return input.val();
    }

    function joinExistingSession() {
        const name = retrievePlayerName();
        if(name === null)
            return;

        // parse invitation token
        const invToken = $('#inv-token');
        const match = invToken.val().match(/^(\d+):([0-9a-f]{16}):([0-9a-f]{20})$/);
        if(match === null) {
            invToken.addClass("is-danger");
            $('#inv-token-error').show();
            return;
        }
        _sessionContext = new hanabiModel.SessionContext(parseInt(match[1]), match[2], match[3])
        $('#join-session').addClass("is-loading").prop("disabled", true);

        requestJoin(name).fail(function() {
            invToken.addClass("is-danger");
            $('#inv-token-error').show();
            _sessionContext = null;
        }).done(function() {
            invToken.removeClass("is-danger");
            $('#inv-token-error').hide();
        }).always(function(){
            $('#join-session').removeClass("is-loading").prop("disabled", false);
        });
    }

    /**
     * Create a session.
     * @param {!string} playerName
     */
    function spawnSession(playerName) {
        _sessionContext = null;

        return callHanabiApi(
            'post', '/session', {},
            function ({session_id, pepper, session_mgmt_token, session_token}) {
                _sessionContext = new hanabiModel.SessionContext(
                    session_id, pepper, session_token, session_mgmt_token
                )
                requestJoin(playerName);
            }
        );
    }

    /**
     * Toggle the global busy indicator
     * @param {!boolean} busy
     */
    function toggleBusy(busy) {
        if(busy)
            $('#loading-icon').show();
        else
            $('#loading-icon').hide();
    }

    let heartbeatTimer = null;
    /** @type {GameState} */
    let gameState = null;
    /** @type {?int} */

    function heartbeat() {
        if (gameState === null)
            throw "Game not running";

        if (heartbeatTimer !== null) {
            clearTimeout(heartbeatTimer);
            heartbeatTimer = null;
        }

        toggleBusy(true);
        hanabiAPIGet(playerContext().playEndpoint, function (response) {
            if (gameState === null) {
                console.log("Game ended while waiting for server response.");
                return;
            }
            let gameStateUpdate = gameState.updateState(response);
            let status = gameState.status;

            // update the player list
            let currentPlayer = playerContext().playerId;
            const playerListUl = $('#player-list ul');
            let playerListFmtd = gameStateUpdate.playersJoining.map(
                ({playerId, name}) =>
                    `<li data-player-id="${playerId}" ${playerId === currentPlayer ? 'class="me"' : ''}>
                    ${name}
                    </li>`
            ).join('');
            let playerCardViews = gameStateUpdate.playersJoining.map(formatOtherPlayerView).join('');
            playerListUl.append(playerListFmtd);
            $('#hanabi-other-players').append(playerCardViews);
            // update status box
            $('#status-box').text(HANABI_CONFIG.guiStrings.statusString(status));

            if(status !== GameStatus.INITIAL) {
                updateFireworks();
                updatePlayerHands();
            }
            let action = gameState.currentAction;
            if(status === GameStatus.TURN_END && action !== null) {
                if(action.actionType === ActionType.HINT) {
                    // TODO render hint
                } else {
                    /** @type {string} */
                    let title;
                    if(action.actionType === ActionType.PLAY) {
                        title = HANABI_CONFIG.guiStrings.cardPlayed;
                    } else {
                        title = HANABI_CONFIG.guiStrings.cardDiscarded;
                    }
                    setSidePanel(
                        title, action.action.colour, action.action.numValue
                    );
                }
            }

            if(gameState.playerList.length >= 2 && status === GameStatus.INITIAL) {
                $('#manager-start-game').prop("disabled", false);
            }
        }).always(function() {
            // reschedule the timer, also on failures
            heartbeatTimer = setTimeout(heartbeat, HANABI_CONFIG.heartbeatTimeout);
            toggleBusy(false);
        });
    }

    /**
     * Force a heartbeat call, unless one is already happening now
     */
    function forceRefresh() {
        if(heartbeatTimer !== null) {
            clearTimeout(heartbeatTimer);
            heartbeat();
        }
    }

    function triggerSessionSpawn() {
        /** @type {string} */
        const name = retrievePlayerName();
        if(name === null)
            return;
        spawnSession(name);
    }

    function startGame() {
        $('#advance-round').prop("disabled", true);
        return callHanabiApi('POST', sessionContext().mgmtEndpoint, {}, function () {
            forceRefresh();
        });
    }

    /**
     * @param {string} title
     * @param {int} colour
     * @param {int} value
     */
    function setSidePanel(title, colour, value) {
        $('#side-panel').html(
            `<p class="subtitle">${title}</p>${formatCard(colour, value, true)}`
        );
    }

    /**
     * @param {int} colour
     * @param {int} value
     * @param {boolean=false} highlight
     * @return {string}
     */
    function formatCard(colour, value, highlight=false) {
        let hlCls = highlight ? " hanabi-card-highlighted" : "";
        return `<div class="hanabi-card hanabi-state${hlCls}" data-hanabi-col="${colour}" data-hanabi-num-value="${value}">
                <span>${value}</span>
            </div>`
    }


    function updateFireworks() {
        $('#current-fireworks').each(function (i) {
            let thisFireworkValue = gameState.currentFireworks[i];
            if(thisFireworkValue) {
                $(this).removeClass("hanabi-empty-slot");
                $(this).prop("data-hanabi-num-value", thisFireworkValue);
                $(this).html(`<span>${thisFireworkValue}</span>`);
            } else {
                $(this).addClass("hanabi-empty-slot");
            }
        });
    }

    function updatePlayerHands() {
        $('#hanabi-other-players .hanabi-player-box').each(
            function() {
                let theId = parseInt($(this).attr('data-player-id'));
                let hand = gameState.cardsHeldBy(theId).map(
                    function(card) {
                        if(card === null) {
                            return emptySlot;
                        } else {
                            let {colour, numValue} = card;
                            return formatCard(colour, numValue);
                        }
                    }
                ).join('');
                $(this).find('.hanabi-card-list').html(hand);
            }
        )
    }

    return {
        joinExistingSession: joinExistingSession,
        triggerSessionSpawn: triggerSessionSpawn, startGame: startGame
    }
}();