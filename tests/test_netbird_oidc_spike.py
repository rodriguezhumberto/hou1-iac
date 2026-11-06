from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_spike_keeps_pinned_images_and_embedded_provider_contract():
    script = (ROOT / "scripts/netbird-oidc-spike.sh").read_text()
    docs = (ROOT / "docs/netbird-authentik-compatibility.md").read_text()
    assert "netbirdio/netbird-server:0.82.0" in script
    assert "netbirdio/dashboard:v2.96.0" in script
    assert "server image must remain 0.82.0" in script
    assert "embedded IdP retained" in script
    assert "/api/identity-providers" in script
    assert "PKCE" in docs
    assert "gRPC" in docs and "WebSocket" in docs
    assert "No NetBird upgrade is approved" in docs


def test_spike_never_prints_secret_values():
    script = (ROOT / "scripts/netbird-oidc-spike.sh").read_text()
    assert 'printf.*NETBIRD_PAT' not in script
    assert 'printf.*AUTHENTIK_CLIENT_SECRET' not in script
