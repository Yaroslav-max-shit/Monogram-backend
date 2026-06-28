def test_register(client):
    response = client.post("/auth/register", json={
        "username": "testuser",
        "email": "test@example.com",
        "password": "TestPass123!",
        "first_name": "Test",
        "last_name": "User"
    })
    assert response.status_code == 201
    data = response.json()
    assert data["user"]["username"] == "testuser"

def test_login(client):
    client.post("/auth/register", json={
        "username": "testuser",
        "email": "test@example.com",
        "password": "TestPass123!",
        "first_name": "Test"
    })
    response = client.post("/auth/login", json={
        "username": "testuser",
        "password": "TestPass123!"
    })
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data or "status" in data

def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
