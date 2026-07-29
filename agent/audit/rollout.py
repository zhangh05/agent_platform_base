# agent/audit/rollout.py
"""RolloutRecorder — persist turn data for replay."""

import json


class RolloutRecorder:
    def __init__(self):
        self._store: dict = {}

    def persist_turn(self, turn, result):
        """Record a turn and its result for later replay."""
        key = turn.turn_id if hasattr(turn, 'turn_id') else str(id(turn))
        self._store[key] = {
            "turn": {
                "turn_id": getattr(turn, 'turn_id', ''),
                "user_input": turn.op.user_input if turn.op else "",
            },
            "result": {
                "ok": result.ok if result else False,
                "final_response": result.final_response if result else "",
                "warnings": result.warnings if result else [],
                "errors": result.errors if result else [],
            },
        }

    def read_turn(self, turn_id: str) -> dict:
        return self._store.get(turn_id)

    def to_json(self) -> str:
        return json.dumps(self._store, indent=2, ensure_ascii=False, default=str)
