/**
 * Herald game store shell (state + empty getters/actions buckets).
 * Load gameStore.getters.js then gameStore.actions.js after Vue is available.
 */
var GameStore = {
    getBasePath() {
        return (typeof window !== 'undefined' && window.HERALD_BASE_PATH !== undefined)
            ? window.HERALD_BASE_PATH
            : '';
    },

    state: Vue.reactive({
        isConnected: false,
        isLoading: false,
        error: null,
        currentPlayerId: null,
        game: null,
        players: [],
        units: [],
        objectives: [],
        events: [],
        ws: null,
        reconnectAttempts: 0,
        maxReconnectAttempts: 5,
    }),

    getters: {},
    actions: {},
};

window.GameStore = GameStore;
