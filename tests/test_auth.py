from app.auth import validate_email, validate_password_strength


def test_validate_email():
    assert validate_email("user@example.com")
    assert not validate_email("bad-email")


def test_validate_password_strength():
    assert validate_password_strength("Abcd123!")
    assert not validate_password_strength("weakpass")
