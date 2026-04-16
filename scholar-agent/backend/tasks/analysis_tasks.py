"""
Post-upload analysis pipeline.
"""

import json
import logging
import re

from celery import chain

from backend.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

MOCK_USER_ID = "00000000-0000-0000-0000-000000000001"

CLAIM_EXTRACT_PROMPT = """从以下论文内容中提取核心学术观点。

每个观点标注类型：
- method: 提出或使用的方法
- conclusion: 实验结论或发现
- limitation: 方法的局限性
- dataset: 使用的数据集或基准

要求：
- 每个观点一句话，简洁准确
- 最多提取 8 个观点
- 优先提取有具体数据支撑的结论

只输出 JSON（不要任何其他内容）：
{"claims": [{"type": "method", "content": "...", "section": "..."}]}"""


RELATION_JUDGE_PROMPT = """你是学术论文对比专家。判断以下观点对之间的关系。

新论文的观点：
{new_claim}

已有论文的观点（可能有多条）：
{existing_claims}

关系类型：
- contradiction: 结论矛盾（同一问题得出相反结论）
- complement: 方法互补（解决同一问题的不同角度）
- extension: 延伸发展（在前人基础上改进）
- overlap: 实验重叠（使用相同数据集或基线）
- none: 无明显关系

只输出 JSON：
{{"relations": [{{"idx": 0, "type": "complement", "summary": "简短说明"}}]}}

idx 是已有观点的序号（从 0 开始）。无关系则 relations 为空。"""


@celery_app.task(name="extract_claims", bind=True, max_retries=2)
def extract_claims_task(self, document_id: str):
    import asyncio

    from backend.services.claims_service import save_claims

    try:
        chunks = _get_document_chunks(document_id)
        if not chunks:
            logger.warning(f"No chunks found for document {document_id}")
            return {"status": "skipped", "reason": "no chunks"}

        sections = _group_by_section(chunks)
        all_claims = []
        for section_title, content in sections.items():
            if len(content) < 100:
                continue
            claims = asyncio.run(_extract_claims_for_section(content, section_title))
            all_claims.extend(claims)

        count = save_claims(document_id, all_claims)
        logger.info(f"Extracted {count} claims from document {document_id}")
        return {"status": "done", "claims_count": count, "document_id": document_id}
    except Exception as exc:
        logger.error(f"extract_claims failed for {document_id}: {exc}")
        raise self.retry(exc=exc, countdown=30)


async def _extract_claims_for_section(content: str, section_title: str) -> list[dict]:
    from backend.config import get_settings
    from backend.llm_adapters.base import resolve_adapter

    settings = get_settings()
    try:
        adapter, model_name = resolve_adapter(settings.light_llm_model)
    except ValueError:
        adapter, model_name = resolve_adapter(settings.default_llm_model)

    prompt = f"论文章节：{section_title}\n\n内容：\n{content[:2000]}"
    try:
        response = await adapter.chat(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            system=CLAIM_EXTRACT_PROMPT,
            temperature=0.2,
            max_tokens=800,
        )
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if match:
            data = json.loads(match.group())
            claims = data.get("claims", [])
            for claim in claims:
                claim["section"] = claim.get("section", section_title)
            return claims
    except Exception as exc:
        logger.warning(f"Claim extraction failed for section '{section_title}': {exc}")
    return []


@celery_app.task(name="build_relations", bind=True, max_retries=2)
def build_relations_task(self, extract_result: dict, document_id: str):
    import asyncio

    from backend.services.claims_service import get_all_claims_by_type, get_claims_by_document
    from backend.services.relations_service import save_relation

    try:
        new_claims = get_claims_by_document(document_id)
        if not new_claims:
            return {"status": "skipped", "reason": "no claims"}

        total_relations = 0
        for new_claim in new_claims:
            existing = get_all_claims_by_type(new_claim["type"], exclude_doc=document_id)
            if not existing:
                continue

            scored = _score_by_keyword_overlap(new_claim["content"], existing)
            top_candidates = scored[:5]
            if not top_candidates:
                continue

            relations = asyncio.run(_batch_judge_relations(new_claim, top_candidates))
            for relation in relations:
                if relation.get("type") and relation["type"] != "none":
                    target = top_candidates[relation["idx"]] if relation["idx"] < len(top_candidates) else None
                    if target:
                        save_relation(
                            doc_a_id=document_id,
                            doc_b_id=target["document_id"],
                            relation_type=relation["type"],
                            summary=relation.get("summary", ""),
                            claim_a_id=new_claim["id"],
                            claim_b_id=target["id"],
                            confidence=0.7,
                        )
                        total_relations += 1

        logger.info(f"Built {total_relations} relations for document {document_id}")
        return {"status": "done", "relations_count": total_relations}
    except Exception as exc:
        logger.error(f"build_relations failed for {document_id}: {exc}")
        raise self.retry(exc=exc, countdown=60)


def _score_by_keyword_overlap(query_content: str, candidates: list[dict]) -> list[dict]:
    query_words = set(re.findall(r"\w{2,}", query_content.lower()))
    scored = []
    for candidate in candidates:
        candidate_words = set(re.findall(r"\w{2,}", candidate.get("content", "").lower()))
        overlap = len(query_words & candidate_words)
        if overlap > 0:
            scored.append({**candidate, "_overlap": overlap})
    scored.sort(key=lambda item: item["_overlap"], reverse=True)
    return scored


