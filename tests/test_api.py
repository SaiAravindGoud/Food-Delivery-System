from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint():
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_openapi_schema():
    with TestClient(app) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]

    assert "/auth/login" in paths
    assert "/restaurants" in paths
    assert "/cart" in paths
    assert "/orders" in paths
    assert "/owner/orders" in paths
    assert "/delivery/partner/profile" in paths
    assert "/coupons" in paths


def test_order_routes_registered():
    with TestClient(app) as client:
        paths = client.get("/openapi.json").json()["paths"]

    assert "post" in paths["/orders"]
    assert "get" in paths["/orders"]
    assert "patch" in paths["/owner/orders/{order_id}/status"]
    assert "post" in paths["/delivery/partner/deliveries/{delivery_id}/verify-delivery"]
