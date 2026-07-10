import pytest
from fastapi.testclient import TestClient
from lxml import html
from self_governance.db import (
    Base,
    Tenant,
    SuccessionSession,
    TokenUsage,
    engine,
    SessionLocal,
)
from self_governance.github_app import app
from self_governance.auth import hash_key

@pytest.fixture
def clean_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.query(TokenUsage).delete()
    db.query(SuccessionSession).delete()
    db.query(Tenant).delete()
    db.commit()
    yield db
    db.close()

def test_dashboard_empty_sessions(clean_db):
    # Test case: Tenant with no sessions and no token usage (empty state)
    tenant = Tenant(
        id="empty",
        name="Empty Tenant",
        stripe_customer_id="cus_empty123",
        api_key_hash=hash_key("tenant_empty_key"),
    )
    clean_db.add(tenant)
    clean_db.commit()

    client = TestClient(app)
    response = client.get(
        "/dashboard", headers={"Authorization": "Bearer tenant_empty_key"}
    )
    assert response.status_code == 200
    
    # Parse HTML structure
    tree = html.fromstring(response.text)
    
    # 1. Verify Tenant Context Badge
    badge_text = tree.xpath("//div[@class='badge']/span/text()")
    full_badge_text = "".join(tree.xpath("//div[@class='badge']//text()")).strip()
    assert "Tenant Context: empty" in full_badge_text

    # 2. Verify empty sessions row
    session_rows = tree.xpath("//table/tbody/tr")
    assert len(session_rows) == 1
    row_text = "".join(session_rows[0].xpath(".//text()")).strip()
    assert "No sessions recorded." in row_text
    
    # 3. Verify zeroed accrued spend & tokens
    cost_val = tree.xpath("//div[contains(@class, 'cost-val')]/text()")[0].strip()
    assert cost_val == "$0.000000"
    
    # Check tokens
    token_val = tree.xpath("//div[@class='metric-value']/text()")[0].strip()
    assert token_val == "0"


def test_dashboard_extreme_values(clean_db):
    # Test case: Tenant with extreme spending values, extreme token usage, and long customer ID
    long_stripe_id = "cus_" + "a" * 100
    long_tenant_id = "extreme" + "b" * 50
    
    tenant = Tenant(
        id=long_tenant_id,
        name="Extreme Tenant",
        stripe_customer_id=long_stripe_id,
        api_key_hash=hash_key(f"tenant_{long_tenant_id}_key"),
    )
    clean_db.add(tenant)
    clean_db.commit()

    # Add extreme token usage
    usage = TokenUsage(
        tenant_id=long_tenant_id,
        prompt_tokens=999999999999,
        completion_tokens=888888888888,
        cost_usd=1234567.890123
    )
    clean_db.add(usage)
    
    # Add a session with a very long approved roster name
    long_roster = "Agent_" + "C" * 200 + ",Agent_" + "D" * 200
    session = SuccessionSession(
        tenant_id=long_tenant_id,
        status="COMPLETED",
        approved_roster=long_roster,
        temperature=1.23456,
        threshold=7.89101
    )
    clean_db.add(session)
    clean_db.commit()

    client = TestClient(app)
    response = client.get(
        "/dashboard", headers={"Authorization": f"Bearer tenant_{long_tenant_id}_key"}
    )
    assert response.status_code == 200
    
    tree = html.fromstring(response.text)
    
    # 1. Verify extreme total cost and tokens are rendered exactly as strings
    cost_val = tree.xpath("//div[contains(@class, 'cost-val')]/text()")[0].strip()
    assert cost_val == "$1234567.890123"
    
    # Total tokens = 999999999999 + 888888888888 = 1888888888887
    token_val = tree.xpath("//div[@class='metric-value']/text()")[0].strip()
    assert token_val == "1888888888887"

    # 2. Verify long Stripe customer ID is rendered correctly
    stripe_val = tree.xpath("//span[@class='stripe-info-val']/text()")[0].strip()
    assert stripe_val == long_stripe_id

    # 3. Verify long approved roster wraps/renders
    roster_cell = tree.xpath("//table/tbody/tr/td[4]/text()")[0].strip()
    assert roster_cell == long_roster


def test_theme_toggle_flash_vulnerability(clean_db):
    # Verify that the dashboard HTML structure has potential theme-flash / transition glitch on page load
    # specifically checking if theme-toggle-container has data-active-theme="system" hardcoded in raw HTML
    tenant = Tenant(
        id="theme",
        name="Theme Test",
        stripe_customer_id="cus_theme",
        api_key_hash=hash_key("tenant_theme_key"),
    )
    clean_db.add(tenant)
    clean_db.commit()

    client = TestClient(app)
    response = client.get(
        "/dashboard", headers={"Authorization": "Bearer tenant_theme_key"}
    )
    assert response.status_code == 200
    
    # Verify that data-active-theme="system" is hardcoded in the container.
    assert 'class="theme-toggle-container" data-active-theme="system"' in response.text
    
    # Verify that transition: all or transition on '*' is active in CSS
    assert 'transition: background-color var(--transition-normal)' in response.text
    assert '*' in response.text

