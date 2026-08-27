"""Input validation (backend/comments/utils.py)."""

import pytest

from backend.comments import constants
from backend.comments.utils import (
    CommentError, validate_capture, validate_target, validate_text,
)

def err(fn, *args):
    with pytest.raises(CommentError) as e:
        fn(*args)
    return e.value

# ---- text --------------------------------------------------------------------

def test_text_valid_is_trimmed():
    assert validate_text("  hi there  ") == "hi there"

def test_text_empty_rejected():
    assert err(validate_text, "").status_code == 400
    assert err(validate_text, "   \n\t ").status_code == 400

def test_text_non_string_rejected():
    for bad in (None, 42, ["x"], {"t": 1}):
        assert err(validate_text, bad).status_code == 400

def test_text_at_limit_ok_over_limit_rejected():
    assert validate_text("a" * constants.MAX_TEXT)
    e = err(validate_text, "a" * (constants.MAX_TEXT + 1))
    assert e.status_code == 400 and "too long" in e.message

# ---- target ------------------------------------------------------------------

def test_target_valid():
    t = validate_target({"selector": "#btn", "label": "Button"})
    assert t["selector"] == "#btn"

def test_target_missing_or_bad_selector():
    for bad in (None, {}, {"selector": ""}, {"selector": 5},
                {"selector": "x" * (constants.MAX_SELECTOR + 1)}):
        assert err(validate_target, bad).status_code == 400

def test_target_label_optional_but_capped():
    assert validate_target({"selector": "#a"})
    assert err(validate_target,
               {"selector": "#a", "label": "x" * (constants.MAX_LABEL + 1)}).status_code == 400

# ---- capture -----------------------------------------------------------------

def test_capture_none_ok():
    assert validate_capture(None) is None

def test_capture_must_be_object():
    assert err(validate_capture, "nope").status_code == 400

def test_capture_dom_and_screenshot_caps():
    assert err(validate_capture,
               {"domSnapshot": "x" * (constants.MAX_DOM_BYTES + 1)}).status_code == 400
    assert err(validate_capture,
               {"screenshot": "x" * (constants.MAX_SCREENSHOT_BYTES + 1)}).status_code == 400
    assert validate_capture({"domSnapshot": "<html/>", "screenshot": "data:image/jpeg;x"})

def test_capture_lists_truncated_keeping_newest():
    cap = validate_capture({"network": list(range(constants.MAX_NETWORK_ENTRIES + 50))})
    assert len(cap["network"]) == constants.MAX_NETWORK_ENTRIES
    assert cap["network"][-1] == constants.MAX_NETWORK_ENTRIES + 49  # newest kept

def test_capture_list_wrong_type_rejected():
    assert err(validate_capture, {"network": "not-a-list"}).status_code == 400

def test_capture_short_fields_capped():
    assert err(validate_capture,
               {"sha": "x" * (constants.MAX_CAPTURE_FIELD + 1)}).status_code == 400
    assert validate_capture({"sha": "abc1234", "url": "/demo/profile"})

# ---- capture safety: server-side redaction + total cap -----------------------

def test_capture_total_size_capped():
    half = "x" * (constants.MAX_CAPTURE_TOTAL_BYTES // 2 + 100)
    e = err(validate_capture, {"domSnapshot": "d" * 1000,
                               "screenshot": "data:image/jpeg;" + "s" * 7_000_000,
                               "extra_blob": half})
    assert e.status_code == 400 and "too large" in e.message

def test_network_urls_redacted_server_side():
    cap = validate_capture({"network": [
        {"url": "/api/user?token=supersecret123&x=1"},
        {"url": "/api/ok?page=2"},
    ]})
    assert "supersecret123" not in cap["network"][0]["url"]
    assert "[redacted]" in cap["network"][0]["url"]
    assert cap["network"][1]["url"] == "/api/ok?page=2"       # untouched

def test_console_secrets_and_jwts_redacted():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop"
    cap = validate_capture({"console": [
        {"msg": f"login ok bearer={jwt}"},
        {"msg": "password: hunter2 oops"},
    ]})
    assert jwt not in cap["console"][0]["msg"]
    assert "hunter2" not in cap["console"][1]["msg"]

def test_dom_password_inputs_redacted():
    cap = validate_capture({"domSnapshot": '<input type="password" value="hunter2">'})
    assert "hunter2" not in cap["domSnapshot"]
    assert "[redacted]" in cap["domSnapshot"]
