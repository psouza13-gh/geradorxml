import json
import os
import uuid
from typing import List, Optional
from app.models.client import Client

DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "clients.json")


def _load_raw() -> list:
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_raw(data: list) -> None:
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_clients() -> List[Client]:
    return [Client.from_dict(d) for d in _load_raw()]


def save_client(client: Client) -> None:
    data = _load_raw()
    for i, c in enumerate(data):
        if c["id"] == client.id:
            data[i] = client.to_dict()
            _save_raw(data)
            return
    data.append(client.to_dict())
    _save_raw(data)


def delete_client(client_id: str) -> None:
    data = [c for c in _load_raw() if c["id"] != client_id]
    _save_raw(data)


def new_client() -> Client:
    return Client(id=str(uuid.uuid4()), nome="", cnpj="")
