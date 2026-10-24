"""Read-only owner lookup for legacy QA status requests after history retention."""
import json


def saved_qa_owner(store, workflow_id, course_id=None):
    """Find the single book that saved QA ``workflow_id``.

    With ``course_id`` only that course's book-progress records are listed;
    without it (legacy APKs that know only the workflow ID) every course is.
    """
    owners = set()
    args = {"Bucket": store.bucket, "Prefix": f"{course_id}/book-progress/" if course_id else ""}
    while True:
        page = store.s3.list_objects_v2(**args)
        for item in page.get("Contents", []):
            key = item["Key"]
            if "/book-progress/" not in key or not key.endswith(".json"):
                continue
            record = json.loads(store.s3.get_object(Bucket=store.bucket, Key=key)["Body"].read())
            qa = record.get("stages", {}).get("qa", {})
            if qa.get("workflow_id") == workflow_id and qa.get("artifact_ref"):
                cid, bid = record.get("course_id"), record.get("book_id")
                if cid and bid and key.startswith(cid + "/book-progress/"):
                    owners.add((cid, bid))
        token = page.get("NextContinuationToken")
        if not token:
            break
        args["ContinuationToken"] = token
    # Refuse ambiguous ownership rather than show another edition's questions.
    return next(iter(owners)) if len(owners) == 1 else None
