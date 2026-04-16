"""
上传管线 - Celery 异步任务

论文上传成功后触发：
  Task 1: extract_claims    — 提取核心观点
  Task 2: build_relations   — 与已有论文构建关系
  Task 3: match_state       — 匹配用户研究状态，生成通知

所有任务在后台运行，用户不等待。
"""

import json
import re
import logging
from celery import current_app as celery_app

logger = logging.getLogger(__name__)

MOCK_USER_ID = "00000000-0000-0000-0000-000000000001"

# ================================================================
# Task 1: 提取论文核心观点
# ================================================================

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


@celery_app.task(name="extract_claims", bind=True, max_retries=2)
def extract_claims_task(self, document_id: str):
    """
    从论文中提取核心观点，存入 paper_claims 表。

    读取已向量化的 chunks，按 section 分组，
    对每组调 LLM 提取观点。
    """
    import asyncio
    from backend.services.claims_service import save_claims

    try:
        # 1. 获取论文的 chunks
        chunks = _get_document_chunks(document_id)
        if not chunks:
            logger.warning(f"No chunks found for document {document_id}")
            return {"status": "skipped", "reason": "no chunks"}

        # 2. 按 section 分组，拼接内容
        sections = _group_by_section(chunks)

        # 3. 对每个 section 提取观点
        all_claims = []
        for section_title, content in sections.items():
            if len(content) < 100:
                continue

            claims = asyncio.run(
                _extract_claims_for_section(content, section_title)
            )
            all_claims.extend(claims)

        # 4. 保存到 DB
        count = save_claims(document_id, all_claims)

        logger.info(f"Extracted {count} claims from document {document_id}")
        return {"status": "done", "claims_count": count, "document_id": document_id}

    except Exception as e:
        logger.error(f"extract_claims failed for {document_id}: {e}")
        raise self.retry(exc=e, countdown=30)


async def _extract_claims_for_section(content: str, section_title: str) -> list[dict]:
    """对单个 section 提取观点"""
    from backend.llm_adapters.base import resolve_adapter
    from backend.config import get_settings

    settings = get_settings()
    try:
        adapter, model = resolve_adapter(settings.light_llm_model)
    except ValueError:
        adapter, model = resolve_adapter(settings.default_llm_model)

    # 截取前 2000 字
    text = content[:2000]
    prompt = f"论文章节：{section_title}\n\n内容：\n{text}"

    try:
        resp = await adapter.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            system=CLAIM_EXTRACT_PROMPT,
            temperature=0.2,
            max_tokens=800,
        )

        match = re.search(r'\{.*\}', resp, re.DOTALL)
        if match:
            data = json.loads(match.group())
            claims = data.get("claims", [])
            # 补充 section 信息
            for c in claims:
                c["section"] = c.get("section", section_title)
            return claims
    except Exception as e:
        logger.warning(f"Claim extraction failed for section '{section_title}': {e}")

    return []


# ================================================================
# Task 2: 构建论文间关系
# ================================================================

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


@celery_app.task(name="build_relations", bind=True, max_retries=2)
def build_relations_task(self, extract_result: dict, document_id: str):
    """
    拿新论文的 claims 与已有论文的 claims 做对比。

    优化策略：
    1. 只对比同类型的 claims（method vs method）
    2. 先做文本相似度过滤（简化版：关键词重叠），取 top-5
    3. 批量送 LLM（一次调用判断多对）
    """
    import asyncio
    from backend.services.claims_service import get_claims_by_document, get_all_claims_by_type
    from backend.services.relations_service import save_relation

    try:
        new_claims = get_claims_by_document(document_id)
        if not new_claims:
            return {"status": "skipped", "reason": "no claims"}

        total_relations = 0

        for nc in new_claims:
            # 1. 获取同类型的已有 claims
            existing = get_all_claims_by_type(nc["type"], exclude_doc=document_id)
            if not existing:
                continue

            # 2. 简单相似度过滤：关键词重叠度
            scored = _score_by_keyword_overlap(nc["content"], existing)
            top_candidates = scored[:5]  # 取 top-5

            if not top_candidates:
                continue

            # 3. 批量 LLM 判断
            relations = asyncio.run(
                _batch_judge_relations(nc, top_candidates)
            )

            # 4. 保存有效关系
            for r in relations:
                if r.get("type") and r["type"] != "none":
                    target = top_candidates[r["idx"]] if r["idx"] < len(top_candidates) else None
                    if target:
                        save_relation(
                            doc_a_id=document_id,
                            doc_b_id=target["document_id"],
                            relation_type=r["type"],
                            summary=r.get("summary", ""),
                            claim_a_id=nc["id"],
                            claim_b_id=target["id"],
                            confidence=0.7,
                        )
                        total_relations += 1

        logger.info(f"Built {total_relations} relations for document {document_id}")
        return {"status": "done", "relations_count": total_relations}

    except Exception as e:
        logger.error(f"build_relations failed for {document_id}: {e}")
        raise self.retry(exc=e, countdown=60)


