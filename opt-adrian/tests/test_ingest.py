"""Sprint 7 — ingestion + photo helpers. Pure, no FastAPI/Agno/LLM/network."""
from __future__ import annotations

from endpoints.ingest import (
    aggregate_burst,
    extract_payload,
    is_activity_event,
    is_superseded_by_outbound,
    last_inbound_burst,
    should_ignore,
)
from tools.photos import build_photo_payload_by_id, resolve_photos


# --- activity filter ------------------------------------------------------

def test_is_activity_event():
    assert is_activity_event({"type": "TYPE_ACTIVITY_CONTACT"}) is True
    assert is_activity_event({"messageType": "TYPE_ACTIVITY_OPPORTUNITY"}) is True
    assert is_activity_event({"type": "WhatsApp"}) is False


# --- burst aggregation ----------------------------------------------------

_HISTORY = [
    {"direction": "outbound", "body": "Olá! Como posso ajudar?"},
    {"direction": "inbound", "body": "quero um carro"},
    {"direction": "inbound", "body": "automatico"},
    {"direction": "inbound", "type": "TYPE_ACTIVITY_X", "body": "ignored"},
]


def test_last_inbound_burst_stops_at_outbound_and_skips_activity():
    assert last_inbound_burst(_HISTORY) == ["quero um carro", "automatico"]


# --- dedup temporal (supersede) ------------------------------------------

def test_superseded_when_agent_already_replied():
    # último real é outbound posterior ao inbound -> eco antigo -> supersede
    hist = [
        {"direction": "inbound", "body": "oi", "dateAdded": "2026-07-02T10:00:00Z"},
        {"direction": "outbound", "body": "Olá!", "dateAdded": "2026-07-02T10:00:05Z"},
    ]
    assert is_superseded_by_outbound(hist) is True


def test_not_superseded_when_inbound_is_latest():
    hist = [
        {"direction": "outbound", "body": "Olá!", "dateAdded": "2026-07-02T10:00:00Z"},
        {"direction": "inbound", "body": "quero ver", "dateAdded": "2026-07-02T10:00:05Z"},
    ]
    assert is_superseded_by_outbound(hist) is False


def test_supersede_ignores_activity_events():
    hist = [
        {"direction": "inbound", "body": "oi", "dateAdded": "2026-07-02T10:00:00Z"},
        {"direction": "outbound", "type": "TYPE_ACTIVITY_X", "dateAdded": "2026-07-02T10:00:09Z"},
    ]
    # o outbound mais recente é activity -> não conta -> não supersede
    assert is_superseded_by_outbound(hist) is False


def test_aggregate_burst_appends_incoming():
    out = aggregate_burst(_HISTORY, "ate 80 mil")
    assert out == "quero um carro\nautomatico\nate 80 mil"


def test_aggregate_burst_no_history():
    assert aggregate_burst([], "oi") == "oi"


def test_aggregate_burst_dedups_incoming_if_already_last():
    out = aggregate_burst(_HISTORY, "automatico")
    assert out == "quero um carro\nautomatico"  # not duplicated


# --- payload mapping ------------------------------------------------------

def test_extract_payload_maps_keys_and_audio():
    payload = {
        "messageId": "m1", "contactId": "c1", "conversationId": "conv1",
        "tags": ["agente-ia"], "message": "oi",
        "attachments": [
            {"type": "audio/ogg", "url": "http://a.ogg"},
            {"type": "image/png", "url": "http://b.png"},
        ],
        "fullName": "João", "source": "WhatsApp",
    }
    d = extract_payload(payload)
    assert d["message_id"] == "m1" and d["contact_id"] == "c1"
    assert d["conversation_id"] == "conv1" and d["text"] == "oi"
    assert d["audio_urls"] == ["http://a.ogg"]  # image excluded
    assert d["lead_name"] == "João"


def test_extract_payload_message_as_object():
    # GHL inbound sends message as an object, not a string
    d = extract_payload({
        "contactId": "c1", "message": {"body": "oi tudo bem", "type": "TYPE_WHATSAPP"},
        "tags": "agente-ia",
    })
    assert d["text"] == "oi tudo bem"
    assert d["contact_id"] == "c1"


def test_extract_payload_message_empty_object():
    d = extract_payload({"contactId": "c1", "message": {}, "Mensagem Completa": "fallback"})
    assert d["text"] == "fallback"


def test_should_ignore():
    assert should_ignore("", []) is True
    assert should_ignore("oi", []) is False
    assert should_ignore("", ["http://a.ogg"]) is False


# --- photos ---------------------------------------------------------------

_INV = [
    {"external_id": "1", "fotos": ["x.jpg", "y.jpg"]},
    {"external_id": "2", "fotos": []},
]


def test_build_photo_payload_by_id():
    assert build_photo_payload_by_id("1", _INV) == ["x.jpg", "y.jpg"]
    assert build_photo_payload_by_id("2", _INV) == []
    assert build_photo_payload_by_id("ghost", _INV) == []


def test_resolve_photos_dedup_and_order():
    assert resolve_photos(["1", "2", "1"], _INV) == ["x.jpg", "y.jpg"]


def test_resolve_photos_caps():
    inv = [
        {"external_id": "1", "fotos": [f"a{i}.jpg" for i in range(14)]},
        {"external_id": "2", "fotos": [f"b{i}.jpg" for i in range(14)]},
    ]
    # 1 per vehicle
    assert resolve_photos(["1", "2"], inv, per_vehicle=1) == ["a0.jpg", "b0.jpg"]
    # total cap
    assert len(resolve_photos(["1", "2"], inv, per_vehicle=4, total=6)) == 6


def test_photo_url_handles_string_and_dict_shapes():
    from tools.photos import _photo_url

    assert _photo_url("http://a.jpg") == "http://a.jpg"
    assert _photo_url({"url": "http://b.jpg"}) == "http://b.jpg"
    assert _photo_url({"src": "http://c.jpg"}) == "http://c.jpg"
    assert _photo_url({"nope": 1}) is None
    assert _photo_url("  ") is None


def test_build_photo_payload_object_shape():
    inv = [{"external_id": "9", "fotos": [{"url": "http://p1.jpg"}, "http://p2.jpg"]}]
    assert build_photo_payload_by_id("9", inv) == ["http://p1.jpg", "http://p2.jpg"]


def test_snapshot_marks_photo_availability():
    from tools.inventory import format_inventory_snapshot

    snap = format_inventory_snapshot([
        {"external_id": "1", "brand": "Jeep", "model": "Compass", "fotos": ["a.jpg"]},
        {"external_id": "2", "brand": "Fiat", "model": "Argo", "fotos": []},
    ])
    assert "fotos:1" in snap
    assert "sem_foto" in snap
