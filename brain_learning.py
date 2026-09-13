"""Bounded offline proposal generation. Originals and provider results stay intact."""

import json
import time

from pydantic import BaseModel, ConfigDict, Field
from brain import Propose, encode, digest


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    lesson: str = Field(min_length=1, max_length=2000)
    conditions: str = Field(min_length=1, max_length=2000)
    source_ids: list[str] = Field(min_length=1, max_length=8)


class Proposals(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    lessons: list[Proposal] = Field(max_length=3)


def init(brain):
    with brain.db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS learning_jobs (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, input TEXT NOT NULL,
                state TEXT NOT NULL, result TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS learning_sources (
                record_id TEXT PRIMARY KEY, job_id TEXT NOT NULL);
        """)
        if "attempts" not in {r[1] for r in db.execute("PRAGMA table_info(learning_jobs)")}:
            db.execute("ALTER TABLE learning_jobs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 1")


def run_once(brain, generator, interval=3600):
    """One scope and at most eight episodes per pass; no network under the DB lock."""
    init(brain)
    with brain.db() as db:
        db.execute("BEGIN IMMEDIATE")
        latest = db.execute("SELECT max(updated) FROM learning_jobs").fetchone()[0]
        if latest is not None and time.time() - latest < interval:
            return None
        for interrupted in db.execute("SELECT id,result FROM learning_jobs WHERE state='running' AND updated<?", (time.time() - 240,)).fetchall():
            report = json.loads(interrupted["result"])
            report["error_type"] = "InterruptedAttempt"
            db.execute("UPDATE learning_jobs SET state='failed',result=? WHERE id=?", (encode(report), interrupted["id"]))
        # A crash cannot leave work permanently running. Retry once after the
        # normal interval; preserve the original failure in the job record.
        retry = db.execute("SELECT * FROM learning_jobs WHERE state='failed' AND attempts<2 ORDER BY created LIMIT 1").fetchone()
        if retry:
            job_id, scope = retry["id"], retry["scope"]
            selected = json.loads(retry["input"])
            prior = json.loads(retry["result"])
            db.execute("UPDATE learning_jobs SET state='running',attempts=attempts+1,updated=? WHERE id=?", (time.time(), job_id))
        else:
            selected, scope, job_id = claim_new(brain, db)
            prior = {}
        if not selected:
            return None
    raw = None
    try:
        proposals, raw = generator.generate(selected)
        allowed = {r["id"] for r in selected}
        if any(not set(p.source_ids) <= allowed for p in proposals.lessons):
            raise ValueError("learning proposal cited records outside its input")
        with brain.db() as db:
            db.execute("BEGIN IMMEDIATE")
            ids = []
            for proposal in proposals.lessons:
                result = brain.propose(db, scope, "consolidator", Propose(task_id="consolidation:" + job_id, **proposal.model_dump()))
                ids.append(result["id"])
            report = {"proposal_ids": ids, "provider": raw, "previous_attempt": prior}
            db.execute("UPDATE learning_jobs SET state='complete',result=?,updated=? WHERE id=?", (encode(report), time.time(), job_id))
        return {"id": job_id, "state": "complete", "proposal_ids": ids}
    except Exception as exc:
        with brain.db() as db:
            # Type only: provider exceptions can contain source text or credentials.
            db.execute("UPDATE learning_jobs SET state='failed',result=?,updated=? WHERE id=?",
                       (encode({"error_type": type(exc).__name__, "previous_attempt": prior,
                                "provider": getattr(exc, "evidence", raw)}), time.time(), job_id))
        return {"id": job_id, "state": "failed", "error_type": type(exc).__name__}


def claim_new(brain, db):
    pending = db.execute("""SELECT r.* FROM records r LEFT JOIN learning_sources s ON s.record_id=r.id
        WHERE r.kind='experience' AND s.record_id IS NULL ORDER BY r.created LIMIT 1""").fetchone()
    if pending is None:
        return [], "", ""
    scope = pending["scope"]
    rows = db.execute("""SELECT r.* FROM records r LEFT JOIN learning_sources s ON s.record_id=r.id
        WHERE r.kind='experience' AND r.scope=? AND s.record_id IS NULL ORDER BY r.created LIMIT 8""", (scope,)).fetchall()
    selected, size = [], 0
    for row in rows:
        value = brain.view(row)
        cost = len(encode(value).encode())
        if selected and size + cost > 24000:
            break
        if not selected and cost > 24000:
            job_id = digest([scope, [value["id"]]])[:32]
            now = time.time()
            db.execute("INSERT INTO learning_jobs(id,scope,input,state,result,created,updated) VALUES(?,?,?,?,?,?,?)",
                       (job_id, scope, encode([value]), "skipped", encode({"error_type": "InputTooLarge"}), now, now))
            db.execute("INSERT INTO learning_sources VALUES(?,?)", (value["id"], job_id))
            return [], scope, job_id
        selected.append(value)
        size += cost
    job_id = digest([scope, [r["id"] for r in selected]])[:32]
    now = time.time()
    db.execute("INSERT INTO learning_jobs(id,scope,input,state,result,created,updated) VALUES(?,?,?,?,?,?,?)", (job_id, scope, encode(selected), "running", "{}", now, now))
    db.executemany("INSERT INTO learning_sources VALUES(?,?)", [(r["id"], job_id) for r in selected])
    return selected, scope, job_id
