"""Persist demo match snapshots and user decisions separately from RAG/benchmark GOLD.

The matched answer remains immutable in the original snapshot. All human
corrections and decisions are auditable events; user selection is not GOLD.
Session capability tokens are hashed at rest. Review session tokens are
delivered only to the initiating browser (never in URLs or localStorage).
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from secrets import compare_digest, token_urlsafe
from typing import Any

from psycopg.types.json import Jsonb

SCHEMA = """
CREATE TABLE IF NOT EXISTS demo_review_sessions (
  request_id TEXT PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  token_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS demo_review_rows (
  request_id TEXT NOT NULL REFERENCES demo_review_sessions(request_id) ON DELETE CASCADE,
  item_index INTEGER NOT NULL CHECK (item_index >= 0),
  initial_status TEXT NOT NULL,
  initial_result JSONB NOT NULL,
  current_result JSONB NOT NULL,
  revision INTEGER NOT NULL DEFAULT 0,
  decision TEXT NOT NULL DEFAULT 'PENDING',
  edited BOOLEAN NOT NULL DEFAULT FALSE,
  selected_ld_id BIGINT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (request_id, item_index)
);
CREATE TABLE IF NOT EXISTS demo_review_events (
  id BIGSERIAL PRIMARY KEY,
  request_id TEXT NOT NULL,
  item_index INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  event_data JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (request_id, item_index)
    REFERENCES demo_review_rows(request_id, item_index) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_demo_review_sessions_created
  ON demo_review_sessions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_demo_review_rows_decision
  ON demo_review_rows(decision);
CREATE INDEX IF NOT EXISTS idx_demo_review_events_created
  ON demo_review_events(created_at DESC);
"""


def initialize_review_schema(conn: Any) -> None:
    """Use the existing Postgres pool and schema initialization lifecycle."""
    for stmt in SCHEMA.split(";"):
        if stmt.strip():
            conn.execute(stmt)


class ReviewConflict(ValueError):
    pass


class ReviewNotFound(ValueError):
    pass


def token_digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class DemoFeedbackStore:
    def __init__(self, pool: Any):
        self.pool = pool

    def start(self, request_id: str, results: list[dict[str, Any]]) -> str:
        token = token_urlsafe(32)
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO demo_review_sessions(request_id,token_hash) VALUES (%s,%s)",
                    (request_id, token_digest(token)),
                )
                for index, result in enumerate(results):
                    conn.execute(
                        """INSERT INTO demo_review_rows(
                            request_id,item_index,initial_status,initial_result,current_result)
                           VALUES(%s,%s,%s,%s,%s)""",
                        (request_id,index,result["status"],Jsonb(result),Jsonb(result)),
                    )
        return token

    def _authorized_session(self, conn: Any, request_id: str, token: str) -> None:
        session = conn.execute(
            "SELECT token_hash FROM demo_review_sessions WHERE request_id=%s FOR UPDATE",
            (request_id,),
        ).fetchone()
        if not session or not compare_digest(str(session[0]), token_digest(token)):
            raise ReviewNotFound("Сессия не найдена. Запустите подбор повторно.")

    def assert_can_append(
        self, request_id: str, token: str, expected_count: int, *, max_rows: int = 100
    ) -> None:
        """Validate before paid inference; the append itself checks again."""
        with self.pool.connection() as conn:
            with conn.transaction():
                self._authorized_session(conn, request_id, token)
                count = conn.execute(
                    "SELECT count(*) FROM demo_review_rows WHERE request_id=%s",
                    (request_id,),
                ).fetchone()[0]
                if count != expected_count:
                    raise ReviewConflict("Список изменился в другой вкладке. Обновите результаты.")
                if count >= max_rows:
                    raise ReviewConflict("Достигнут лимит в 100 позиций на один запуск.")

    def append(self, request_id: str, token: str, expected_count: int,
               result: dict[str, Any], *, max_rows: int = 100) -> dict[str, Any]:
        """Atomically attach exactly one evaluated row to the original batch.

        Lock the session first: two browser tabs cannot append the same index.
        The existing rows and all human decisions remain untouched.
        """
        with self.pool.connection() as conn:
            with conn.transaction():
                self._authorized_session(conn, request_id, token)
                count, latest = conn.execute(
                    """SELECT count(*),max(item_index)
                       FROM demo_review_rows WHERE request_id=%s""",
                    (request_id,),
                ).fetchone()
                if count != expected_count:
                    raise ReviewConflict("Список изменился в другой вкладке. Обновите результаты.")
                if count >= max_rows:
                    raise ReviewConflict("Достигнут лимит в 100 позиций на один запуск.")
                # Contiguous indices are an invariant, not a user-controlled ID.
                if latest is not None and latest + 1 != count:
                    raise ReviewConflict("Некорректная нумерация позиций в сеансе.")
                index = count
                conn.execute(
                    """INSERT INTO demo_review_rows(
                           request_id,item_index,initial_status,initial_result,current_result)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (request_id,index,result["status"],Jsonb(result),Jsonb(result)),
                )
                conn.execute(
                    """INSERT INTO demo_review_events(
                           request_id,item_index,event_type,event_data)
                       VALUES (%s,%s,'ITEM_ADDED',%s)""",
                    (request_id,index,Jsonb({"status":result["status"]})),
                )
                return {"item_index":index,"revision":0,"decision":"PENDING","result":result}

    def _locked_row(self, conn: Any, request_id: str, index: int, token: str) -> dict[str, Any]:
        session = conn.execute(
            "SELECT token_hash FROM demo_review_sessions WHERE request_id=%s",
            (request_id,),
        ).fetchone()
        if not session or not compare_digest(str(session[0]), token_digest(token)):
            raise ReviewNotFound("Сессия не найдена. Запустите подбор повторно.")
        row = conn.execute(
            """SELECT current_result,revision,decision,edited,selected_ld_id
               FROM demo_review_rows WHERE request_id=%s AND item_index=%s FOR UPDATE""",
            (request_id,index),
        ).fetchone()
        if not row:
            raise ReviewNotFound("Позиция не найдена.")
        return {
            "current_result":row[0], "revision":row[1],
            "decision":row[2], "edited":row[3], "selected_ld_id":row[4],
        }

    def get(self, request_id: str, index: int, token: str) -> dict[str, Any]:
        with self.pool.connection() as conn:
            with conn.transaction():
                return self._locked_row(conn,request_id,index,token)

    def revise(self, request_id: str, index: int, token: str,
               expected_revision: int, result: dict[str, Any],
               overrides: dict[str, Any]) -> dict[str, Any]:
        with self.pool.connection() as conn:
            with conn.transaction():
                row=self._locked_row(conn,request_id,index,token)
                if row["revision"] != expected_revision:
                    raise ReviewConflict("Эта позиция уже была изменена. Обновите список.")
                conn.execute(
                    """UPDATE demo_review_rows
                       SET current_result=%s, revision=revision+1, decision='PENDING',
                           edited=TRUE, selected_ld_id=NULL, updated_at=now()
                       WHERE request_id=%s AND item_index=%s""",
                    (Jsonb(result),request_id,index),
                )
                conn.execute(
                    """INSERT INTO demo_review_events(request_id,item_index,event_type,event_data)
                       VALUES (%s,%s,'PARAMETERS_CORRECTED',%s)""",
                    (request_id,index,Jsonb({"overrides":overrides,
                                             "new_status":result["status"]})),
                )
                return {"result":result, "revision":expected_revision+1, "decision":"PENDING"}

    def decide(self, request_id: str, index: int, token: str,
               expected_revision: int, action: str,
               selected_ld_id: int | None = None,
               acknowledge_unknown: bool = False) -> dict[str, Any]:
        with self.pool.connection() as conn:
            with conn.transaction():
                row=self._locked_row(conn,request_id,index,token)
                if row["revision"] != expected_revision:
                    raise ReviewConflict("Эта позиция уже была изменена. Обновите список.")
                result=row["current_result"]
                if action == "REJECTED":
                    selection=None
                else:
                    selection=self._candidate(result,action,selected_ld_id,acknowledge_unknown)
                conn.execute(
                    """UPDATE demo_review_rows
                       SET revision=revision+1, decision=%s, selected_ld_id=%s,
                           updated_at=now()
                       WHERE request_id=%s AND item_index=%s""",
                    (action,selection,request_id,index),
                )
                conn.execute(
                    """INSERT INTO demo_review_events(request_id,item_index,event_type,event_data)
                       VALUES (%s,%s,%s,%s)""",
                    (request_id,index,action,Jsonb({
                        "selected_ld_id":selection,
                        "acknowledge_unknown":acknowledge_unknown,
                    })),
                )
                return {"revision":expected_revision+1, "decision":action,
                        "selected_ld_id":selection}

    @staticmethod
    def _candidate(result: dict[str, Any], action: str,
                   selected_ld_id: int | None, acknowledge_unknown: bool) -> int:
        if action not in {"ACCEPTED","SELECTED_OTHER"}:
            raise ReviewConflict("Неизвестное действие.")
        recommended = result.get("recommended") or []
        options = recommended + (result.get("other_candidates") or [])
        primary = recommended[0] if recommended else None
        if action == "ACCEPTED":
            if primary is None:
                raise ReviewConflict("Нет рекомендованного товара для принятия.")
            selected = primary
        else:
            selected = next((x for x in options if x.get("ld_id") == selected_ld_id), None)
            if selected is None or (primary and selected.get("ld_id") == primary.get("ld_id")):
                raise ReviewConflict("Выбираемый товар отсутствует среди доступных альтернатив.")
        rows=(selected.get("comparison") or {}).get("rows") or []
        strict = [x for x in rows if x.get("strict")]
        if any(x.get("status") == "MISMATCH" for x in strict):
            raise ReviewConflict("Есть расхождение с обязательными характеристиками.")
        if (not strict or any(x.get("status") != "MATCH" for x in strict)) and not acknowledge_unknown:
            raise ReviewConflict("Не все обязательные характеристики подтверждены. Требуется подтверждение ручной проверки.")
        # Human judgement is recorded separately; never re-label the RAG engine.
        return int(selected["ld_id"])

    def stats(self, days: int = 30) -> dict[str, Any]:
        with self.pool.connection() as conn:
            totals = conn.execute(
                """SELECT count(*),
                   count(*) FILTER (WHERE initial_status='MATCHED'),
                   count(*) FILTER (WHERE initial_status='NOT_FOUND'),
                   count(*) FILTER (WHERE initial_status='INSUFFICIENT_PARAMETERS'),
                   count(*) FILTER (WHERE initial_status='ERROR'),
                   count(*) FILTER (WHERE decision='ACCEPTED'),
                   count(*) FILTER (WHERE decision='REJECTED'),
                   count(*) FILTER (WHERE decision='SELECTED_OTHER'),
                   count(*) FILTER (WHERE edited),
                   count(*) FILTER (WHERE decision='PENDING')
                   FROM demo_review_rows d JOIN demo_review_sessions s USING(request_id)
                   WHERE s.created_at >= now() - (%s * interval '1 day')""",
                (days,),
            ).fetchone()
            timeline=conn.execute(
                """SELECT date_trunc('day',s.created_at)::date AS day, count(*),
                          count(*) FILTER (WHERE d.initial_status='MATCHED'),
                          count(*) FILTER (WHERE d.initial_status='NOT_FOUND'),
                          count(*) FILTER (WHERE d.initial_status='INSUFFICIENT_PARAMETERS'),
                          count(*) FILTER (WHERE d.initial_status='ERROR'),
                          count(*) FILTER (WHERE d.decision='ACCEPTED'),
                          count(*) FILTER (WHERE d.decision='REJECTED'),
                          count(*) FILTER (WHERE d.decision='SELECTED_OTHER')
                   FROM demo_review_rows d JOIN demo_review_sessions s USING(request_id)
                   WHERE s.created_at >= now() - (%s * interval '1 day')
                   GROUP BY 1 ORDER BY 1""",
                (days,),
            ).fetchall()
            actions=conn.execute(
                """SELECT event_type,count(*) FROM demo_review_events e
                   WHERE e.created_at >= now() - (%s * interval '1 day')
                   GROUP BY event_type ORDER BY count(*) DESC""",
                (days,),
            ).fetchall()
            interaction_timeline=conn.execute(
                """SELECT date_trunc('day',created_at)::date AS day,
                          count(*) FILTER (WHERE event_type='ACCEPTED'),
                          count(*) FILTER (WHERE event_type='REJECTED'),
                          count(*) FILTER (WHERE event_type='SELECTED_OTHER'),
                          count(*) FILTER (WHERE event_type='PARAMETERS_CORRECTED')
                   FROM demo_review_events
                   WHERE created_at >= now() - (%s * interval '1 day')
                   GROUP BY 1 ORDER BY 1""",
                (days,),
            ).fetchall()
        keys=("processed","auto_matched","not_found","insufficient",
              "errors","accepted","rejected","selected_other",
              "parameter_edited","pending")
        return {
            "period_days":days,
            "counts":dict(zip(keys,map(int,totals))),
            "timeline":[{"day":str(x[0]),"processed":int(x[1]),
                         "auto_matched":int(x[2]),"not_found":int(x[3]),
                         "insufficient":int(x[4]),"errors":int(x[5]),
                         "accepted":int(x[6]),"rejected":int(x[7]),
                         "selected_other":int(x[8])} for x in timeline],
            "interaction_timeline":[{
                "day":str(x[0]),"accepted":int(x[1]),"rejected":int(x[2]),
                "selected_other":int(x[3]),"parameter_edited":int(x[4])
            } for x in interaction_timeline],
            "events":{str(name):int(count) for name,count in actions},
            "note":"Частоты действий пользователей, не точность алгоритма. Группы действий пересекаются.",
        }
