import hashlib
from self_governance.auth import hash_key, verify_key


def test_pbkdf2_hashing_and_verification():
    # 1. Test hash_key format and PBKDF2 verification
    key = "tenant_test_secret_key"
    hashed = hash_key(key)
    
    assert hashed.startswith("pbkdf2_sha256$100000$")
    assert verify_key(key, hashed) is True
    assert verify_key("wrong_key", hashed) is False


def test_legacy_sha256_fallback_verification():
    # 2. Test legacy SHA-256 fallback verification
    key = "tenant_legacy_secret_key"
    legacy_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
    
    assert not legacy_hash.startswith("pbkdf2_sha256$")
    assert verify_key(key, legacy_hash) is True
    assert verify_key("wrong_key", legacy_hash) is False
