"""WebSocket handler for real-time game synchronization."""

import json
import uuid
import logging
from typing import Dict, Optional, Any, Set
from dataclasses import dataclass, field

from litestar import WebSocket, websocket
from litestar.exceptions import WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Game, Player, GameEvent, EventType
from app.utils.logging import error_log, log_exception_with_context

logger = logging.getLogger("Herald.WebSocket")


@dataclass
class GameRoom:
    """Tracks connected clients for a game."""
    game_code: str
    connections: Dict[uuid.UUID, WebSocket] = field(default_factory=dict)  # player_id -> websocket
    anonymous_connections: Set[WebSocket] = field(default_factory=set)  # Connections before "join" message
    
    async def broadcast(self, message: dict, exclude: Optional[uuid.UUID] = None) -> None:
        """Send message to all connected clients except excluded one."""
        disconnected = []
        disconnected_anon = []
        
        # Send to identified connections
        for player_id, ws in self.connections.items():
            if player_id != exclude:
                try:
                    await ws.send_json(message)
                except Exception as e:
                    logger.warning(f"Failed to send to player {player_id}: {e}")
                    disconnected.append(player_id)
        
        # Send to anonymous connections too
        for ws in self.anonymous_connections:
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send to anonymous connection: {e}")
                disconnected_anon.append(ws)
        
        # Clean up disconnected clients
        for player_id in disconnected:
            self.connections.pop(player_id, None)
        for ws in disconnected_anon:
            self.anonymous_connections.discard(ws)
    
    async def send_to(self, player_id: uuid.UUID, message: dict) -> bool:
        """Send message to a specific player."""
        ws = self.connections.get(player_id)
        if ws:
            try:
                await ws.send_json(message)
                return True
            except Exception as e:
                logger.warning(f"Failed to send to player {player_id}: {e}")
                self.connections.pop(player_id, None)
        return False
    
    def add_anonymous_connection(self, ws: WebSocket) -> None:
        """Add an anonymous connection (before player identifies)."""
        self.anonymous_connections.add(ws)
    
    def add_connection(self, player_id: uuid.UUID, ws: WebSocket) -> None:
        """Add a player connection (after identification)."""
        # Move from anonymous to identified
        self.anonymous_connections.discard(ws)
        self.connections[player_id] = ws
    
    def remove_connection(self, player_id: Optional[uuid.UUID], ws: WebSocket) -> None:
        """Remove a player connection."""
        if player_id:
            self.connections.pop(player_id, None)
        self.anonymous_connections.discard(ws)
    
    @property
    def connection_count(self) -> int:
        return len(self.connections) + len(self.anonymous_connections)


class GameRoomManager:
    """
    Manages WebSocket connections across all games.
    
    This is a singleton that tracks all active game rooms and their
    connected players.
    """
    
    def __init__(self):
        self._rooms: Dict[str, GameRoom] = {}
    
    def get_room(self, game_code: str) -> GameRoom:
        """Get or create a room for a game."""
        code = game_code.upper()
        if code not in self._rooms:
            self._rooms[code] = GameRoom(game_code=code)
        return self._rooms[code]
    
    def remove_room(self, game_code: str) -> None:
        """Remove a room when game ends."""
        self._rooms.pop(game_code.upper(), None)
    
    def get_all_rooms(self) -> Dict[str, GameRoom]:
        """Get all active rooms."""
        return self._rooms.copy()


# Global room manager instance
room_manager = GameRoomManager()


async def broadcast_to_game(game_code: str, message: dict, exclude_player_id: Optional[uuid.UUID] = None) -> None:
    """
    Broadcast a message to all connected clients in a game room.
    
    This can be called from REST API handlers to notify WebSocket clients
    of changes (e.g., player joined, game started, etc.)
    """
    room = room_manager.get_room(game_code)
    if room.connection_count > 0:
        await room.broadcast(message, exclude=exclude_player_id)
        logger.debug(f"Broadcast to game {game_code}: {message.get('type')}")


