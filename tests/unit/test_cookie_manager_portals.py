"""Testes unitários para status de portais (LinkedIn/Gupy/Indeed), merge de storage_state e rota connect."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from web.app import app
from web.cookie_manager import (
    compute_portal_statuses,
    get_cookies_info,
    merge_storage_states,
    save_cookies,
)


def test_compute_portal_statuses_detects_active_and_expired_cookies():
    now = 1_800_000_000.0
    cookies = [
        {"name": "li_at", "domain": ".linkedin.com", "path": "/", "expires": now + 86400},
        {"name": "_gupy_session", "domain": ".gupy.io", "path": "/", "expires": now - 60},  # expirado
    ]
    origins = [
        {
            "origin": "https://portal.gupy.io",
            "localStorage": [{"name": "oidc.user:candidate", "value": "tok123"}],
        }
    ]

    statuses = compute_portal_statuses(cookies, origins, now_ts=now)

    assert statuses["linkedin"]["connected"] is True
    assert statuses["linkedin"]["cookie_count"] == 1
    assert statuses["gupy"]["connected"] is True  # localStorage com token ainda autenticado
    assert statuses["gupy"]["origins_count"] == 1
    assert statuses["indeed"]["connected"] is False


def test_merge_storage_states_preserves_previous_portals():
    existing = {
        "cookies": [
            {"name": "li_at", "domain": ".linkedin.com", "path": "/", "value": "old_li"},
            {"name": "JSESSIONID", "domain": ".linkedin.com", "path": "/", "value": "jsess"},
        ],
        "origins": [{"origin": "https://www.linkedin.com", "localStorage": [{"name": "a", "value": "1"}]}],
    }
    incoming = {
        "cookies": [
            {"name": "_gupy_session", "domain": ".gupy.io", "path": "/", "value": "gupy123"},
        ],
        "origins": [{"origin": "https://portal.gupy.io", "localStorage": [{"name": "token", "value": "t"}]}],
    }

    merged = merge_storage_states(existing, incoming)
    cookie_names = {c["name"] for c in merged["cookies"]}
    origin_urls = {o["origin"] for o in merged["origins"]}

    assert {"li_at", "JSESSIONID", "_gupy_session"} <= cookie_names
    assert {"https://www.linkedin.com", "https://portal.gupy.io"} <= origin_urls


def test_save_cookies_merges_with_existing_file(tmp_path: Path, monkeypatch):
    storage_file = tmp_path / "storage_state.json"
    storage_file.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "li_at",
                        "domain": ".linkedin.com",
                        "path": "/",
                        "value": "li_cookie",
                        "expires": time.time() + 3600,
                    }
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("web.cookie_manager.get_storage_state_path", lambda: storage_file)

    res = save_cookies(
        {
            "cookies": [
                {
                    "name": "_gupy_session",
                    "domain": ".gupy.io",
                    "path": "/",
                    "value": "gupy_cookie",
                    "expires": time.time() + 3600,
                }
            ],
            "origins": [],
        },
    )

    assert res["has_session"] is True
    assert res["cookie_count"] == 2
    info = get_cookies_info()
    assert info["portals"]["linkedin"]["connected"] is True
    assert info["portals"]["gupy"]["connected"] is True
    assert "LinkedIn" in info["known_portals"]
    assert "Gupy" in info["known_portals"]


def test_connect_portal_endpoint_launches_portal_and_validates_portal_name():
    client = TestClient(app)

    bad_resp = client.post("/api/cookies/connect/desconhecido")
    assert bad_resp.status_code == 400

    with patch(
        "web.app.launch_visual_login",
        return_value={
            "success": True,
            "portal": "linkedin",
            "url": "https://www.linkedin.com/login",
            "auto_save": True,
            "pid": 12345,
            "message": "ok",
        },
    ) as mock_launch:
        resp = client.post("/api/cookies/connect/linkedin")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["success"] is True
        assert payload["portal"] == "linkedin"
        mock_launch.assert_called_once_with(
            portal="linkedin",
            use_camoufox=True,
            auto_save=True,
        )
