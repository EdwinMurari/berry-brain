"""Apply an operator-reviewed room-to-profile plan without changing fact text or IDs.

Run inside the memory container. The plan contains id, expected_owner,
expected_hash, and new_owner. A private payload backup is written before changes.
"""

import argparse
import json
import os
import uuid
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


COLLECTION = "berry_memories_v4"


def request(path, body):
    req = urllib.request.Request(
        f"http://qdrant:6333/collections/{COLLECTION}/points{path}",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as response:
        data = json.load(response)
    if data.get("status") != "ok":
        raise RuntimeError("Qdrant did not confirm the operation")
    return data["result"]


def validate_plan(plan, rows, profiles):
    if not isinstance(plan, list) or not plan or len(plan) > 256:
        raise ValueError("plan must contain 1-256 reviewed records")
    by_id = {row["id"]: row["payload"] for row in rows}
    seen = set()
    pending = []
    for item in plan:
        if not isinstance(item, dict) or set(item) != {"id", "expected_owner", "expected_hash", "new_owner"}:
            raise ValueError("invalid plan entry")
        uuid.UUID(item["id"])
        if item["id"] in seen:
            raise ValueError("duplicate record in plan")
        seen.add(item["id"])
        payload = by_id[item["id"]]
        if not item["expected_owner"].startswith("room:") or item["new_owner"] not in {f"profile:{p}" for p in profiles}:
            raise ValueError("plan must move room facts to a configured human profile")
        if (payload.get("agent_id") != "berry-agents" or payload.get("app_id") != "berry-agents"
                or payload.get("hash") != item["expected_hash"]
                or f"profile:{payload.get('profile_id')}" != item["new_owner"]):
            raise ValueError("record content, source, or personal owner changed")
        if payload.get("user_id") == item["new_owner"] and payload.get("scope") == "profile":
            continue
        if payload.get("user_id") != item["expected_owner"] or payload.get("scope") != "room":
            raise ValueError("record no longer has its reviewed room scope")
        pending.append(item)
    return pending


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-json", required=True)
    parser.add_argument("--profiles", nargs="+", required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan_json)
    rows = request("", {"ids": [item["id"] for item in plan], "with_payload": True, "with_vector": False})
    pending = validate_plan(plan, rows, args.profiles)
    if not pending:
        print(json.dumps({"moved": 0, "already_applied": len(plan)}))
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path(f"/data/ownership-before-{stamp}-{uuid.uuid4().hex[:8]}.json")
    descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump({"plan": plan, "records": rows}, output)
        output.flush()
        os.fsync(output.fileno())
    for item in pending:
        request("/payload?wait=true", {
            "payload": {"scope": "profile", "user_id": item["new_owner"]},
            "filter": {"must": [
                {"has_id": [item["id"]]},
                {"key": "user_id", "match": {"value": item["expected_owner"]}},
                {"key": "hash", "match": {"value": item["expected_hash"]}},
                {"key": "agent_id", "match": {"value": "berry-agents"}},
                {"key": "app_id", "match": {"value": "berry-agents"}},
            ]},
        })
    after = request("", {"ids": [item["id"] for item in plan], "with_payload": True, "with_vector": False})
    if validate_plan(plan, after, args.profiles):
        raise RuntimeError(f"migration incomplete; original payloads are in {backup}")
    old = {row["id"]: row["payload"] for row in rows}
    for row in after:
        expected = {**old[row["id"]], "scope": "profile", "user_id": f"profile:{old[row['id']]['profile_id']}"}
        if row["payload"] != expected:
            raise RuntimeError(f"unrelated payload fields changed; backup is {backup}")
    print(json.dumps({"moved": len(pending), "verified": len(after), "backup": str(backup)}))


if __name__ == "__main__":
    main()