async def get_game_state(session: AsyncSession, code: str) -> Optional[dict]:
    """Fetch full game state for broadcasting."""
    stmt = (
        select(Game)
        .where(Game.code == code.upper())
        .options(
            selectinload(Game.players).selectinload(Player.units),
            selectinload(Game.objectives),
        )
    )
    result = await session.execute(stmt)
    game = result.scalar_one_or_none()
    
    if not game:
        return None
    
    # Build state dict
    players = []
    units = []
    
    for player in game.players:
        players.append({
            "id": str(player.id),
            "name": player.name,
            "color": player.color,
            "is_host": player.is_host,
            "is_connected": player.is_connected,
            "army_name": player.army_name,
            "starting_unit_count": player.starting_unit_count,
            "starting_points": player.starting_points,
            "has_finished_activations": player.has_finished_activations,
        })
        
        for unit in player.units:
            unit_dict = {
                "id": str(unit.id),
                "player_id": str(unit.player_id),
                "name": unit.name,
                "custom_name": unit.custom_name,
                "quality": unit.quality,
                "defense": unit.defense,
                "size": unit.size,
                "tough": unit.tough,
                "cost": unit.cost,
                "loadout": unit.loadout,
                "rules": unit.rules,
                "is_hero": unit.is_hero,
                "is_caster": unit.is_caster,
                "caster_level": unit.caster_level,
                "is_transport": unit.is_transport,
                "transport_capacity": unit.transport_capacity,
                "has_ambush": unit.has_ambush,
                "has_scout": unit.has_scout,
                "attached_to_unit_id": str(unit.attached_to_unit_id) if unit.attached_to_unit_id else None,
            }
            
            if unit.state:
                unit_dict["state"] = {
                    "id": str(unit.state.id),
                    "wounds_taken": unit.state.wounds_taken,
                    "models_remaining": unit.state.models_remaining,
                    "activated_this_round": unit.state.activated_this_round,
                    "is_shaken": unit.state.is_shaken,
                    "is_fatigued": unit.state.is_fatigued,
                    "deployment_status": unit.state.deployment_status.value,
                    "transport_id": str(unit.state.transport_id) if unit.state.transport_id else None,
                    "spell_tokens": unit.state.spell_tokens,
                    "limited_weapons_used": unit.state.limited_weapons_used,
                    "custom_notes": unit.state.custom_notes,
                }
            else:
                unit_dict["state"] = None
            
            units.append(unit_dict)
    
    objectives = [
        {
            "id": str(obj.id),
            "marker_number": obj.marker_number,
            "label": obj.label,
            "status": obj.status.value,
            "controlled_by_id": str(obj.controlled_by_id) if obj.controlled_by_id else None,
        }
        for obj in game.objectives
    ]
    
    return {
        "id": str(game.id),
        "code": game.code,
        "name": game.name,
        "game_system": game.game_system.value,
        "status": game.status.value,
        "current_round": game.current_round,
        "max_rounds": game.max_rounds,
        "current_player_id": str(game.current_player_id) if game.current_player_id else None,
        "first_player_next_round_id": str(game.first_player_next_round_id) if game.first_player_next_round_id else None,
        "players": players,
        "units": units,
        "objectives": objectives,
    }


