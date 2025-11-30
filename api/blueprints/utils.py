from flask import jsonify
from werkzeug.exceptions import NotFound


def to_json(payload, status=200):
    return jsonify(payload), status


def not_found(resource: str = "Resource"):
    raise NotFound(f"{resource} não encontrado.")


def error_response(messages, status=422):
    if isinstance(messages, str):
        messages = [messages]
    return jsonify({"errors": messages}), status

