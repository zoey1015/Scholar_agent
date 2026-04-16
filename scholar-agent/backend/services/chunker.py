"""
文本分块服务

将解析后的论文/专利文本按章节和长度切分为 chunk，
每个 chunk 适合做 embedding 向量化（通常 256-512 tokens）。

分块策略：
1. 优先按章节边界切分
2. 超长章节按段落边界再切分
3. 每个 chunk 保留上下文信息（section_title, chunk_type）
"""

import logging
import re
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

# 分块参数
DEFAULT_MAX_CHUNK_SIZE = 512        # 最大 token 数（近似用字符数 / 3 估算）
DEFAULT_CHUNK_OVERLAP = 50          # 重叠 token 数
MAX_CHAR_PER_CHUNK = 1500           # 最大字符数（粗略估算，1 token ≈ 3 chars for 中文, 4 chars for 英文）
OVERLAP_CHARS = 150                 # 重叠字符数


def chunk_document(parsed_data: dict, document_id: str) -> list[dict]:
    """
    将解析后的论文数据切分为 chunks

    Args:
        parsed_data: GROBID 解析结果 dict，包含 abstract, sections 等
        document_id: 文档 ID

    Returns:
        list of chunk dicts:
        [
            {
                "chunk_id": str (uuid),
                "document_id": str,
                "chunk_index": int,
                "content": str,
                "section_title": str,
                "chunk_type": str,  # "abstract" / "section" / "reference"
                "token_count": int (估算),
            },
            ...
        ]
    """
    chunks = []
    chunk_index = 0

    # 1. 摘要作为独立 chunk
    abstract = parsed_data.get("abstract", "").strip()
    if abstract:
        chunks.append(_make_chunk(
            document_id=document_id,
            chunk_index=chunk_index,
            content=abstract,
            section_title="Abstract",
            chunk_type="abstract",
        ))
        chunk_index += 1

    # 2. 各章节切分
    sections = parsed_data.get("sections", [])
    for section in sections:
        title = section.get("title", "")
        content = section.get("content", "").strip()
        if not content:
            continue

        # 判断是否需要再切分
        if len(content) <= MAX_CHAR_PER_CHUNK:
            # 短章节，整体作为一个 chunk
            chunks.append(_make_chunk(
                document_id=document_id,
                chunk_index=chunk_index,
                content=content,
                section_title=title,
                chunk_type="section",
            ))
            chunk_index += 1
        else:
            # 长章节，按段落边界切分
            sub_chunks = _split_long_text(content, title)
            for sub_content in sub_chunks:
                chunks.append(_make_chunk(
                    document_id=document_id,
                    chunk_index=chunk_index,
                    content=sub_content,
                    section_title=title,
                    chunk_type="section",
                ))
                chunk_index += 1

    # 3. 如果没有有效 section（GROBID 解析失败的情况），用 raw_text 兜底
    if not chunks or (len(chunks) == 1 and chunks[0]["chunk_type"] == "abstract"):
        raw_text = parsed_data.get("raw_text", "").strip()
        if raw_text and len(raw_text) > len(abstract) + 100:
            # 去掉已经作为 abstract 的部分
            remaining = raw_text
            if abstract and raw_text.startswith(abstract[:100]):
                remaining = raw_text[len(abstract):]

            for sub_content in _split_long_text(remaining, "Full Text"):
                chunks.append(_make_chunk(
                    document_id=document_id,
                    chunk_index=chunk_index,
                    content=sub_content,
                    section_title="Full Text",
                    chunk_type="section",
                ))
                chunk_index += 1

    logger.info(f"Document {document_id}: split into {len(chunks)} chunks")
    return chunks


def _make_chunk(
    document_id: str,
    chunk_index: int,
    content: str,
    section_title: str,
    chunk_type: str,
) -> dict:
    """创建一个 chunk dict"""
    return {
        "chunk_id": str(uuid.uuid4()),
        "document_id": document_id,
        "chunk_index": chunk_index,
        "content": content.strip(),
        "section_title": section_title,
        "chunk_type": chunk_type,
        "token_count": _estimate_tokens(content),
    }


def _split_long_text(text: str, section_title: str = "") -> list[str]:
    """
    按段落边界切分长文本

    策略：
    1. 先按双换行分段
    2. 贪心合并短段落，直到接近 MAX_CHAR_PER_CHUNK
    3. 相邻 chunk 之间有 OVERLAP_CHARS 的重叠
    """
    # 按段落分割
    paragraphs = re.split(r'\n\s*\n', text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    if not paragraphs:
        return [text[:MAX_CHAR_PER_CHUNK]] if text.strip() else []

    chunks = []
    current_parts = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)

        # 单个段落就超长，强制按字符切分
        if para_len > MAX_CHAR_PER_CHUNK:
            # 先把之前积累的内容输出
            if current_parts:
                chunks.append("\n\n".join(current_parts))
                current_parts = []
                current_len = 0

            # 按句子边界切分超长段落
            for sub in _split_by_sentences(para):
                chunks.append(sub)
            continue

        # 加入当前段落是否会超限
        if current_len + para_len + 2 > MAX_CHAR_PER_CHUNK and current_parts:
            chunks.append("\n\n".join(current_parts))
            # 重叠：保留最后一个段落
            last = current_parts[-1] if len(current_parts[-1]) <= OVERLAP_CHARS else ""
            current_parts = [last] if last else []
            current_len = len(last)

        current_parts.append(para)
        current_len += para_len + 2

    # 剩余部分
    if current_parts:
        chunks.append("\n\n".join(current_parts))

    return chunks


def _split_by_sentences(text: str) -> list[str]:
    """
    按句子边界切分超长段落（兜底方案）
    """
    # 简单的句子分割（句号、问号、感叹号 + 空格/换行）
    sentences = re.split(r'(?<=[.!?。！？])\s+', text)

    chunks = []
    current = []
    current_len = 0

    for sent in sentences:
        if current_len + len(sent) > MAX_CHAR_PER_CHUNK and current:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
        current.append(sent)
        current_len += len(sent) + 1

    if current:
        chunks.append(" ".join(current))

    return chunks


def _estimate_tokens(text: str) -> int:
    """
    粗略估算 token 数

    英文约 4 字符/token，中文约 1.5 字符/token。
    这里用简单的混合估算。
    """
    # 统计中文字符数
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    other_chars = len(text) - chinese_chars

    # 中文按 1.5 char/token，英文按 4 char/token
    tokens = chinese_chars / 1.5 + other_chars / 4
    return max(1, int(tokens))
