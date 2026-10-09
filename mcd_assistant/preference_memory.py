"""Account-isolated local preferences, dialogue snapshots and order attempt journal."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .decision import InputError


class PreferenceFact(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    person: str = Field(min_length=1, max_length=64)
    key: Literal['food_like', 'food_dislike', 'dietary', 'priority', 'dining_mode']
    value: str = Field(min_length=1, max_length=200)
    source: Literal['explicit', 'inferred'] = 'explicit'
    duration: Literal['durable', 'session'] = 'durable'
    source_quote: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1, default=1.0)

    @model_validator(mode='after')
    def validate_source(self):
        if self.source == 'inferred' and self.key == 'dietary':
            raise ValueError('推断不能生成饮食硬限制')
        if self.source == 'inferred' and self.confidence >= 1:
            raise ValueError('推断不得冒充已确认事实')
        if self.source == 'explicit' and self.confidence != 1:
            raise ValueError('明确陈述无需概率化')
        return self


def default_memory_path() -> Path:
    configured = os.environ.get('MCD_MEMORY_PATH')
    return Path(configured).expanduser() if configured else Path(__file__).resolve().parents[1] / 'data' / 'matching-memory.db'


class PreferenceMemory:
    def __init__(self, path: str | Path = ':memory:', clock: Callable[[], float] = time.time):
        self.clock = clock
        self.path = str(path)
        if self.path != ':memory:':
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA busy_timeout=5000')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS facts(
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, person TEXT NOT NULL,
                key TEXT NOT NULL, value TEXT NOT NULL, payload TEXT NOT NULL,
                created REAL NOT NULL, expires REAL,
                UNIQUE(scope,person,key,value));
            CREATE TABLE IF NOT EXISTS dialogues(
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, revision INTEGER NOT NULL,
                payload TEXT NOT NULL, updated REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS dialogue_scope ON dialogues(scope,updated);
            CREATE TABLE IF NOT EXISTS feedback(
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, dialogue TEXT NOT NULL,
                plan_id TEXT NOT NULL, outcome TEXT NOT NULL, reason TEXT NOT NULL,
                payload TEXT NOT NULL, created REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS feedback_scope ON feedback(scope,created);
            CREATE TABLE IF NOT EXISTS order_attempts(
                scope TEXT NOT NULL, request_key TEXT NOT NULL, dialogue TEXT NOT NULL,
                revision INTEGER NOT NULL, cart_hash TEXT NOT NULL, status TEXT NOT NULL,
                order_id TEXT, updated REAL NOT NULL,
                PRIMARY KEY(scope,request_key), UNIQUE(scope,dialogue,revision));
            CREATE UNIQUE INDEX IF NOT EXISTS active_order_per_dialogue ON order_attempts(scope,dialogue)
                WHERE status IN ('creating','unknown','created','paid');
        ''')
        self.db.execute('INSERT OR IGNORE INTO settings VALUES(?,?)', ('scope_salt', secrets.token_hex(32)))
        self.db.commit()
        salt = self.db.execute('SELECT value FROM settings WHERE key=?', ('scope_salt',)).fetchone()['value']
        self.salt = bytes.fromhex(salt)
        if self.path != ':memory:':
            Path(self.path).chmod(0o600)

    def close(self) -> None:
        self.db.close()

    def account_scope(self, identity: str) -> str:
        return hmac.new(self.salt, identity.encode(), hashlib.sha256).hexdigest()

    def facts(self, scope: str, session_id: str | None = None) -> list[dict]:
        rows = self.db.execute('SELECT id,payload FROM facts WHERE scope=? AND (expires IS NULL OR expires>?) ORDER BY created DESC LIMIT 500',
                               (scope, self.clock())).fetchall()
        facts = [{'id': r['id'], **json.loads(r['payload'])} for r in rows]
        return [f for f in facts if f['duration'] != 'session' or f.get('session_id') == session_id]

    def remember(self, scope: str, facts: list[PreferenceFact], evidence: str, session_id: str | None = None) -> list[dict]:
        if len(facts) > 32:
            raise InputError('一次最多保存 32 条偏好')
        for fact in facts:
            if fact.source_quote not in evidence:
                raise InputError('偏好必须引用提供的原文，不接受没有依据的长期事实')
        with self.db:
            for fact in facts:
                existing = self.db.execute('SELECT payload FROM facts WHERE scope=? AND person=? AND key=? AND value=?',
                    (scope, fact.person.casefold(), fact.key, fact.value)).fetchone()
                if existing and json.loads(existing['payload'])['source'] == 'explicit' and fact.source == 'inferred':
                    continue
                if fact.source == 'explicit' and fact.key in {'food_like', 'food_dislike'}:
                    opposite = 'food_dislike' if fact.key == 'food_like' else 'food_like'
                    self.db.execute('DELETE FROM facts WHERE scope=? AND person=? AND key=? AND value=?',
                                    (scope, fact.person.casefold(), opposite, fact.value))
                if fact.source == 'explicit' and fact.key in {'priority', 'dining_mode'}:
                    self.db.execute('DELETE FROM facts WHERE scope=? AND person=? AND key=?',
                                    (scope, fact.person.casefold(), fact.key))
                expiry = self.clock() + 86400 if fact.duration == 'session' else None
                self.db.execute('''INSERT INTO facts VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(scope,person,key,value) DO UPDATE SET payload=excluded.payload,
                    created=excluded.created,expires=excluded.expires''',
                    (secrets.token_hex(8), scope, fact.person.casefold(), fact.key, fact.value,
                     json.dumps({**fact.model_dump(), 'session_id': session_id if fact.duration == 'session' else None},
                                ensure_ascii=False), self.clock(), expiry))
        return self.facts(scope, session_id)

    def forget(self, scope: str, fact_ids: list[str]) -> None:
        with self.db:
            for identity in fact_ids:
                self.db.execute('DELETE FROM facts WHERE scope=? AND id=?', (scope, identity))

    def clear_profile(self, scope: str) -> None:
        with self.db:
            for table in ['facts', 'feedback', 'dialogues']:
                self.db.execute(f'DELETE FROM {table} WHERE scope=?', (scope,))
        # Minimal order attempt receipts remain for duplicate-order prevention.

    def save_dialogue(self, scope: str, identity: str, revision: int, payload: dict) -> None:
        existing = self.db.execute('SELECT scope FROM dialogues WHERE id=?', (identity,)).fetchone()
        if existing and existing['scope'] != scope:
            raise InputError('会话不属于当前账号')
        with self.db:
            self.db.execute('''INSERT INTO dialogues VALUES(?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,updated=excluded.updated''',
                (identity, scope, revision, json.dumps(payload, ensure_ascii=False), self.clock()))

    def dialogue(self, scope: str, identity: str | None = None) -> dict | None:
        row = self.db.execute('SELECT * FROM dialogues WHERE scope=? AND id=?' if identity else
                              'SELECT * FROM dialogues WHERE scope=? ORDER BY updated DESC LIMIT 1',
                              (scope, identity) if identity else (scope,)).fetchone()
        return {'conversation_id': row['id'], 'revision': row['revision'], **json.loads(row['payload'])} if row else None

    def feedback(self, scope: str, dialogue: str, plan: dict, outcome: str, reason: str, scenario: dict) -> None:
        # Store food/decision evidence, never private coupon IDs, credentials or pay URLs.
        data = {'foods': [{'person': a['person'], 'food_name': a['food_name']} for a in plan.get('allocation', [])],
                'products': [{'person': a['person'], 'food_name': a['product_name']} for a in plan.get('meal_allocation', [])],
                'cash_cents': plan['cash_cents'], 'scenario': scenario, 'source': 'user_report'}
        with self.db:
            self.db.execute('INSERT INTO feedback VALUES(?,?,?,?,?,?,?,?)',
                (secrets.token_hex(8), scope, dialogue, plan['plan_id'], outcome, reason,
                 json.dumps(data, ensure_ascii=False), self.clock()))

    def history(self, scope: str) -> list[dict]:
        rows = self.db.execute('SELECT outcome,reason,payload,created FROM feedback WHERE scope=? ORDER BY created DESC LIMIT 100',
                               (scope,)).fetchall()
        return [{'outcome': r['outcome'], 'reason': r['reason'], 'recorded_at': r['created'], **json.loads(r['payload'])} for r in rows]

    def score(self, scope: str, allocation: list[dict], scenario: dict, session_id: str | None = None) -> tuple[float, list[str]]:
        score, reasons = 0.0, []
        for fact in self.facts(scope, session_id):
            if fact['key'] not in {'food_like', 'food_dislike'}:
                continue
            matches = [a for a in allocation if a['person'].casefold() == fact['person'].casefold() and fact['value'] in a['food_name']]
            if matches:
                score += (1 if fact['key'] == 'food_like' else -1) * len(matches) * fact['confidence']
                reasons.append(f"{fact['person']} 的{fact['source']}偏好：{fact['value']}")
        for event in self.history(scope):
            # Rejection alone does not dislike every item. Ordered/paid is not satisfaction.
            weight = {'selected': 0.15, 'satisfied': 0.6, 'disliked': -0.6}.get(event['outcome'], 0)
            if not weight:
                continue
            old = event['scenario']
            similarity = 1 + int(old.get('weekday') == scenario.get('weekday')) + int(old.get('companions') == scenario.get('companions'))
            similarity += int(old.get('weather') != 'unknown' and old.get('weather') == scenario.get('weather'))
            foods = {(a['person'].casefold(), a['food_name']) for a in event['foods'] + event.get('products', [])}
            matches = sum((a['person'].casefold(), a['food_name']) in foods for a in allocation)
            if matches:
                score += weight * similarity / 4 * matches
                reasons.append(f"相似场景曾{event['outcome']}的餐品，作为软倾向")
        return round(score, 3), list(dict.fromkeys(reasons))[:8]

    def begin_order(self, scope: str, key: str, dialogue: str, revision: int, cart_hash: str) -> tuple[dict, bool]:
        row = self.db.execute('SELECT * FROM order_attempts WHERE scope=? AND request_key=?', (scope,key)).fetchone()
        if row:
            if row['cart_hash'] != cart_hash:
                raise InputError('同一请求键不能用于不同订单')
            return dict(row), False
        pending = self.db.execute("SELECT * FROM order_attempts WHERE scope=? AND dialogue=? AND status IN ('creating','unknown','created','paid') ORDER BY updated DESC LIMIT 1",
                                  (scope,dialogue)).fetchone()
        if pending:
            raise InputError('当前会话已有订单或结果未知的创建请求，请先查询状态，不重复创建')
        try:
            with self.db:
                self.db.execute('INSERT INTO order_attempts VALUES(?,?,?,?,?,?,?,?)',
                                (scope,key,dialogue,revision,cart_hash,'creating',None,self.clock()))
        except sqlite3.IntegrityError:
            raise InputError('该需求版本已有创建记录，请读取订单状态') from None
        return {'status':'creating','order_id':None}, True

    def finish_order(self, scope: str, key: str, status: str, order_id: str | None) -> None:
        with self.db:
            self.db.execute('UPDATE order_attempts SET status=?,order_id=?,updated=? WHERE scope=? AND request_key=?',
                            (status,order_id,self.clock(),scope,key))

    def order_attempt(self, scope: str, key: str) -> dict:
        row = self.db.execute('SELECT * FROM order_attempts WHERE scope=? AND request_key=?', (scope,key)).fetchone()
        if not row:
            raise InputError('当前账号没有该创建请求')
        return dict(row)
