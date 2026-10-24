from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path

spec=spec_from_file_location("reboot_recovery",Path(__file__).parents[3]/"scripts/recover-lost-b2c-activities.py")
recovery=module_from_spec(spec)
spec.loader.exec_module(recovery)

def test_current_worker_activity_is_preserved():
    assert not recovery.worker_was_lost("1@123456abcdef",110,120,{"123456abcdef":100})

def test_pre_reboot_process_with_same_container_identity_is_recovered():
    assert recovery.worker_was_lost("1@123456abcdef",80,90,{"123456abcdef":100})

def test_removed_container_worker_is_recovered():
    assert recovery.worker_was_lost("1@123456abcdef",80,90,{"abcdef123456":100})

def test_unknown_native_worker_is_never_interrupted():
    assert not recovery.worker_was_lost("worker@native-host",80,90,{"abcdef123456":100})

def test_new_heartbeat_after_boot_prevents_recovery():
    assert not recovery.worker_was_lost("1@123456abcdef",80,110,{"123456abcdef":100})


def test_ingestion_outline_and_index_recover_only_matching_b2c_workflows():
    assert recovery.recoverable_activity("b2c-ingest-example", "ingest_parse_pdf")
    assert recovery.recoverable_activity("b2c-ingest-example", "outline_from_parsed_book")
    assert recovery.recoverable_activity("b2c-index-example", "index_approved_book")
    assert not recovery.recoverable_activity("b2c-index-example", "ingest_parse_pdf")
    assert not recovery.recoverable_activity("other-project", "ingest_parse_pdf")
    assert not recovery.recoverable_activity("b2c-ingest-example", "unrelated_activity")
