from invoice_agent import *

def test_normalize_and_create():
    r = normalize_record({"document_info":{"reference_no":"DN-1"},"charge_details":{"charge_item":"H/D Fee","amount":"USD 1,240.50","currency":"$"}})
    assert r["charge_details"]["charge_item"] == "Handling Charge"
    assert r["charge_details"]["amount"] == 1240.50
    assert r["charge_details"]["currency"] == "USD"
    assert decide_operation(r, [])["operation"] == "CREATE"

def test_update_and_validation():
    r = {"document_info":{"reference_no":"DN-1"},"charge_details":{"charge_item":"Handling Charge","amount":40,"currency":"USD"}}
    out = decide_operation(r, [r])
    assert out["operation"] == "UPDATE"
    validate_records([out])
