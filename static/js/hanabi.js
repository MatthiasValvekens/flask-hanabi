import * as hanabiModel from './hanabi-model.js';

export {GameStatus} from './hanabi-model.js';

/**
 * @typedef GUIStrings
 * @property {function} statusString - Return status text for the given round state
 * @property {string[]} colourNames
 * @property {string} cardPlayed
 * @property {string} cardDiscarded
 * @property {string} itsYourTurn
 * @property {string} playerUsedACard
 * @property {string} playerDiscardedACard
 * @property {string} playerMadeAMistake
 * @property {string} sidePanelHint
 * @property {string} playerGaveAHint
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

export function pseudoPythonInterpolate(fmt, obj) {
    // based on noice one-liner from here: https://code.djangoproject.com/ticket/4414
    return fmt.replace(/%\(\w+\)s/g, function(match){return String(obj[match.slice(2,-2)])});
}

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
            <div class="tile is-child box hanabi-player-box" data-player-id="${player.playerId}">
                <div>
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

        requestJoin(/** @type {!string} */ name).fail(function() {
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
            let statusString = HANABI_CONFIG.guiStrings.statusString(status);

            if(gameStateUpdate.activePlayerChanged) {
                $('.hanabi-player-box.active-player').removeClass('active-player');
                if(gameState.isCurrentlyActive) {
                    statusString = HANABI_CONFIG.guiStrings.itsYourTurn;
                } else {
                    let filter = `[data-player-id="${gameState.activePlayerId}"]`;
                    $(`#hanabi-other-players .hanabi-player-box${filter}`).addClass("active-player");
                }
            }
            if(gameStateUpdate.gameStateAdvanced) {
                $('#status-box').text(statusString);
                if(status !== GameStatus.INITIAL) {
                    updateFireworks();
                    updatePlayerHands();
                    updateCounters();
                }
                if(status === GameStatus.TURN_END && gameState.isCurrentlyActive) {
                    $('#end-turn-button').css('visibility', 'visible')
                        .prop('disabled', false);
                } else {
                    $('#end-turn-button').css('visibility', 'hidden')
                        .prop('disabled', true);
                }
                let action = gameState.currentAction;
                if(action === null) {
                    clearSidePanelCard();
                    $('#player-action-message').text('');
                } else if(action.actionType !== ActionType.HINT) {
                    processCardAction(/** @type{CardAction} */ action.action);
                } else {
                    processHint(/** @type {HintAction} */ action.action);
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
        const name = retrievePlayerName();
        if(name === null)
            return;
        spawnSession(/** @type {!string} */name);
    }

    function startGame() {
        $('#manager-start-game').prop("disabled", true);
        return callHanabiApi('POST', sessionContext().mgmtEndpoint, {}, function () {
            forceRefresh();
        });
    }

    /**
     * @param {string} title
     * @param {int} colour
     * @param {int} value
     */
    function setSidePanelCard(title, colour, value) {
        $('#side-panel p.subtitle').text(title).css('visibility', 'visible');
        $('#side-panel-card').html(formatCard(colour, value, true));
    }

    /**
     * @param {CardAction} cardAction
     */
    function processCardAction(cardAction) {
        let title, actionSummaryFmt;
        if(cardAction.wasPlay) {
            title = HANABI_CONFIG.guiStrings.cardPlayed;
            if(cardAction.wasError) {
                actionSummaryFmt = HANABI_CONFIG.guiStrings.playerMadeAMistake;
            } else {
                actionSummaryFmt = HANABI_CONFIG.guiStrings.playerUsedACard;
            }
        } else {
            title = HANABI_CONFIG.guiStrings.cardDiscarded;
            actionSummaryFmt = HANABI_CONFIG.guiStrings.playerDiscardedACard;
        }
        setSidePanelCard(
            title, cardAction.colour, cardAction.numValue
        );
        $('#player-action-message').html(
            pseudoPythonInterpolate(
                actionSummaryFmt, {
                    player: gameState.playerName(gameState.activePlayerId)
                }
            )
        );
    }

    /** @param {HintAction} hint */
    function processHint(hint) {
        $('#side-panel p.subtitle').text(
            HANABI_CONFIG.guiStrings.sidePanelHint
        ).css('visibility', 'visible');
        $('#player-action-message').html(
            pseudoPythonInterpolate(
                HANABI_CONFIG.guiStrings.playerGaveAHint,
                {
                    playerFrom: gameState.playerName(gameState.activePlayerId),
                    playerTo: gameState.playerName(hint.targetPlayer)
                }
            )
        );
        $('#side-panel-card').html(formatHintCard(hint));
    }

    /**
     * @param {HintAction} hint
     * @return {string}
     */
    function formatHintCard(hint) {
        let tagRmdr = "";
        let inner;
        if(hint.isColourHint) {
            tagRmdr = `data-hanabi-col=${hint.hintValue}`;
            inner = "?";
        } else {
            inner = hint.hintValue;
        }
        return `<div class="hanabi-card hanabi-state hanabi-card-highlighted" ${tagRmdr}>
                <span>${inner}</span>
        </div>`;
    }

    function clearSidePanelCard() {
        $('#side-panel p.subtitle').css('visibility', 'hidden');
        $('#side-panel-card').html(emptySlot);
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

    function updateCounters() {
        $('#errors-left').text(gameState.errorsRemaining);
        $('#tokens-left').text(gameState.tokensRemaining);
        if(gameState.tokensRemaining === 0) {
            $('#discard-button').prop('disabled', true);
        }
    }

    function updateFireworks() {
        $('#current-fireworks .hanabi-card').each(function (i) {
            let thisFireworkValue = gameState.currentFireworks[i];
            if(thisFireworkValue) {
                $(this).removeClass("hanabi-empty-slot");
                $(this).attr("data-hanabi-num-value", thisFireworkValue);
                $(this).html(`<span>${thisFireworkValue}</span>`);
            } else {
                $(this).addClass("hanabi-empty-slot");
            }
        });
    }

    /**
     * @param {int} theId
     * @param {boolean} processHighlights
     * @return {string}
     */
    function renderHandOfPlayer(theId, processHighlights) {
        return gameState.cardsHeldBy(theId).map(
            function(card, index) {
                let highlight = false;
                if(processHighlights) {
                    let act = gameState.currentAction;
                    // highlight if the card is targeted by a hint
                    highlight = act && act.actionType === ActionType.HINT
                        && act.action.targetPlayer === theId
                        && act.action.positions.includes(index);
                }
                if(card === null) {
                    return emptySlot;
                } else {
                    let {colour, numValue} = card;
                    return formatCard(colour, numValue, highlight);
                }
            }
        ).join('');
    }

    function updatePlayerHands() {
        let playerHandFmt = gameState.slotsInUse.map(
            function(inUse, index) {
                if(!inUse) {
                    return emptySlot;
                }
                let act = gameState.currentAction;
                let highlight = act && act.actionType === ActionType.HINT
                                && act.action.targetPlayer === playerContext().playerId
                                && act.action.positions.includes(index);
                if(highlight) {
                    return formatHintCard(/** @type {HintAction} */ act.action);
                } else {
                    return `<div class="hanabi-card hanabi-state">
                        <span>?</span>
                    </div>`
                }
            }
        );
        $('#player-hand').html(playerHandFmt);
        $('#hanabi-other-players .hanabi-player-box').each(
            function() {
                let theId = parseInt($(this).attr('data-player-id'));
                let hand = renderHandOfPlayer(theId, true);
                $(this).find('.hanabi-card-list').html(hand);
            }
        )
    }

    function handleHintModal() {
        if(gameState.isCurrentlyActive && gameState.status === GameStatus.PLAYER_THINKING) {
            const hintModal = $('#give-hint-modal');
            const theId = parseInt($(this).attr('data-player-id'));
            hintModal.attr('data-target-id', theId);
            const name = gameState.playerName(theId);
            $('#hint-recipient').text(name);
            hintModal.find('.hanabi-card-list').html(renderHandOfPlayer(theId, false));
            hintModal.addClass('is-active');
        }
    }

    function handleCardPlay() {
        if(gameState.isCurrentlyActive && gameState.status === GameStatus.PLAYER_THINKING) {
            const cardPosition = $(this).index();
            const playCardModal = $('#play-card-modal');
            playCardModal.attr('data-card-position', cardPosition);
            playCardModal.addClass('is-active');
        }
    }

    /**
     * @param {boolean} discard
     */
    function executeCardAction(discard) {
        const playCardModal = $('#play-card-modal');
        const position = playCardModal.attr('data-card-position');
        playCardModal.removeClass('is-active');
        callHanabiApi(
            'post', playerContext().playEndpoint, {
                type: discard ? ActionType.DISCARD : ActionType.PLAY,
                position: position
            }, forceRefresh
        );
    }

    function submitHint(isColourHint) {
        const giveHintModal = $('#give-hint-modal');
        const targetId = parseInt(giveHintModal.attr('data-target-id'));
        giveHintModal.removeClass('is-active');
        let hintData = {type: ActionType.HINT, hint_target: targetId};
        hintData[isColourHint ? 'colour' : 'num_value'] = parseInt($('#hint-input').val());
        callHanabiApi(
            'post', playerContext().playEndpoint, hintData,
            forceRefresh
        );

    }

    function endTurn() {
        callHanabiApi(
            'post', playerContext().playEndpoint + '/advance', {},
            forceRefresh
        );
    }

    return {
        joinExistingSession: joinExistingSession,
        triggerSessionSpawn: triggerSessionSpawn, startGame: startGame,
        handleHintModal: handleHintModal, handleCardPlay: handleCardPlay,
        executePlayAction: (() => executeCardAction(false)),
        executeDiscardAction: (() => executeCardAction(true)),
        submitNumValueHint: (() => submitHint(false)),
        submitColourHint: (() => submitHint(true)),
        endTurn: endTurn
    }
}();