def _score_by_keyword_overlap(query_content: str, candidates: list[dict]) -> list[dict]:
    """简单关键词重叠度排序（替代向量相似度，减少依赖）"""
    query_words = set(re.findall(r'\w{2,}', query_content.lower()))

    scored = []
    for c in candidates:
        c_words = set(re.findall(r'\w{2,}', c.get("content", "").lower()))
        overlap = len(query_words & c_words)
        if overlap > 0:
            scored.append({**c, "_overlap": overlap})

    scored.sort(key=lambda x: x["_overlap"], reverse=True)
    return scored


async def _batch_judge_relations(new_claim: dict, candidates: list[dict]) -> list[dict]:
    """批量 LLM 判断关系（一次调用）"""
    from backend.llm_adapters.base import resolve_adapter
    from backend.config import get_settings

    settings = get_settings()
    try:
        adapter, model = resolve_adapter(settings.light_llm_model)
    except ValueError:
        adapter, model = resolve_adapter(settings.default_llm_model)

    existing_text = "\n".join(
        f"[{i}] {c.get('content', '')[:300]}"
        for i, c in enumerate(candidates)
    )

    prompt = RELATION_JUDGE_PROMPT.format(
        new_claim=new_claim.get("content", ""),
        existing_claims=existing_text,
    )

    try:
        resp = await adapter.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            system="",
            temperature=0.2,
            max_tokens=500,
        )

        match = re.search(r'\{.*\}', resp, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return data.get("relations", [])
    except Exception as e:
        logger.warning(f"Relation judging failed: {e}")

    return []


# ================================================================
# Task 3: 匹配用户研究状态
# ================================================================

@celery_app.task(name="match_research_state", bind=True, max_retries=1)
def match_research_state_task(self, relations_result: dict, document_id: str, user_id: str = None):
    """
    检查新论文是否回答了用户的 open questions。

    对新论文的 claims 与用户的 research_state 做关键词匹配，
    如果有相关的就生成通知。
    """
    from backend.services.research_state_service import get_open_items, create_notification
    from backend.services.claims_service import get_claims_by_document

    uid = user_id or MOCK_USER_ID

    try:
        open_items = get_open_items(uid)
        if not open_items:
            return {"status": "skipped", "reason": "no open items"}

        new_claims = get_claims_by_document(document_id)
        if not new_claims:
            return {"status": "skipped", "reason": "no claims"}

        notif_count = 0
        claims_text = " ".join(c.get("content", "") for c in new_claims)

        for item in open_items:
            item_content = item.get("content", "")
            # 简单关键词匹配
            item_words = set(re.findall(r'\w{3,}', item_content.lower()))
            claims_words = set(re.findall(r'\w{3,}', claims_text.lower()))

            overlap = item_words & claims_words
            if len(overlap) >= 2:  # 至少 2 个关键词重叠
                item_type = item.get("type", "question")
                create_notification(
                    user_id=uid,
                    notif_type="state_match",
                    title=f"新上传的论文可能与你的{item_type}相关",
                    detail=f"你的研究{item_type}「{item_content[:50]}」与新论文有关联（重叠关键词：{', '.join(list(overlap)[:5])}）",
                    related_doc=document_id,
                )
                notif_count += 1

        logger.info(f"Generated {notif_count} notifications for document {document_id}")
        return {"status": "done", "notifications_count": notif_count}

    except Exception as e:
        logger.error(f"match_research_state failed: {e}")
        return {"status": "error", "error": str(e)}


# ================================================================
# 管线入口（论文上传成功后调用）
# ================================================================

def trigger_analysis_pipeline(document_id: str, user_id: str = None):
    """
    触发异步分析管线

    Celery chain: extract_claims → build_relations → match_state
    在论文解析和向量化成功后调用。
    """
    from celery import chain

    uid = user_id or MOCK_USER_ID

    pipeline = chain(
        extract_claims_task.s(document_id),
        build_relations_task.s(document_id),
        match_research_state_task.s(document_id, uid),
    )

    result = pipeline.apply_async()
    logger.info(f"Analysis pipeline triggered for {document_id}, task_id={result.id}")
    return result.id


# ================================================================
# 辅助函数
# ================================================================

def _get_document_chunks(document_id: str) -> list[dict]:
    """从向量库或 DB 获取论文的 chunks"""
    from backend.db.postgres import SyncSession
    from sqlalchemy import text

    db = SyncSession()
    try:
        # 尝试从 document_chunks 表获取
        rows = db.execute(text("""
            SELECT chunk_id, section_title, content
            FROM document_chunks
            WHERE document_id = :doc_id
            ORDER BY chunk_index
        """), {"doc_id": document_id}).fetchall()

        return [
            {"chunk_id": str(r[0]), "section_title": r[1] or "", "content": r[2]}
            for r in rows
        ]
    except Exception as e:
        logger.warning(f"Failed to get chunks: {e}")
        return []
    finally:
        db.close()


def _group_by_section(chunks: list[dict]) -> dict[str, str]:
    """按 section 分组，拼接内容"""
    sections = {}
    for c in chunks:
        section = c.get("section_title", "") or "General"
        if section not in sections:
            sections[section] = ""
        sections[section] += c.get("content", "") + "\n"

    return sections
