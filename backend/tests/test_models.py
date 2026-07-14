def test_create_model(client, auth_headers):
    r = client.post("/api/v1/models",
                    json={"name": "GPT-4o", "provider": "openai", "api_key": "sk-test",
                          "models": ["gpt-4o", "gpt-4o-mini"]},
                    headers=auth_headers)
    assert r.status_code == 201
    d = r.json()["data"]
    assert d["name"] == "GPT-4o"
    assert "api_key" not in d  # key should not be returned

def test_list_models(client, auth_headers):
    client.post("/api/v1/models", json={"name": "M1", "provider": "openai", "models": []}, headers=auth_headers)
    r = client.get("/api/v1/models", headers=auth_headers)
    assert len(r.json()["data"]) >= 1

def test_update_model(client, auth_headers):
    r = client.post("/api/v1/models", json={"name": "Old", "provider": "openai", "models": []}, headers=auth_headers)
    mid = r.json()["data"]["id"]
    r2 = client.put(f"/api/v1/models/{mid}", json={"name": "New"}, headers=auth_headers)
    assert r2.json()["data"]["name"] == "New"

def test_delete_model(client, auth_headers):
    r = client.post("/api/v1/models", json={"name": "Del", "provider": "anthropic", "models": []}, headers=auth_headers)
    mid = r.json()["data"]["id"]
    r2 = client.delete(f"/api/v1/models/{mid}", headers=auth_headers)
    assert r2.status_code == 204


def test_create_easyocr_model_config(client, auth_headers):
    r = client.post("/api/v1/models", json={
        "name": "Local EasyOCR",
        "config_type": "ocr",
        "provider": "easyocr",
        "models": [],
        "options": {"enabled": False, "lang": "ch_sim,en", "device": "cpu"},
    }, headers=auth_headers)

    assert r.status_code == 201
    data = r.json()["data"]
    assert data["config_type"] == "ocr"
    assert data["provider"] == "easyocr"


def test_easyocr_model_test_reports_disabled(client, auth_headers):
    r = client.post("/api/v1/models", json={
        "name": "Local EasyOCR",
        "config_type": "ocr",
        "provider": "easyocr",
        "models": [],
        "options": {"enabled": False},
    }, headers=auth_headers)
    mid = r.json()["data"]["id"]

    test = client.post(f"/api/v1/models/{mid}/test", headers=auth_headers)

    assert test.status_code == 200
    assert test.json()["data"]["ok"] is False
    assert "EasyOCR" in test.json()["data"]["response"]
