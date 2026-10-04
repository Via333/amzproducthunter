"""Local-only saved products and a durable, single-worker research queue."""

from contextlib import contextmanager
from datetime import datetime, timezone
import csv
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def valid_asin(value):
    value = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{10}", value):
        raise ValueError("ASIN 必须是 10 位字母或数字")
    return value


def valid_keyword(value):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        raise ValueError("关键词无效")
    value = " ".join(value.split())
    if not value or len(value) > 200:
        raise ValueError("关键词需为 1-200 个字符")
    return value


def opportunity_keys(row):
    """User decisions follow product identity, never scan rank or source category."""
    family = str(row.get("product_family") or "legacy:" + str(row.get("product_form") or "")).strip().lower()
    form = " ".join(str(row.get("product_form") or "").lower().split())
    market = row.get("market_id") or ("barware" if family in {"cocktail_jigger", "cocktail_tool_kit"}
                                    else "unresolved" if family.startswith("unresolved:") else family)
    return {"market": str(market), "family": family,
            "shape": json.dumps([family, form], ensure_ascii=False, separators=(",", ":"))}


@contextmanager
def research_lock(root):
    directory = Path(root) / ".local"
    directory.mkdir(exist_ok=True)
    with (directory / "research.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class Workbench:
    def __init__(self, root):
        self.root = Path(root)
        directory = self.root / ".local"
        directory.mkdir(exist_ok=True)
        self.database = directory / "research-workbench.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS favorites (
                    asin TEXT PRIMARY KEY, title TEXT NOT NULL, family TEXT NOT NULL,
                    saved INTEGER NOT NULL, updated TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY, asin TEXT NOT NULL, title TEXT NOT NULL,
                    status TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
                    message TEXT NOT NULL DEFAULT '', pid INTEGER);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_asin ON jobs(asin)
                    WHERE status IN ('queued','running');
                CREATE TABLE IF NOT EXISTS dismissals (
                    kind TEXT NOT NULL, item_key TEXT NOT NULL, label TEXT NOT NULL,
                    discarded INTEGER NOT NULL, updated TEXT NOT NULL,
                    PRIMARY KEY(kind,item_key));
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            for name, definition in [("kind", "TEXT NOT NULL DEFAULT 'research'"),
                                     ("keyword", "TEXT NOT NULL DEFAULT ''"),
                                     ("request_id", "TEXT"), ("result_json", "TEXT")]:
                if name not in columns:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS job_request_id ON jobs(request_id) WHERE request_id IS NOT NULL")
        os.chmod(self.database, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def research_page(self, asin):
        path = self.root / "web" / "research" / f"{asin}.html"
        index = self.root / "archive" / "product_research_index.csv"
        if path.is_file() and index.is_file():
            with index.open(encoding="utf-8-sig", newline="") as handle:
                if any(row.get("asin") == asin for row in csv.DictReader(handle)):
                    return f"/research/{asin}.html"
        return ""

    def state(self):
        with self.connect() as db:
            favorites = [dict(row) for row in db.execute("SELECT * FROM favorites WHERE saved=1 ORDER BY updated DESC")]
            jobs = [dict(row) for row in db.execute("SELECT * FROM jobs WHERE kind='research' ORDER BY id DESC LIMIT 200")]
            keyword_jobs = [dict(row) for row in db.execute("SELECT id,keyword,status,created,updated,message FROM jobs WHERE kind='keyword' ORDER BY id DESC LIMIT 200")]
            dismissals = [dict(row) for row in db.execute("SELECT kind,item_key AS key,label,discarded,updated FROM dismissals WHERE discarded=1 ORDER BY updated DESC")]
        for item in favorites + jobs:
            item["url"] = self.research_page(item["asin"])
            item.pop("pid", None)
        return {"favorites": favorites, "jobs": jobs, "dismissals": dismissals, "keyword_jobs": keyword_jobs}

    def enqueue_keyword(self, keyword, request_id):
        keyword = valid_keyword(keyword)
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-zA-Z0-9-]{16,80}", request_id):
            raise ValueError("搜索请求标识无效")
        identity = "keyword:" + hashlib.sha256(keyword.casefold().encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM jobs WHERE request_id=?", (request_id,)).fetchone()
            if existing:
                if existing["asin"] != identity:
                    raise ValueError("请求标识已用于其他关键词")
                return {"id": existing["id"], "keyword": existing["keyword"], "status": existing["status"]}
            existing = db.execute("SELECT * FROM jobs WHERE asin=? AND status IN ('queued','running')", (identity,)).fetchone()
            if existing:
                return {"id": existing["id"], "keyword": existing["keyword"], "status": existing["status"]}
            if db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] >= 30:
                raise ValueError("任务队列已满")
            timestamp = now()
            cursor = db.execute("INSERT INTO jobs(asin,title,kind,keyword,request_id,status,created,updated) VALUES(?,?,'keyword',?,?,'queued',?,?)",
                                (identity, keyword, keyword, request_id, timestamp, timestamp))
            return {"id": cursor.lastrowid, "keyword": keyword, "status": "queued"}

    def keyword_result(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT result_json FROM jobs WHERE id=? AND kind='keyword' AND status='completed'", (int(job_id),)).fetchone()
        if not row or not row["result_json"]:
            raise ValueError("搜索结果尚未生成")
        return json.loads(row["result_json"])

    def dismiss(self, kind, key, discarded, label=""):
        if kind not in {"market", "family", "shape", "asin"} or not isinstance(discarded, bool):
            raise ValueError("舍弃类型或状态无效")
        if not isinstance(key, str) or not key.strip() or len(key) > 600 or any(ord(c) < 32 for c in key):
            raise ValueError("舍弃标识无效")
        if kind == "asin":
            key = valid_asin(key)
        if kind == "shape":
            parts = json.loads(key)
            if not isinstance(parts, list) or len(parts) != 2 or not all(isinstance(p, str) and p for p in parts):
                raise ValueError("形态标识无效")
            key = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
        with self.connect() as db:
            db.execute("INSERT INTO dismissals VALUES(?,?,?,?,?) ON CONFLICT(kind,item_key) DO UPDATE SET label=excluded.label, discarded=excluded.discarded, updated=excluded.updated",
                       (kind, key, str(label)[:200], int(discarded), now()))

    def favorite(self, asin, saved, title="", family=""):
        asin = valid_asin(asin)
        if not isinstance(saved, bool):
            raise ValueError("收藏状态无效")
        with self.connect() as db:
            db.execute("INSERT INTO favorites VALUES(?,?,?,?,?) ON CONFLICT(asin) DO UPDATE SET title=excluded.title, family=excluded.family, saved=excluded.saved, updated=excluded.updated",
                       (asin, str(title)[:300], str(family)[:150], int(saved), now()))

    def enqueue(self, asin, title=""):
        asin = valid_asin(asin)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM jobs WHERE asin=? AND status IN ('queued','running')", (asin,)).fetchone()
            if existing:
                return dict(existing)
            page = self.research_page(asin)
            if page:
                return {"asin": asin, "status": "completed", "url": page, "message": "已有研究，未重复调用"}
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            if count >= 30:
                raise ValueError("研究队列已满，请先收藏")
            timestamp = now()
            cursor = db.execute("INSERT INTO jobs(asin,title,status,created,updated) VALUES(?,?,'queued',?,?)", (asin, str(title)[:300], timestamp, timestamp))
            return dict(db.execute("SELECT * FROM jobs WHERE id=?", (cursor.lastrowid,)).fetchone())

    def cancel(self, job_id):
        with self.connect() as db:
            changed = db.execute("UPDATE jobs SET status='cancelled',updated=? WHERE id=? AND status='queued'", (now(), int(job_id))).rowcount
            if not changed:
                raise ValueError("只能取消尚未开始的任务")

    def recover(self):
        with self.connect() as db:
            rows = db.execute("SELECT id,pid FROM jobs WHERE status='running'").fetchall()
            for row in rows:
                if row["pid"]:
                    try:
                        os.kill(row["pid"], 0)
                        continue
                    except ProcessLookupError:
                        pass
                db.execute("UPDATE jobs SET status='interrupted',message=?,updated=? WHERE id=?", ("服务中断，请检查结果后手动重试", now(), row["id"]))

    def claim(self):
        self.recover()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM jobs WHERE status='running'").fetchone():
                return None
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET status='running',updated=?,pid=? WHERE id=?", (now(), os.getpid(), row["id"]))
            return dict(row)

    def finish(self, job_id, status, message="", result=None):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status=?,message=?,updated=?,pid=NULL,result_json=? WHERE id=?",
                       (status, message, now(), json.dumps(result, ensure_ascii=False) if result is not None else None, job_id))

    def set_pid(self, job_id, pid):
        with self.connect() as db:
            db.execute("UPDATE jobs SET pid=? WHERE id=?", (pid, job_id))
