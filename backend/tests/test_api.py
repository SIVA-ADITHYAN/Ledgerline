import pytest
from fastapi.testclient import TestClient

from ledgerline.api import app


@pytest.fixture
def client(tmp_path, monkeypatch, fake_llm):
    monkeypatch.setenv("LEDGERLINE_DB", str(tmp_path / "api.db"))
    return TestClient(app)


def test_upload_list_get_approve(client):
    uploaded = client.post("/api/invoices", files={"file": ("dev_001.txt", b"some invoice text", "text/plain")})
    assert uploaded.status_code == 200
    invoice_id = uploaded.json()["id"]

    listed = client.get("/api/invoices").json()
    assert [row["id"] for row in listed] == [invoice_id]

    detail = client.get(f"/api/invoices/{invoice_id}").json()
    assert detail["invoice_number"] == "SIS/26-27/0042"

    approved = client.post(f"/api/invoices/{invoice_id}/approve", json={"user_id": "kavya", "note": "ok"})
    assert approved.json()["status"] == "approved"


def test_stats_counts_processed_documents(client):
    client.post("/api/invoices", files={"file": ("a.txt", b"text", "text/plain")})
    assert client.get("/api/stats").json()["invoices_processed"] == 1


def test_approve_already_approved_invoice_fails_and_does_not_duplicate_payment(client, tmp_path):
    uploaded = client.post("/api/invoices", files={"file": ("dev_001.txt", b"some invoice text", "text/plain")})
    invoice_id = uploaded.json()["id"]

    # First approval succeeds
    approved1 = client.post(f"/api/invoices/{invoice_id}/approve", json={"user_id": "kavya", "note": "ok"})
    assert approved1.status_code == 200
    assert approved1.json()["status"] == "approved"

    # Second approval must fail (400 Bad Request) and not create another payment
    approved2 = client.post(f"/api/invoices/{invoice_id}/approve", json={"user_id": "farhan", "note": "repeat"})
    assert approved2.status_code == 400

    # Verify directly in SQLite that exactly 1 payment exists
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "api.db"))
    payments = conn.execute("SELECT COUNT(*) FROM payments WHERE invoice_id = ?", (invoice_id,)).fetchone()[0]
    conn.close()
    assert payments == 1


def test_same_user_cannot_approve_twice(client, tmp_path):
    import sqlite3
    # Upload an invoice and manually adjust required_approvals to 2
    uploaded = client.post("/api/invoices", files={"file": ("dev_001.txt", b"some invoice text", "text/plain")})
    invoice_id = uploaded.json()["id"]
    conn = sqlite3.connect(str(tmp_path / "api.db"))
    conn.execute("UPDATE invoices SET required_approvals = 2 WHERE id = ?", (invoice_id,))
    conn.commit()
    conn.close()

    # User kavya approves once -> 1 of 2
    res1 = client.post(f"/api/invoices/{invoice_id}/approve", json={"user_id": "kavya", "note": "first"})
    assert res1.status_code == 200
    assert res1.json()["status"] != "approved"

    # User kavya approves second time -> must fail or not count as distinct approval
    res2 = client.post(f"/api/invoices/{invoice_id}/approve", json={"user_id": "kavya", "note": "second"})
    # Either 400 (already approved by this user) or approvals remains 1 and status != approved
    if res2.status_code == 200:
        assert res2.json()["approvals"] == 1
        assert res2.json()["status"] != "approved"
    else:
        assert res2.status_code == 400

    # Different user approves -> now 2 of 2, status becomes approved
    res3 = client.post(f"/api/invoices/{invoice_id}/approve", json={"user_id": "farhan", "note": "second user"})
    assert res3.status_code == 200
    assert res3.json()["status"] == "approved"


def test_duplicate_invoice_cannot_be_approved(client, tmp_path):
    import json
    import sqlite3
    uploaded = client.post("/api/invoices", files={"file": ("dev_001.txt", b"some invoice text", "text/plain")})
    invoice_id = uploaded.json()["id"]

    # Mark as duplicate
    conn = sqlite3.connect(str(tmp_path / "api.db"))
    conn.execute(
        "UPDATE invoices SET needs_review = 1, review_reasons_json = ? WHERE id = ?",
        (json.dumps(["duplicate"]), invoice_id),
    )
    conn.commit()
    conn.close()

    # Approving duplicate must be rejected
    res = client.post(f"/api/invoices/{invoice_id}/approve", json={"user_id": "kavya", "note": "override"})
    assert res.status_code == 400
    assert "duplicate" in res.json()["detail"].lower()


def test_needs_review_requires_note(client, tmp_path):
    import sqlite3
    uploaded = client.post("/api/invoices", files={"file": ("dev_001.txt", b"some invoice text", "text/plain")})
    invoice_id = uploaded.json()["id"]

    conn = sqlite3.connect(str(tmp_path / "api.db"))
    conn.execute("UPDATE invoices SET needs_review = 1 WHERE id = ?", (invoice_id,))
    conn.commit()
    conn.close()

    # Empty note must fail
    res = client.post(f"/api/invoices/{invoice_id}/approve", json={"user_id": "kavya", "note": "   "})
    assert res.status_code == 400
    assert "note" in res.json()["detail"].lower()


def test_rejected_invoice_cannot_be_approved(client):
    uploaded = client.post("/api/invoices", files={"file": ("dev_001.txt", b"some invoice text", "text/plain")})
    invoice_id = uploaded.json()["id"]

    # Reject invoice
    rej = client.post(f"/api/invoices/{invoice_id}/reject", json={"user_id": "kavya", "note": "wrong vendor"})
    assert rej.status_code == 200
    assert rej.json()["status"] == "rejected"

    # Attempting to approve rejected invoice must fail
    appr = client.post(f"/api/invoices/{invoice_id}/approve", json={"user_id": "farhan", "note": "try approve"})
    assert appr.status_code == 400
    assert "rejected" in appr.json()["detail"].lower()


def test_rejection_recorded_in_audit_log(client, tmp_path):
    import sqlite3
    uploaded = client.post("/api/invoices", files={"file": ("dev_001.txt", b"some invoice text", "text/plain")})
    invoice_id = uploaded.json()["id"]

    client.post(f"/api/invoices/{invoice_id}/reject", json={"user_id": "kavya", "note": "suspected fraud"})

    conn = sqlite3.connect(str(tmp_path / "api.db"))
    row = conn.execute("SELECT user_id, note FROM rejections WHERE invoice_id = ?", (invoice_id,)).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "kavya"
    assert row[1] == "suspected fraud"