async def _batch_judge_relations(new_claim: dict, candidates: list[dict]) -> list[dict]:
    from backend.config import get_settings
    from backend.llm_adapters.base import resolve_adapter

    settings = get_settings()
    try:
        adapter, model_name = resolve_adapter(settings.light_llm_model)
    except ValueError:
        adapter, model_name = resolve_adapter(settings.default_llm_model)

    existing_text = "\n".join(f"[{idx}] {candidate.get('content', '')[:300]}" for idx, candidate in enumerate(candidates))
    prompt = RELATION_JUDGE_PROMPT.format(new_claim=new_claim.get("content", ""), existing_claims=existing_text)

    try:
        response = await adapter.chat(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            system="",
            temperature=0.2,
            max_tokens=500,
        )
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return data.get("relations", [])
    except Exception as exc:
        logger.warning(f"Relation judging failed: {exc}")
    return []


@celery_app.task(name="match_research_state", bind=True, max_retries=1)
def match_research_state_task(self, relations_result: dict, document_id: str, user_id: str = None):
    from backend.services.claims_service import get_claims_by_document
    from backend.services.research_state_service import create_notification, get_open_items

    uid = user_id or MOCK_USER_ID
    try:
        open_items = get_open_items(uid)
        if not open_items:
            return {"status": "skipped", "reason": "no open items"}

        new_claims = get_claims_by_document(document_id)
        if not new_claims:
            return {"status": "skipped", "reason": "no claims"}

        notif_count = 0
        claims_text = " ".join(claim.get("content", "") for claim in new_claims)

        for item in open_items:
            item_words = set(re.findall(r"\w{3,}", item.get("content", "").lower()))
            claims_words = set(re.findall(r"\w{3,}", claims_text.lower()))
            overlap = item_words & claims_words
            if len(overlap) >= 2:
                item_type = item.get("type", "question")
                create_notification(
                    user_id=uid,
                    notif_type="state_match",
                    title=f"新上传的论文可能与你的{item_type}相关",
                    detail=f"你的研究{item_type}「{item.get('content', '')[:50]}」与新论文有关联（重叠关键词：{', '.join(list(overlap)[:5])}）",
                    related_doc=document_id,
                )
                notif_count += 1

        logger.info(f"Generated {notif_count} notifications for document {document_id}")
        return {"status": "done", "notifications_count": notif_count}
    except Exception as exc:
        logger.error(f"match_research_state failed: {exc}")
        return {"status": "error", "error": str(exc)}


def trigger_analysis_pipeline(document_id: str, user_id: str = None):
    uid = user_id or MOCK_USER_ID
    pipeline = chain(
        extract_claims_task.s(document_id),
        build_relations_task.s(document_id),
        match_research_state_task.s(document_id, uid),
    )
    result = pipeline.apply_async()
    logger.info(f"Analysis pipeline triggered for {document_id}, task_id={result.id}")
    return result.id


def list_backfill_candidates(limit: int = 200) -> list[dict]:
    """Return historical ready documents that have chunks but no extracted claims."""
    from backend.db.postgres import SyncSession
    from sqlalchemy import text

    db = SyncSession()
    try:
        rows = db.execute(
            text(
                """
                SELECT d.id::text, d.user_id::text
                FROM documents d
                WHERE d.parse_status IN ('ready', 'success')
                  AND EXISTS (
                      SELECT 1 FROM chunks c WHERE c.document_id = d.id
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM paper_claims pc WHERE pc.document_id = d.id
                  )
                ORDER BY d.created_at ASC
                LIMIT :lim
                """
            ),
            {"lim": max(1, int(limit))},
        ).fetchall()
        return [{"document_id": row[0], "user_id": row[1]} for row in rows]
    except Exception as exc:
        logger.error(f"list_backfill_candidates failed: {exc}")
        return []
    finally:
        db.close()


def backfill_existing_documents(limit: int = 200, dry_run: bool = False) -> dict:
    """Trigger analysis pipeline for historical docs that missed graph construction."""
    candidates = list_backfill_candidates(limit=limit)
    if dry_run:
        return {
            "dry_run": True,
            "candidates": len(candidates),
            "documents": candidates,
        }

    queued = []
    for item in candidates:
        task_id = trigger_analysis_pipeline(item["document_id"], item.get("user_id"))
        queued.append(
            {
                "document_id": item["document_id"],
                "user_id": item.get("user_id"),
                "task_id": task_id,
            }
        )

    return {
        "dry_run": False,
        "queued": len(queued),
        "documents": queued,
    }


def _get_document_chunks(document_id: str) -> list[dict]:
    from backend.db.postgres import SyncSession

    from sqlalchemy import text

    db = SyncSession()
    try:
        rows = db.execute(
            text(
                """
                SELECT id, section_title, content
                FROM chunks
                WHERE document_id = :doc_id
                ORDER BY chunk_index
                """
            ),
            {"doc_id": document_id},
        ).fetchall()
        return [{"chunk_id": str(row[0]), "section_title": row[1] or "", "content": row[2]} for row in rows]
    except Exception as exc:
        logger.warning(f"Failed to get chunks: {exc}")
        return []
    finally:
        db.close()


def _group_by_section(chunks: list[dict]) -> dict[str, str]:
    sections = {}
    for chunk in chunks:
        section = chunk.get("section_title", "") or "General"
        sections.setdefault(section, "")
        sections[section] += chunk.get("content", "") + "\n"
    return sections