@websocket("/ws/game/{code:str}")
async def game_websocket(
    socket: WebSocket,
    code: str,
    session: AsyncSession,
) -> None:
    """
    WebSocket endpoint for game synchronization.
    
    Protocol:
    - Client connects with player_id in query params or sends "join" message
    - Server sends full game state on connect
    - Client sends state updates, server broadcasts to other clients
    - Server sends events when other players make changes
    
    Message types (client -> server):
    - {"type": "join", "player_id": "uuid"}
    - {"type": "update_unit", "unit_id": "uuid", "changes": {...}}
    - {"type": "update_objective", "objective_id": "uuid", "changes": {...}}
    - {"type": "update_game", "changes": {...}}
    - {"type": "ping"}
    
    Message types (server -> client):
    - {"type": "state", "data": {...}}  - Full game state
    - {"type": "state_update", "data": {...}}  - Partial state update
    - {"type": "player_joined", "player": {...}}
    - {"type": "player_left", "player_id": "uuid"}
    - {"type": "unit_updated", "unit": {...}}
    - {"type": "objective_updated", "objective": {...}}
    - {"type": "game_updated", "game": {...}}
    - {"type": "pong"}
    - {"type": "error", "message": "..."}
    """
    await socket.accept()
    
    room = room_manager.get_room(code)
    player_id: Optional[uuid.UUID] = None
    
    # Add to anonymous connections immediately so we receive broadcasts
    room.add_anonymous_connection(socket)
    logger.info(f"WebSocket connected to game {code} (anonymous)")
    
    try:
        # Send initial game state
        game_state = await get_game_state(session, code)
        if not game_state:
            await socket.send_json({"type": "error", "message": f"Game '{code}' not found"})
            await socket.close()
            return
        
        await socket.send_json({"type": "state", "data": game_state})
        
        # Main message loop
        while True:
            try:
                data = await socket.receive_json()
            except json.JSONDecodeError:
                await socket.send_json({"type": "error", "message": "Invalid JSON"})
                continue
            
            msg_type = data.get("type")
            
            if msg_type == "join":
                # Register player connection
                try:
                    player_id = uuid.UUID(data.get("player_id"))
                    room.add_connection(player_id, socket)
                    
                    # Update player connection status in DB without lazy loads
                    stmt = select(Player.id, Player.name, Player.color).where(Player.id == player_id)
                    result = await session.execute(stmt)
                    row = result.first()
                    
                    if row:
                        # Mark connected
                        await session.execute(
                            Player.__table__.update()
                            .where(Player.id == player_id)
                            .values(is_connected=True)
                        )
                        await session.commit()
                        
                        # Notify others
                        await room.broadcast(
                            {
                                "type": "player_joined",
                                "player": {
                                    "id": str(row.id),
                                    "name": row.name,
                                    "color": row.color,
                                }
                            },
                            exclude=player_id
                        )
                        
                        logger.info(f"Player {row.name} joined game {code}")
                    else:
                        await socket.send_json({"type": "error", "message": "Player not found"})
                
                except (ValueError, TypeError):
                    await socket.send_json({"type": "error", "message": "Invalid player_id"})
            
            elif msg_type == "ping":
                await socket.send_json({"type": "pong"})
            
            elif msg_type == "request_state":
                # Client requesting full state refresh
                game_state = await get_game_state(session, code)
                if game_state:
                    await socket.send_json({"type": "state", "data": game_state})
            
            elif msg_type == "state_update":
                # Generic state update - just broadcast to others
                # The actual persistence is done via REST API
                # This is for instant UI sync
                await room.broadcast(
                    {"type": "state_update", "data": data.get("data", {})},
                    exclude=player_id
                )
            
            else:
                await socket.send_json({"type": "error", "message": f"Unknown message type: {msg_type}"})
    
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected for game {code}")
    
    except Exception as e:
        logger.exception(f"WebSocket error for game {code}: {e}")
    
    finally:
        # Clean up - remove from both anonymous and identified
        room.remove_connection(player_id, socket)
        
        if player_id:
            # Update player connection status
            try:
                # Mark disconnected without lazy loads
                await session.execute(
                    Player.__table__.update()
                    .where(Player.id == player_id)
                    .values(is_connected=False)
                )
                await session.commit()
                
                # Notify others
                await room.broadcast(
                    {"type": "player_left", "player_id": str(player_id)}
                )
                
                logger.info(f"Player {player_id} left game {code}")
            except Exception as e:
                error_log(
                    "Error updating player status on disconnect",
                    exc=e,
                    context={
                        "game_code": code,
                        "player_id": str(player_id) if player_id else None,
                    }
                )
        else:
            logger.info(f"Anonymous WebSocket disconnected from game {code}")
        
        # Remove empty rooms
        if room.connection_count == 0:
            room_manager.remove_room(code)


# Export the websocket handler for use in routes
websocket_handler = game_websocket
