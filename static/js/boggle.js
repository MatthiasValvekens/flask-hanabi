import * as boggleModel from './boggle-model.js';
export {RoundState} from './boggle-model.js';

/**
 * @typedef GUIStrings
 * @property {function} statusString - Return status text for the given round state
 * @property {string} notInDictionary - Return "not in dictionary" label
 * @property {string} duplicates - Return "duplicates" label
 * @property {string} approveButton - Return label for "approve" button
 */

/**
 * Boggle configuration parameters.
 *
 * @type {Object}
 * @property {string} apiBaseURL - Base URL for the Boggle API
 * @property {int} heartbeatTimeout - Timeout in milliseconds between state polls.
 * @property {GUIStrings} guiStrings - GUI string functions
 */
export const BOGGLE_CONFIG = {
    apiBaseURL: "",
    heartbeatTimeout: 3000,
    emptyTimerString: '-:--',
    statisticsEnabled: true,
    guiStrings: null
};


export const boggleController = function () {
    const RoundState = boggleModel.RoundState;

    /** @type {SessionContext} */
    let _sessionContext = null;

    /** @returns {!SessionContext} */
    function sessionContext() {
        if (_sessionContext === null)
            throw "No session context";
        return _sessionContext;
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
     * Call the Boggle API.
     * @callback callback
     * @param {!string} method - HTTP method to use
     * @param {!string} endpoint - Endpoint URL (relative to {@link BOGGLE_CONFIG.apiBaseURL})
     * @param {!object} data - Data to send in request body (will be JSONified)
     * @param callback - Response callback
     * @param errorHandler - Error callback
     * @returns {*}
     */
    function callBoggleApi(method, endpoint, data,
                           callback, errorHandler=ajaxErrorHandler) {
        return $.ajax({
            url: BOGGLE_CONFIG.apiBaseURL + endpoint,
            type: method,
            data: JSON.stringify(data),
            contentType: "application/json"
        }).done(callback).fail(errorHandler);
    }

    /**
     * Simpler Boggle API call for GET requests
     * @callback callback
     * @param {!string} endpoint
     * @param callback
     */
    function boggleAPIGet(endpoint, callback) {
        return $.getJSON(BOGGLE_CONFIG.apiBaseURL + endpoint, null, callback);
    }

    /**
     * Join the session specified in the session context.
     * @param {!string} name
     */
    function requestJoin(name) {

        function playerSetupCallback({player_id, player_token, name}) {
            let sess = sessionContext();
            _playerContext = Object.freeze(
                new boggleModel.PlayerContext(sess, player_id, player_token, name)
            );

            if(sess.isManager) {
                $('#manager-controls').show();
                $('#inv-token-display').val(
                    `${sess.sessionId}:${sess.saltToken}:${sess.invToken}`
                );
            }
            $('#start-section').hide();
            $('#game-section').show();

            gameState = new boggleModel.GameState(_playerContext);
            heartbeat();
        }
        return callBoggleApi(
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
        _sessionContext = Object.freeze(
            new boggleModel.SessionContext(parseInt(match[1]), match[2], match[3])
        );
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
     * Retrieve a list of dictionaries from the server
     */
    function getOptions() {
        $('#spawn-session').addClass("is-loading").prop("disabled", true);
        return boggleAPIGet( '/options', function({dictionaries, dice_configs}) {
            const dictSelect = $('#dictionary');
            dictionaries.forEach(
                function (dictionary) {
                    dictSelect.append(
                        `<option value="${dictionary}">${dictionary}</option>`
                    );
                }
            );
            const diceSelect = $('#dice-config');
            dice_configs.forEach(
                function(dice) {
                    diceSelect.append(
                        `<option value="${dice}">${dice}</option>`
                    )
                }
            );

            // set up the spawn session button
            $('#spawn-session').removeClass("is-loading")
                .click(function(){
                    /** @type {string} */
                    const name = retrievePlayerName();
                    if(name === null || !dictSelect.get(0).reportValidity()
                        || !diceSelect.get(0).reportValidity())
                        return;
                    const dictionary = $('#dictionary option:selected').val();
                    const diceConfig = $('#dice-config option:selected').val();
                    const mildScoring = $('#use-mild-scoring').is(':checked');
                    spawnSession(name, diceConfig, dictionary, mildScoring);
                }).prop("disabled", false);
            }
        )
    }

    /**
     * Create a session.
     * @param {!string} playerName
     * @param {!string} diceConfig
     * @param {?string} dictionary
     * @param {!boolean} mildScoring
     */
    function spawnSession(playerName, diceConfig, dictionary=null, mildScoring=false) {
        _sessionContext = null;

        let data = {dice_config: diceConfig, mild_scoring: mildScoring};
        if(dictionary !== null)
            data.dictionary = dictionary;
        return callBoggleApi(
            'post', '/session', data,
            function ({session_id, pepper, session_mgmt_token, session_token}) {
                _sessionContext = Object.freeze(
                    new boggleModel.SessionContext(session_id, pepper, session_token, session_mgmt_token)
                );
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
    let timeoutJob = null;
    /** @type {GameState} */
    let gameState = null;
    /** @type {?int} */
    let timerGoalValue = null;

    function timerControl(goalCallback=null) {
        let timerElement = document.getElementById('timer');
        if(timerGoalValue === null) {
            timerElement.innerText = BOGGLE_CONFIG.emptyTimerString;
            timeoutJob = null;
        }
        // add a fudge factor of half a second to mitigate timing issues with the server
        let delta = timerGoalValue + 500 - (new Date().getTime());
        if(delta <= 0) {
            if(goalCallback !== null)
                goalCallback();
            timerElement.innerText = BOGGLE_CONFIG.emptyTimerString;
            timerGoalValue = null;
            timeoutJob = null;
            // no need to reschedule the timer
            return;
        }
        let minutes = Math.floor(delta / (1000 * 60));
        let seconds = Math.floor((delta - minutes * 1000 * 60) / 1000);
        timerElement.innerText = `${minutes}:${seconds < 10? '0' : ''}${seconds}`;
        timeoutJob = setTimeout(() => timerControl(goalCallback), 1000);
    }

    function advanceRound() {
        // clean up word approval UI
        let wordApproval = $('#dict-invalid');
        wordApproval.off('click');
        $('#approve-button').remove();
        wordApproval.removeAttr('id');

        // clean up path hover listeners
        $('.player-scores').off();

        // reset timer
        timerGoalValue = null;
        if(timeoutJob !== null)
            clearTimeout(timeoutJob);

        let requestData = {'until_start': parseInt($('#round-announce-countdown').val())};
        return callBoggleApi('POST', sessionContext().mgmtEndpoint, requestData, function () {
            // disable until next heartbeat update
            $('#advance-round').prop("disabled", true);
        });
    }

    function submitWords() {
        if(gameState === null)
            throw "Cannot submit";
        if(gameState.roundSubmitted)
            return;
        const words = $('#words').val().trim().toUpperCase().split(/\s+/);
        let submission = {'round_no': gameState.roundNo, 'words': words};
        callBoggleApi('put', playerContext().playEndpoint, submission, forceRefresh);
        gameState.markSubmitted();
    }

    function getStatistics() {
        return boggleAPIGet(sessionContext().statisticsEndpoint, function({total_scores}){
            const playerListUl = $('#player-list ul');
            total_scores.forEach(function({player: {player_id}, total_score}) {
                    if(total_score > 0) {
                        const scoreSpan = playerListUl.find(
                            `li[data-player-id=${player_id}] .player-total-score`
                        );
                        scoreSpan.text(`(${total_score})`);
                    }
                }
            );
        });
    }

    function heartbeat() {
        if (gameState === null)
            throw "Game not running";

        if (heartbeatTimer !== null) {
            clearTimeout(heartbeatTimer);
            heartbeatTimer = null;
        }

        toggleBusy(true);
        // don't bother querying stats unless we're in the SCORED phase
        if(gameState.status === RoundState.SCORED)
            getStatistics();
        boggleAPIGet(playerContext().playEndpoint, function (response) {
            if (gameState === null) {
                console.log("Game ended while waiting for server response.");
                return;
            }
            let gameStateUpdate = gameState.updateState(response);
            let status = gameState.status;

            // update the player list
            let currentPlayer = playerContext().playerId;
            const playerListUl = $('#player-list ul');
            gameStateUpdate.playersLeaving.forEach(function({playerId}) {
                    playerListUl.find(`li[data-player-id="${playerId}"]`).remove();
                }
            );
            let playerListFmtd = gameStateUpdate.playersJoining.map(
                ({playerId, name}) =>
                    `<li data-player-id="${playerId}" ${playerId === currentPlayer ? 'class="me"' : ''}>
                    ${name} <span class="player-total-score"></span>
                    </li>`
            ).join('');
            playerListUl.append(playerListFmtd);


            // update the timer control, if necessary
            let noTimerRunning = timeoutJob === null;
            switch(status) {
                case RoundState.PRE_START:
                    // count down to start of round + fudge factor
                    timerGoalValue = gameState.roundStart;
                    if(noTimerRunning || gameStateUpdate.gameStateAdvanced) {
                        if(!noTimerRunning)
                            clearTimeout(timeoutJob);
                        timerControl(forceRefresh);
                    }
                    break;
                case RoundState.PLAYING:
                    // count down to end of round, and submit scores when timer reaches zero
                    timerGoalValue = gameState.roundEnd;
                    if(noTimerRunning || gameStateUpdate.gameStateAdvanced) {
                        if(!noTimerRunning)
                            clearTimeout(timeoutJob);
                        timerControl(submitWords);
                    }
                    break;
                case RoundState.SCORING:
                    // if we somehow end up killing the round-end timer, make sure we still submit
                    submitWords();
                default:
                    timerGoalValue = null;
            }
            // update status box
            $('#status-box').text(BOGGLE_CONFIG.guiStrings.statusString(status));

            // update availability of submission textarea
            $('#words-container').toggle(status === RoundState.PLAYING);
            if(gameStateUpdate.gameStateAdvanced) {
                $('#words').val('');
                touchInputController.clearInput();
            }

            // update board etc.
            if (gameStateUpdate.gameStateAdvanced) {
                let boggleGrid = $('#boggle');
                if(status !== RoundState.INITIAL && status !== RoundState.PRE_START) {
                    let boardHTML = gameState.boardState.map(
                        (row) =>
                            `<tr>${row.map((letter) => `<td>${letter}</td>`).join('')}</tr>`
                    ).join('');
                    boggleGrid.html(boardHTML);
                }
            }

            let manager = sessionContext().isManager;
            // update scores
            // FIXME do this more cleverly, without redrawing the entire thing every couple seconds
            //  THat would also remove the need for the manager-specific hack
            if(status === RoundState.SCORED && (!manager || gameStateUpdate.gameStateAdvanced)) {
                formatScores(gameState.scores);
                $('#score-section').show();
            }

            // update admin interface
            if(manager) {
                let canAdvance = (status !== RoundState.SCORING) && (status !== RoundState.PRE_START);
                $('#advance-round').prop("disabled", !canAdvance);
            }
        }).always(function() {
            // reschedule the timer, also on failures
            heartbeatTimer = setTimeout(heartbeat, BOGGLE_CONFIG.heartbeatTimeout);
            toggleBusy(false);
        });
    }

    function approveWords() {
        let requestData = {
            words: $('#dict-invalid .score .approved').toArray().map((el) => el.innerText)
        };
        let endpoint = sessionContext().mgmtEndpoint + '/approve_word';
        return callBoggleApi('patch', endpoint, requestData, function({scores}) {
            gameState.updateScores(scores);
            formatScores(gameState.scores);
        });
    }

    function approveSelectHandler() {
        let targ = $('span', this);
        if(targ.hasClass('approved')) {
            targ.addClass("is-danger");
            targ.removeClass("is-success approved");
        } else {
            targ.removeClass("is-danger");
            targ.addClass("is-success approved");
        }
        let candidates = $('#dict-invalid .score .approved').length;
        // only enable approve-button if there are words to approve
        $('#approve-button').prop("disabled", !candidates);
    }

    function clearHighlight() {
        $('#boggle td').removeAttr('data-order');
    }

    /** @param {int[][]} path */
    function highlightPath(path) {
        touchInputController.clearInput();
        for(const [ix, [row, col]] of path.entries()) {
            let cell = $(`#boggle tr:nth-child(${row + 1}) td:nth-child(${col + 1})`);
            cell.attr('data-order', ix + 1);
        }
    }

    // set path reveal onHover, de-hover clears the path display
    function highlightPathHandler(event) {
        if(event.type === 'mouseenter') {
            let pathData = $(this).attr('data-path');
            highlightPath(JSON.parse(pathData));
        } else clearHighlight();
    }

    /**
     * @param {RoundScoreSummary} roundScoreSummary
     */
    function formatScores(roundScoreSummary) {

        const approveButton = `
        <button class="button is-primary is-small" id="approve-button" disabled>
            ${BOGGLE_CONFIG.guiStrings.approveButton}
        </button>`;

        function fmtBad(str, colClass) {
            return `<div class="control score"><div class="tags has-addons" translate="no">
                        <span class="tag ${colClass}">${str}</span>
                    </div></div>`;
        }

        /**
         * @param {WordScore} wordScore
         */
        function fmtPathAttr(wordScore) {
            return wordScore.in_grid ? `data-path='${JSON.stringify(wordScore.path)}'` : '';
        }

        /** @param {WordScore} wordScore */
        function fmtWord(wordScore) {
            if(wordScore.score > 0) {
                return `<div class="control score" ${fmtPathAttr(wordScore)}><div class="tags has-addons" translate="no">
                        <span class="tag${wordScore.longest_bonus ? ' is-warning' : ''}">${wordScore.word}</span>
                        <span class="tag is-success">${wordScore.score}</span>
                    </div></div>`;
            } else if(wordScore.in_grid) {
                return `<div class="control score" ${fmtPathAttr(wordScore)}><div class="tags has-addons" translate="no">
                        <span class="tag ${wordScore.duplicate ? 'is-info' : 'is-danger'}">${wordScore.word}</span>
                    </div></div>`;
            } else {
                return fmtBad(wordScore.word, "is-dark");
            }
        }

        function fmtPlayer({playerId, name}) {
            let {total, words} = roundScoreSummary.wordsByPlayer(playerId);
            let wordList = words.map(fmtWord).join('');
            return `<div class="score-list-container"> <div class="field is-grouped is-grouped-multiline"  data-header="${name} (${total})">${wordList}</div></div>`
        }
        let scoreId = `scores-round-${roundScoreSummary.roundNo}`;
        $(`#${scoreId}`).remove();

        let duplicates = '';
        if(roundScoreSummary.duplicates.size) {
            duplicates = `<div class="score-list-container">
                <div class="field is-grouped is-grouped-multiline" data-header="${BOGGLE_CONFIG.guiStrings.duplicates}">
                    ${Array.from(roundScoreSummary.duplicates).map(
                (x) => fmtBad(x, "is-info")).join('')}
                </div>
            </div>`;
        }

        let invalidWords = '';
        if(roundScoreSummary.dictInvalidWords.size) {
            let manager = sessionContext().isManager;
            let coreFmt = Array.from(roundScoreSummary.dictInvalidWords)
                            .map((x) => fmtBad(x, "is-danger")).join('');
            invalidWords = `<div class="score-list-container" ${manager ? 'id="dict-invalid"' : ''}>
                <div class="field is-grouped is-grouped-multiline" data-header="${BOGGLE_CONFIG.guiStrings.notInDictionary}">${coreFmt}</div>
            </div>${manager ? approveButton: ''}`;
        }

        let structure = `
            <article class="media" id="${scoreId}">
                <figure class="media-left">
                    <p class="image is-64x64">
                        <span class="fas fa-trophy fa-3x"></span>
                    </p>
                </figure>
                <div class="media-content">
                    <div class="content">
                    <p>
                        <strong>#${roundScoreSummary.roundNo}</strong><br>
                    </p>
                    <div class="player-scores">
                    ${gameState.playerList.map(fmtPlayer).join('')}
                    </div>
                    <hr>${duplicates ? duplicates : ''} ${invalidWords ? invalidWords : ''}
                    </div>
                </div>
            </article> `;
        $('#score-container').prepend(structure);

        // add approval toggle
        if(sessionContext().isManager) {
            $('#dict-invalid').on("click", ".score", approveSelectHandler);
            $('#approve-button').click(approveWords);
        }

        $(`#${scoreId} .player-scores`).on('mouseenter mouseleave',
            '.score[data-path]', highlightPathHandler);
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

    function touchInput() {
        let currentRow = null;
        let currentCol = null;
        const cellsVisited = new Set();
        let inputCollected = [];
        function handleClick() {
            if(gameState === null || gameState.status !== RoundState.PLAYING)
                return;

            const colClicked = $(this).index();
            const rowClicked = $(this).parent().index();
            // sets only work with reference equality in JS (lol),
            // so strings it is
            const cellClicked = `${rowClicked}_${colClicked}`;
            if(currentRow !== null && currentCol !== null) {
                // check if this cell is eligible
                let seenBefore = cellsVisited.has(cellClicked);
                // the case where both of these are 0 is excluded by the seenBefore check
                let neighbour = (Math.abs(currentCol - colClicked) <= 1)
                        && (Math.abs(currentRow - rowClicked) <= 1);
                if(seenBefore || !neighbour)
                    return;
            } else clearInput();

            const charInCell = $(this).text().trim();
            cellsVisited.add(cellClicked);
            inputCollected.push(charInCell);
            currentRow = rowClicked;
            currentCol = colClicked;
            $(this).attr('data-order', inputCollected.length);
            $('#touch-input-buttons').css('visibility', 'visible');
        }

        function clearInput() {
            $('#touch-input-buttons').css('visibility', 'hidden');
            clearHighlight();
            inputCollected = [];
            cellsVisited.clear();
            currentCol = null;
            currentRow = null;
        }

        function appendInput() {
            let wordArea = $('#words');
            wordArea.val(`${wordArea.val()} ${inputCollected.join('')}`);
            clearInput();
        }

        return {
            clearInput: clearInput,  handleClick: handleClick, appendInput: appendInput
        }
    }
    const touchInputController = touchInput();


    return {
        getOptions: getOptions, joinExistingSession: joinExistingSession,
        advanceRound: advanceRound, touch: touchInputController
    }
}();


