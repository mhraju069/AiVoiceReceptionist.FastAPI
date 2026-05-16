import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional


KNOWN_CLIENTS_PATH = Path(__file__).resolve().parents[1] / "data" / "known_clients.json"


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 10:
        return f"1{digits}"
    return digits


@lru_cache(maxsize=1)
def _load_known_clients() -> list[dict]:
    with KNOWN_CLIENTS_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def find_known_client_by_phone(phone: str) -> Optional[dict]:
    normalized = normalize_phone(phone)
    if not normalized:
        return None

    for client in _load_known_clients():
        if normalize_phone(client.get("phone", "")) == normalized:
            return client
    return None


def profile_from_known_client(client: dict) -> dict:
    first_name = (client.get("first_name") or "").strip()
    last_name = (client.get("last_name") or "").strip()
    name = f"{first_name} {last_name}".strip() or "Client"
    plan = (client.get("plan") or "").strip()
    group = plan.upper() if plan.upper() in {"A", "B", "C", "D"} else ""
    client_type = f"Class {group} Client" if group else "Known VIP Client"

    return {
        "found": True,
        "contact_id": "",
        "name": name,
        "first_name": first_name,
        "last_name": last_name,
        "group": group,
        "client_type": client_type,
        "invoice_due": False,
        "phone": client.get("phone", ""),
        "email": client.get("email", ""),
        "business_name": client.get("business_name", ""),
        "notes": client.get("notes", ""),
        "source": "known_clients",
    }
