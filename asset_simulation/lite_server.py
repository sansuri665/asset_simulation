"""Local HTML/API host for the Main-6C lite fixed-fleet game."""
from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from threading import RLock
from urllib.parse import parse_qs, urlparse

from .model.freight_board.lite_game import LiteGameSession, VERSION as GAME_VERSION
from .server import AssetSimulationHandler


GAME_SERVICE_ID = "asset-simulation-main-6clite-game-v0.1"
_GAMES: dict[str, LiteGameSession] = {}
_GAMES_LOCK = RLock()


def clear_games() -> None:
    with _GAMES_LOCK:
        _GAMES.clear()


def create_game(seed: int = 42) -> LiteGameSession:
    game = LiteGameSession(seed=seed)
    with _GAMES_LOCK:
        _GAMES[game.game_id] = game
    return game


def get_game(game_id: str) -> LiteGameSession:
    with _GAMES_LOCK:
        game = _GAMES.get(game_id)
    if game is None:
        raise ValueError("unknown or expired game_id")
    return game


def load_game(checkpoint) -> LiteGameSession:
    game = LiteGameSession.restore(checkpoint)
    with _GAMES_LOCK:
        _GAMES[game.game_id] = game
    return game


class LiteGameHandler(AssetSimulationHandler):
    server_version = "AssetSimulationLite/0.1"

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 32_000_000:
            raise ValueError("request body must contain bounded JSON")
        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON request body must be an object")
        return payload

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path in {"/game", "/game.html"}:
                self._viewer_file("game.html")
                return
            if parsed.path == "/api/game":
                self._json(HTTPStatus.OK, {
                    "ok": True,
                    "serviceId": GAME_SERVICE_ID,
                    "gameVersion": GAME_VERSION,
                    "endpoints": [
                        "POST /api/game/new",
                        "POST /api/game/choose",
                        "POST /api/game/round",
                        "POST /api/game/next",
                        "GET /api/game/state?game_id=...",
                        "GET /api/game/save?game_id=...",
                        "POST /api/game/load",
                    ],
                })
                return
            if parsed.path in {"/api/game/state", "/api/game/save"}:
                query = parse_qs(parsed.query, keep_blank_values=True)
                game_id = query.get("game_id", [""])[0]
                game = get_game(game_id)
                if parsed.path.endswith("/save"):
                    self._json(HTTPStatus.OK, {"ok": True, "checkpoint": game.checkpoint()})
                else:
                    self._json(HTTPStatus.OK, game.public_state())
                return
            super().do_GET()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            body = self._read_json()
            if parsed.path == "/api/game/new":
                seed = body.get("seed", 42)
                game = create_game(seed)
                self._json(HTTPStatus.OK, game.public_state())
                return
            if parsed.path == "/api/game/load":
                game = load_game(body.get("checkpoint"))
                self._json(HTTPStatus.OK, game.public_state())
                return
            game = get_game(body.get("game_id", ""))
            if parsed.path == "/api/game/choose":
                result = game.choose_company(body["company_id"])
            elif parsed.path == "/api/game/round":
                result = game.submit_round(
                    body.get("action"),
                    use_advisor=bool(body.get("use_advisor", False)),
                    keep=bool(body.get("keep", False)),
                    round_token=body.get("round_token"),
                )
            elif parsed.path == "/api/game/next":
                result = game.next_turn(round_token=body.get("round_token"))
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self._json(HTTPStatus.OK, result)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve Main-6C lite fixed-fleet game and existing research views.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8784)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), LiteGameHandler)
    print(f"Main-6C lite game: http://{args.host}:{args.port}/game")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
