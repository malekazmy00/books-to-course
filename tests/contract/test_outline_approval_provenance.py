import pytest
from pydantic import ValidationError
from b2c_contracts import OutlineApprovalProvenance

def test_outline_approval_provenance_cannot_forge_human_reviewer():
    with pytest.raises(ValidationError):
        OutlineApprovalProvenance(review_type="system", reviewer_id="human-name", system_id="policy", policy_version="1", evidence_sha256="a"*64)

def test_outline_approval_provenance_human_requires_real_identity():
    with pytest.raises(ValidationError):
        OutlineApprovalProvenance(review_type="human")
    assert OutlineApprovalProvenance(review_type="human", reviewer_id="owner-42").reviewer_id == "owner-42"