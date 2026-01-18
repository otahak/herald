/**
 * Herald Game Store
 * 
 * Reactive state management for game data with WebSocket sync.
 * Uses Vue 3 reactive() for state management (similar to Pinia).
 */

const GameStore = {
    /**
     * Get base path for API calls (empty for localhost, /herald for production)
     */
    getBasePath() {
        return (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
            ? ''
            : '/herald';
    },
    
    // Reactive state
    state: Vue.reactive({
        // Connection state
        isConnected: false,
        isLoading: false,
        error: null,
        
        // Current player info
        currentPlayerId: null,
        
        // Game data
        game: null,
        players: [],
        units: [],
        objectives: [],
        events: [],
        
        // WebSocket
        ws: null,
        reconnectAttempts: 0,
        maxReconnectAttempts: 5,
    }),
    
    // Computed getters
    getters: {
        // Get current player
        currentPlayer() {
            return GameStore.state.players.find(p => p.id === GameStore.state.currentPlayerId);
        },
        
        // Get opponent
        opponent() {
            return GameStore.state.players.find(p => p.id !== GameStore.state.currentPlayerId);
        },
        
        // Get units for a player
        playerUnits(playerId) {
            return GameStore.state.units.filter(u => u.player_id === playerId);
        },
        
        // Get current player's units
        myUnits() {
            return this.playerUnits(GameStore.state.currentPlayerId);
        },
        
        // Get opponent's units
        opponentUnits() {
            const opponent = this.opponent();
            return opponent ? this.playerUnits(opponent.id) : [];
        },
        
        // Is it my turn?
        isMyTurn() {
            return GameStore.state.game?.current_player_id === GameStore.state.currentPlayerId;
        },
        
        // Units in ambush
        ambushUnits() {
            return GameStore.state.units.filter(u => 
                u.state?.deployment_status === 'in_ambush'
            );
        },
        
        // Units in transport
        embarkedUnits(transportId) {
            return GameStore.state.units.filter(u => 
                u.state?.transport_id === transportId
            );
        },
        
        // Get VP for a player (from player object)
        playerVP(playerId) {
            const player = GameStore.state.players.find(p => p.id === playerId);
            return player?.victory_points || 0;
        },
        
        // Army health percentage for a player
        armyHealth(playerId) {
            const player = GameStore.state.players.find(p => p.id === playerId);
            if (!player || player.starting_unit_count === 0) return 1;
            
            const currentUnits = GameStore.state.units.filter(u => 
                u.player_id === playerId && 
                u.state?.deployment_status !== 'destroyed'
            ).length;
            
            return currentUnits / player.starting_unit_count;
        },
        
        // Is army at morale threshold?
        isMoraleThreshold(playerId) {
            return this.armyHealth(playerId) <= 0.5;
        },
    },
    
    // Actions
    actions: {
        /**
         * Create a new game
         */
        async createGame(name, playerName, playerColor, isSolo = false) {
            GameStore.state.isLoading = true;
            GameStore.state.error = null;
            
            try {
                const basePath = GameStore.getBasePath();
                const response = await fetch(`${basePath}/api/games`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name,
                        player_name: playerName,
                        player_color: playerColor,
                        is_solo: isSolo,
                    }),
                });
                
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to create game');
                }
                
                const game = await response.json();
                GameStore.state.game = game;
                GameStore.state.players = game.players;
                GameStore.state.currentPlayerId = game.players[0].id;
                
                return game.code;
            } catch (error) {
                GameStore.state.error = error.message;
                throw error;
            } finally {
                GameStore.state.isLoading = false;
            }
        },
        
        /**
         * Join an existing game
         */
        async joinGame(code, playerName, playerColor) {
            GameStore.state.isLoading = true;
            GameStore.state.error = null;
            
            try {
                const basePath = GameStore.getBasePath();
                const response = await fetch(`${basePath}/api/games/${code}/join`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        player_name: playerName,
                        player_color: playerColor,
                    }),
                });
                
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to join game');
                }
                
                const game = await response.json();
                GameStore.state.game = game;
                GameStore.state.players = game.players;
                GameStore.state.units = game.units || [];
                
                // Use the player ID returned by the server
                GameStore.state.currentPlayerId = game.your_player_id;
                Debug.log('Joined as player:', game.your_player_id);
                
                return game;
            } catch (error) {
                GameStore.state.error = error.message;
                throw error;
            } finally {
                GameStore.state.isLoading = false;
            }
        },
        
        /**
         * Fetch game state
         */
        async fetchGame(code) {
            // Only set isLoading if we don't already have game data (initial load)
            // If we're refreshing after an operation, don't show global loading spinner
            const isInitialLoad = !GameStore.state.game;
            if (isInitialLoad) {
                GameStore.state.isLoading = true;
                GameStore.state.error = null;
            }
            
            try {
                const basePath = GameStore.getBasePath();
                const response = await fetch(`${basePath}/api/games/${code}`);
                
                if (!response.ok) {
                    const error = await response.json();
                    const errorMessage = error.detail || 'Failed to fetch game';
                    
                    // Only set global error for initial load failures (critical)
                    // For refresh failures, just log and return current state
                    if (isInitialLoad) {
                        GameStore.state.error = errorMessage;
                        throw new Error(errorMessage);
                    } else {
                        Debug.error('Game refresh failed (non-critical):', errorMessage);
                        // Return current game state instead of throwing
                        return GameStore.state.game;
                    }
                }
                
                const game = await response.json();
                GameStore.state.game = game;
                GameStore.state.players = game.players;
                GameStore.state.units = game.units || [];
                GameStore.state.objectives = game.objectives || [];
                
                return game;
            } catch (error) {
                // Only set global error for initial load (critical)
                if (isInitialLoad) {
                    GameStore.state.error = error.message;
                }
                throw error;
            } finally {
                if (isInitialLoad) {
                    GameStore.state.isLoading = false;
                }
            }
        },
        
        /**
         * Import army from Army Forge
         */
        async importArmy(code, armyForgeUrl, playerId = null) {
            // Don't set isLoading for import - let the UI handle its own loading state
            // This prevents the global loading spinner from blocking the UI
            // Don't set global error state - handle errors locally
            
            try {
                // Use provided playerId or fall back to currentPlayerId
                const targetPlayerId = playerId || GameStore.state.currentPlayerId;
                
                const basePath = GameStore.getBasePath();
                const response = await fetch(`${basePath}/api/proxy/import-army/${code}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        army_forge_url: armyForgeUrl,
                        player_id: targetPlayerId,
                    }),
                });
                
                if (!response.ok) {
                    const error = await response.json();
                    const errorMessage = error.detail || error.message || `HTTP ${response.status}: Failed to import army`;
                    Debug.error('Army import failed:', errorMessage, error);
                    throw new Error(errorMessage);
                }
                
                const result = await response.json();
                
                // Refresh game state to get new units
                await this.fetchGame(code);
                
                // Broadcast to other players via WebSocket
                this.broadcastStateUpdate({ type: 'army_imported' });
                
                return result;
            } catch (error) {
                // Don't set global error state for import failures - these should be handled locally
                // Only log the error for debugging
                Debug.error('Army import failed (non-critical):', error.message);
                throw error;
            }
        },
        
        /**
         * Create a unit manually
         */
        async createUnitManually(code, unitData, playerId = null) {
            // Don't set isLoading for unit creation - let the UI handle its own loading state
            // This prevents the global loading spinner from blocking the UI
            
            try {
                // Use provided playerId or fall back to currentPlayerId
                const targetPlayerId = playerId || GameStore.state.currentPlayerId;
                
                const basePath = GameStore.getBasePath();
                const response = await fetch(`${basePath}/api/games/${code}/units/manual`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        player_id: targetPlayerId,
                        ...unitData,
                    }),
                });
                
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to create unit');
                }
                
                const result = await response.json();
                
                // Refresh game state to get new unit
                await this.fetchGame(code);
                
                // Fetch events for solo mode compatibility
                await this.fetchEvents();
                
                // Broadcast to other players via WebSocket
                this.broadcastStateUpdate({ type: 'unit_created' });
                
                return result;
            } catch (error) {
                // Don't set global error state for unit creation failures - these should be handled locally
                // Only log the error for debugging
                Debug.error('Unit creation failed (non-critical):', error.message);
                throw error;
            }
        },
        
        /**
         * Switch which player you're controlling (solo mode only)
         */
        switchPlayer(playerId) {
            if (!GameStore.state.game?.is_solo) {
                Debug.warn('Player switching is only available in solo mode');
                return;
            }
            
            const player = GameStore.state.players.find(p => p.id === playerId);
            if (!player) {
                Debug.error('Player not found:', playerId);
                return;
            }
            
            GameStore.state.currentPlayerId = playerId;
            Debug.log('Switched to controlling player:', player.name);
        },
        
        /**
         * Save game state (solo mode only)
         */
        async saveGame(code, saveName, description = null) {
            // Don't set isLoading for save game - let the UI handle its own loading state
            // Don't set global error state - handle errors locally
            
            try {
                const basePath = GameStore.getBasePath();
                const response = await fetch(`${basePath}/api/games/${code}/save`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        save_name: saveName,
                        description: description,
                    }),
                });
                
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to save game');
                }
                
                const result = await response.json();
                
                // Refresh game state
                await this.fetchGame(code);
                
                return result;
            } catch (error) {
                // Don't set global error state for save game failures - these should be handled locally
                Debug.error('Save game failed (non-critical):', error.message);
                throw error;
            }
        },
        
        /**
         * List all saves for a game (solo mode only)
         */
        async listSaves(code) {
            // Don't set isLoading for list saves - let the UI handle its own loading state
            // Don't set global error state - handle errors locally
            
            try {
                const basePath = GameStore.getBasePath();
                const response = await fetch(`${basePath}/api/games/${code}/saves`, {
                    method: 'GET',
                    headers: { 'Content-Type': 'application/json' },
                });
                
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to list saves');
                }
                
                const saves = await response.json();
                return saves;
            } catch (error) {
                // Don't set global error state for list saves failures - these should be handled locally
                Debug.error('List saves failed (non-critical):', error.message);
                throw error;
            }
        },
        
        /**
         * Load a saved game state (solo mode only)
         */
        async loadGame(code, saveId) {
            // Don't set isLoading for load game - let the UI handle its own loading state
            // Don't set global error state - handle errors locally
            
            try {
                const basePath = GameStore.getBasePath();
                const response = await fetch(`${basePath}/api/games/${code}/load`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        save_id: saveId,
                    }),
                });
                
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to load game');
                }
                
                const game = await response.json();
                
                // Refresh game state
                await this.fetchGame(code);
                
                return game;
            } catch (error) {
                // Don't set global error state for load game failures - these should be handled locally
                Debug.error('Load game failed (non-critical):', error.message);
                throw error;
            }
        },
        
        /**
         * Clear all units for the current player
         */
        async clearAllUnits(code, playerId) {
            // Don't set isLoading for clear units - let the UI handle its own loading state
            
            try {
                const basePath = GameStore.getBasePath();
                const response = await fetch(`${basePath}/api/games/${code}/players/${playerId}/units`, {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                });
                
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to clear units');
                }
                
                const result = await response.json();
                
                // Refresh game state to reflect cleared units
                await this.fetchGame(code);
                
                // Fetch events for solo mode compatibility
                await this.fetchEvents();
                
                // Broadcast to other players via WebSocket
                this.broadcastStateUpdate({ type: 'units_cleared' });
                
                return result;
            } catch (error) {
                // Don't set global error state for clear units failures - these should be handled locally
                Debug.error('Clear units failed (non-critical):', error.message);
                throw error;
            }
        },
        
        /**
         * Update unit state
         */
        async updateUnit(unitId, changes) {
            const code = GameStore.state.game?.code;
            if (!code) return;
            
            try {
                const basePath = GameStore.getBasePath();
                const response = await fetch(`${basePath}/api/games/${code}/units/${unitId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(changes),
                });
                
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to update unit');
                }
                
                const updatedUnit = await response.json();
                
                // Update local state
                const index = GameStore.state.units.findIndex(u => u.id === unitId);
                if (index !== -1) {
                    GameStore.state.units[index] = updatedUnit;
                }
                
                // Broadcast to other players
                this.broadcastStateUpdate({ 
                    type: 'unit_updated', 
                    unit: updatedUnit 
                });
                
                return updatedUnit;
            } catch (error) {
                // Don't set global error state for unit update failures - these should be handled locally
                Debug.error('Unit update failed (non-critical):', error.message);
                throw error;
            }
        },
        
        /**
         * Update objective state
         */
        async updateObjective(objectiveId, status, controlledById = null) {
            const code = GameStore.state.game?.code;
            if (!code) return;
            
            try {
                const basePath = GameStore.getBasePath();
                const response = await fetch(`${basePath}/api/games/${code}/objectives/${objectiveId}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        status,
                        controlled_by_id: controlledById,
                    }),
                });
                
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to update objective');
                }
                
                const updatedObjective = await response.json();
                
                // Update local state
                const index = GameStore.state.objectives.findIndex(o => o.id === objectiveId);
                if (index !== -1) {
                    GameStore.state.objectives[index] = updatedObjective;
                }
                
                // Fetch events for solo mode compatibility
                await this.fetchEvents();
                
                // Broadcast to other players
                this.broadcastStateUpdate({ 
                    type: 'objective_updated', 
                    objective: updatedObjective 
                });
                
                return updatedObjective;
            } catch (error) {
                // Don't set global error state for objective update failures - these should be handled locally
                Debug.error('Objective update failed (non-critical):', error.message);
                throw error;
            }
        },
        
        /**
         * Create objectives for a game
         */
        async createObjectives(count = 4) {
            const code = GameStore.state.game?.code;
            if (!code) return;
            
            try {
                const basePath = GameStore.getBasePath();
                const response = await fetch(`${basePath}/api/games/${code}/objectives`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ count }),
                });
                
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to create objectives');
                }
                
                const objectives = await response.json();
                GameStore.state.objectives = objectives;
                
                // Refresh game state to get objectives
                await this.fetchGame(code);
                
                return objectives;
            } catch (error) {
                // Don't set global error state for objective creation failures - these should be handled locally
                Debug.error('Objective creation failed (non-critical):', error.message);
                throw error;
            }
        },
        
        /**
         * Start the game
         */
        async startGame() {
            const code = GameStore.state.game?.code;
            if (!code) return;
            
            // Don't set isLoading for start game - let the UI handle its own loading state
            // Don't set global error state - handle errors locally (start game failures are non-critical)
            
            try {
                const basePath = GameStore.getBasePath();
                const response = await fetch(`${basePath}/api/games/${code}/start`, {
                    method: 'POST',
                });
                
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to start game');
                }
                
                const game = await response.json();
                GameStore.state.game = game;
                GameStore.state.players = game.players;
                GameStore.state.units = game.units || [];
                GameStore.state.objectives = game.objectives || [];
                
                // Broadcast to other players
                this.broadcastStateUpdate({ type: 'game_started' });
                
                return game;
            } catch (error) {
                // Don't set global error state for start game failures - these should be handled locally
                Debug.error('Start game failed (non-critical):', error.message);
                throw error;
            }
        },
        
        /**
         * Advance to next round
         */
        async advanceRound() {
            const code = GameStore.state.game?.code;
            if (!code) return;
            
            const newRound = (GameStore.state.game.current_round || 1) + 1;
            
            try {
                const basePath = GameStore.getBasePath();
                const response = await fetch(`${basePath}/api/games/${code}/state`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ current_round: newRound }),
                });
                
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to advance round');
                }
                
                // Refresh game state
                await this.fetchGame(code);
                
                // Fetch events for solo mode compatibility
                await this.fetchEvents();
                
                // Broadcast to other players
                this.broadcastStateUpdate({ type: 'round_advanced', round: newRound });
                
            } catch (error) {
                // Don't set global error state for round advancement failures - these should be handled locally
                Debug.error('Round advancement failed (non-critical):', error.message);
                throw error;
            }
        },
        
        /**
         * Fetch game events (action log)
         */
        async fetchEvents(limit = 50) {
            const code = GameStore.state.game?.code;
            if (!code) return;
            
            try {
                const basePath = GameStore.getBasePath();
                const response = await fetch(`${basePath}/api/games/${code}/events?limit=${limit}`);
                
                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || 'Failed to fetch events');
                }
                
                const events = await response.json();
                GameStore.state.events = events;
                
                return events;
            } catch (error) {
                // Don't set global error state for event fetch failures - these should be handled silently
                // Events are fetched in background, failures shouldn't disrupt the game
                Debug.error('Event fetch failed (non-critical, will retry):', error.message);
                // Return empty array instead of throwing to prevent UI disruption
                return [];
            }
        },
        
        /**
         * Connect to WebSocket for real-time updates (skips for solo games)
         */
        connectWebSocket(code) {
            // Skip WebSocket for solo games
            if (GameStore.state.game?.is_solo) {
                Debug.log('Skipping WebSocket connection for solo game');
                return;
            }
            
            if (GameStore.state.ws) {
                GameStore.state.ws.close();
            }
            
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            // Use /herald prefix only if we're on the production domain (not localhost)
            const basePath = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1' 
                ? '' 
                : '/herald';
            const wsUrl = `${protocol}//${window.location.host}${basePath}/ws/game/${code}`;
            
            const ws = new WebSocket(wsUrl);
            
            ws.onopen = () => {
                console.log('WebSocket connected');
                GameStore.state.isConnected = true;
                GameStore.state.reconnectAttempts = 0;
                
                // Join as player
                if (GameStore.state.currentPlayerId) {
                    ws.send(JSON.stringify({
                        type: 'join',
                        player_id: GameStore.state.currentPlayerId,
                    }));
                }
            };
            
            ws.onmessage = (event) => {
                try {
                    const message = JSON.parse(event.data);
                    this.handleWebSocketMessage(message);
                } catch (error) {
                    console.error('Failed to parse WebSocket message:', error);
                }
            };
            
            ws.onclose = () => {
                Debug.log('WebSocket disconnected');
                GameStore.state.isConnected = false;
                GameStore.state.ws = null;
                
                // Attempt to reconnect
                if (GameStore.state.reconnectAttempts < GameStore.state.maxReconnectAttempts) {
                    GameStore.state.reconnectAttempts++;
                    const delay = Math.min(1000 * Math.pow(2, GameStore.state.reconnectAttempts), 30000);
                    Debug.log(`Reconnecting in ${delay}ms...`);
                    setTimeout(() => this.connectWebSocket(code), delay);
                }
            };
            
            ws.onerror = (error) => {
                Debug.error('WebSocket error:', error);
            };
            
            GameStore.state.ws = ws;
        },
        
        /**
         * Handle incoming WebSocket messages
         */
        handleWebSocketMessage(message) {
            Debug.log('WS message:', message.type, message);
            
            switch (message.type) {
                case 'state':
                    // Full state update
                    const data = message.data;
                    GameStore.state.game = {
                        id: data.id,
                        code: data.code,
                        name: data.name,
                        game_system: data.game_system,
                        status: data.status,
                        current_round: data.current_round,
                        max_rounds: data.max_rounds,
                        current_player_id: data.current_player_id,
                    };
                    GameStore.state.players = data.players;
                    GameStore.state.units = data.units;
                    GameStore.state.objectives = data.objectives;
                    // Fetch events on state update
                    if (data.code) {
                        this.fetchEvents();
                    }
                    break;
                
                case 'state_update':
                    // Partial state update - refresh from server
                    console.log('State update received:', message.data);
                    if (GameStore.state.game?.code) {
                        this.fetchGame(GameStore.state.game.code);
                        this.fetchEvents();
                    }
                    break;
                
                case 'game_started':
                    // Game started - refresh state
                    console.log('Game started notification');
                    if (GameStore.state.game?.code) {
                        this.fetchGame(GameStore.state.game.code);
                        this.fetchEvents();
                    }
                    break;
                
                case 'player_joined':
                    // Add player to list if not already there
                    const existingPlayer = GameStore.state.players.find(p => p.id === message.player.id);
                    if (!existingPlayer) {
                        GameStore.state.players.push(message.player);
                    } else {
                        // Update existing player's connection status
                        existingPlayer.is_connected = true;
                    }
                    // Refresh full game state to get latest data
                    if (GameStore.state.game?.code) {
                        this.fetchGame(GameStore.state.game.code);
                        this.fetchEvents();
                    }
                    Debug.log('Player joined:', message.player.name);
                    break;
                
                case 'player_left':
                    // Update player connection status
                    const player = GameStore.state.players.find(p => p.id === message.player_id);
                    if (player) {
                        player.is_connected = false;
                    }
                    break;
                
                case 'pong':
                    // Keepalive response
                    break;
                
                case 'error':
                    console.error('WebSocket error:', message.message);
                    GameStore.state.error = message.message;
                    break;
            }
        },
        
        /**
         * Broadcast state update to other players
         */
        broadcastStateUpdate(data) {
            if (GameStore.state.ws && GameStore.state.ws.readyState === WebSocket.OPEN) {
                GameStore.state.ws.send(JSON.stringify({
                    type: 'state_update',
                    data,
                }));
            }
        },
        
        /**
         * Disconnect WebSocket
         */
        disconnectWebSocket() {
            if (GameStore.state.ws) {
                GameStore.state.ws.close();
                GameStore.state.ws = null;
            }
        },
        
        /**
         * Clear all state
         */
        reset() {
            this.disconnectWebSocket();
            GameStore.state.game = null;
            GameStore.state.players = [];
            GameStore.state.units = [];
            GameStore.state.objectives = [];
            GameStore.state.events = [];
            GameStore.state.currentPlayerId = null;
            GameStore.state.error = null;
        },
        
        /**
         * Save player identity to localStorage (persists across sessions)
         */
        savePlayerIdentity(gameCode, playerId, playerName) {
            const identities = JSON.parse(localStorage.getItem('herald_player_identities') || '{}');
            identities[gameCode.toUpperCase()] = {
                playerId,
                playerName,
                savedAt: new Date().toISOString(),
            };
            localStorage.setItem('herald_player_identities', JSON.stringify(identities));
            Debug.log(`Saved player identity: ${playerName} for game ${gameCode}`);
        },
        
        /**
         * Get saved player identity for a game
         */
        getSavedPlayerIdentity(gameCode) {
            const identities = JSON.parse(localStorage.getItem('herald_player_identities') || '{}');
            return identities[gameCode.toUpperCase()] || null;
        },
        
        /**
         * Clear player identity for a game
         */
        clearPlayerIdentity(gameCode) {
            const identities = JSON.parse(localStorage.getItem('herald_player_identities') || '{}');
            delete identities[gameCode.toUpperCase()];
            localStorage.setItem('herald_player_identities', JSON.stringify(identities));
        },
        
        /**
         * Initialize player from saved identity or return null if not found
         */
        async initializeFromSavedIdentity(gameCode) {
            const saved = this.getSavedPlayerIdentity(gameCode);
            if (!saved) return null;
            
            // Verify player still exists in game
            try {
                const game = await this.fetchGame(gameCode);
                const player = game.players.find(p => p.id === saved.playerId);
                
                if (player) {
                    GameStore.state.currentPlayerId = saved.playerId;
                    Debug.log(`Restored identity: ${player.name}`);
                    return player;
                } else {
                    // Player no longer in game, clear identity
                    this.clearPlayerIdentity(gameCode);
                    return null;
                }
            } catch (error) {
                console.error('Failed to restore identity:', error);
                return null;
            }
        },
    },
};

// Make it globally available
window.GameStore = GameStore;
