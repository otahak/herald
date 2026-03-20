/**
 * Computed-style getters for GameStore.state.
 */
Object.assign(GameStore.getters, {
    currentPlayer() {
        return GameStore.state.players.find(p => p.id === GameStore.state.currentPlayerId);
    },

    opponent() {
        return GameStore.state.players.find(p => p.id !== GameStore.state.currentPlayerId);
    },

    playerUnits(playerId) {
        return GameStore.state.units.filter(u => u.player_id === playerId);
    },

    myUnits() {
        return this.playerUnits(GameStore.state.currentPlayerId);
    },

    opponentUnits() {
        const opponent = this.opponent();
        return opponent ? this.playerUnits(opponent.id) : [];
    },

    isMyTurn() {
        return GameStore.state.game?.current_player_id === GameStore.state.currentPlayerId;
    },

    ambushUnits() {
        return GameStore.state.units.filter(u =>
            u.state?.deployment_status === 'in_ambush'
        );
    },

    embarkedUnits(transportId) {
        return GameStore.state.units.filter(u =>
            u.state?.transport_id === transportId
        );
    },

    playerVP(playerId) {
        const player = GameStore.state.players.find(p => p.id === playerId);
        return player?.victory_points || 0;
    },

    armyHealth(playerId) {
        const player = GameStore.state.players.find(p => p.id === playerId);
        if (!player || player.starting_unit_count === 0) return 1;

        const currentUnits = GameStore.state.units.filter(u =>
            u.player_id === playerId &&
            u.state?.deployment_status !== 'destroyed'
        ).length;

        return currentUnits / player.starting_unit_count;
    },

    isMoraleThreshold(playerId) {
        return this.armyHealth(playerId) <= 0.5;
    },
});
