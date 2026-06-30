import pytest
from wingman.cloud import identity


def test_unset_raises():
    with pytest.raises(identity.Unauthenticated):
        identity.current_user_id()


def test_set_and_read():
    tok = identity.set_current_user("u1", "e@x.com", "Eve")
    try:
        assert identity.current_user_id() == "u1"
        assert identity.current_email() == "e@x.com"
        assert identity.current_display_name() == "Eve"
    finally:
        identity.reset(tok)
    with pytest.raises(identity.Unauthenticated):
        identity.current_user_id()
