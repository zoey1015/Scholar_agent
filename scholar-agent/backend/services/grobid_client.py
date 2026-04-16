"""
GROBID 客户端

调用 GROBID 服务解析学术论文 PDF，返回结构化数据。
GROBID 是一个开源的机器学习库，专门用于提取学术论文的结构化信息。

API 文档: https://grobid.readthedocs.io/en/latest/Grobid-service/
"""

import logging
import xml.etree.ElementTree as ET
from typing import Optional

import httpx

from backend.config import get_settings

logger = logging.getLogger(__name__)

# TEI XML 命名空间
TEI_NS = "http://www.tei-c.org/ns/1.0"
NS = {"tei": TEI_NS}


class GrobidClient:
    """GROBID 服务客户端"""

    def __init__(self):
        self.base_url = get_settings().grobid_url
        self.client = httpx.AsyncClient(timeout=120.0)

    async def is_alive(self) -> bool:
        """检查 GROBID 服务是否可用"""
        try:
            resp = await self.client.get(f"{self.base_url}/api/isalive")
            return resp.status_code == 200
        except Exception:
            return False

    async def parse_fulltext(self, pdf_bytes: bytes) -> dict:
        """
        解析论文全文

        调用 GROBID 的 processFulltextDocument 接口，
        返回包含标题、作者、摘要、章节、引用等结构化信息的 dict。

        Args:
            pdf_bytes: PDF 文件的二进制内容

        Returns:
            {
                "title": str,
                "authors": [{"name": str, "affiliation": str}],
                "abstract": str,
                "sections": [{"title": str, "content": str, "level": int}],
                "references": [{"title": str, "authors": str, "year": str}],
                "raw_text": str,
                "keywords": [str],
            }
        """
        # 调用 GROBID API
        resp = await self.client.post(
            f"{self.base_url}/api/processFulltextDocument",
            files={"input": ("paper.pdf", pdf_bytes, "application/pdf")},
            data={
                "consolidateHeader": "1",       # 合并头部信息
                "consolidateCitations": "0",     # 不合并引用（加速）
                "includeRawAffiliations": "1",
            },
        )

        if resp.status_code != 200:
            raise RuntimeError(f"GROBID returned status {resp.status_code}: {resp.text[:500]}")

        tei_xml = resp.text
        return self._parse_tei_xml(tei_xml)

    async def parse_header(self, pdf_bytes: bytes) -> dict:
        """
        仅解析论文头部（标题、作者、摘要），速度更快。
        适用于批量入库时的快速元信息提取。
        """
        resp = await self.client.post(
            f"{self.base_url}/api/processHeaderDocument",
            files={"input": ("paper.pdf", pdf_bytes, "application/pdf")},
            data={"consolidateHeader": "1"},
        )

        if resp.status_code != 200:
            raise RuntimeError(f"GROBID header parse failed: {resp.status_code}")

        return self._parse_tei_header(resp.text)

    def _parse_tei_xml(self, tei_xml: str) -> dict:
        """解析 GROBID 返回的 TEI XML 全文"""
        try:
            root = ET.fromstring(tei_xml)
        except ET.ParseError as e:
            logger.error(f"TEI XML parse error: {e}")
            return {"title": "", "authors": [], "abstract": "", "sections": [],
                    "references": [], "raw_text": tei_xml[:5000], "keywords": []}

        result = {
            "title": self._extract_title(root),
            "authors": self._extract_authors(root),
            "abstract": self._extract_abstract(root),
            "sections": self._extract_sections(root),
            "references": self._extract_references(root),
            "keywords": self._extract_keywords(root),
            "raw_text": "",
        }

        # raw_text 由所有 section 内容拼接
        all_text_parts = [result["abstract"]]
        for sec in result["sections"]:
            all_text_parts.append(sec["content"])
        result["raw_text"] = "\n\n".join(part for part in all_text_parts if part)

        return result

    def _parse_tei_header(self, tei_xml: str) -> dict:
        """解析 GROBID 返回的 TEI XML 头部"""
        try:
            root = ET.fromstring(tei_xml)
        except ET.ParseError:
            return {"title": "", "authors": [], "abstract": ""}

        return {
            "title": self._extract_title(root),
            "authors": self._extract_authors(root),
            "abstract": self._extract_abstract(root),
        }

    # ========================
    # TEI XML 字段提取方法
    # ========================

    def _extract_title(self, root: ET.Element) -> str:
        """提取论文标题"""
        # 路径: teiHeader/fileDesc/titleStmt/title[@type='main']
        title_el = root.find(f".//{{{TEI_NS}}}titleStmt/{{{TEI_NS}}}title[@type='main']")
        if title_el is not None and title_el.text:
            return title_el.text.strip()

        # 备选路径
        title_el = root.find(f".//{{{TEI_NS}}}titleStmt/{{{TEI_NS}}}title")
        if title_el is not None and title_el.text:
            return title_el.text.strip()

        return ""

    def _extract_authors(self, root: ET.Element) -> list[dict]:
        """提取作者列表"""
        authors = []
        # 路径: teiHeader/fileDesc/sourceDesc/biblStruct/analytic/author
        for author_el in root.findall(
            f".//{{{TEI_NS}}}sourceDesc//{{{TEI_NS}}}author"
        ):
            persname = author_el.find(f"{{{TEI_NS}}}persName")
            if persname is None:
                continue

            # 拼接名字
            forename = persname.find(f"{{{TEI_NS}}}forename")
            surname = persname.find(f"{{{TEI_NS}}}surname")

            name_parts = []
            if forename is not None and forename.text:
                name_parts.append(forename.text.strip())
            if surname is not None and surname.text:
                name_parts.append(surname.text.strip())

            name = " ".join(name_parts)
            if not name:
                continue

            # 提取机构
            affiliation = ""
            aff_el = author_el.find(f"{{{TEI_NS}}}affiliation")
            if aff_el is not None:
                org_el = aff_el.find(f"{{{TEI_NS}}}orgName")
                if org_el is not None and org_el.text:
                    affiliation = org_el.text.strip()

            authors.append({"name": name, "affiliation": affiliation})

        return authors

    def _extract_abstract(self, root: ET.Element) -> str:
        """提取摘要"""
        abstract_el = root.find(f".//{{{TEI_NS}}}profileDesc/{{{TEI_NS}}}abstract")
        if abstract_el is None:
            return ""

        # 摘要可能包含多个 <p> 段落
        paragraphs = []
        for p in abstract_el.findall(f".//{{{TEI_NS}}}p"):
            text = self._get_all_text(p)
            if text:
                paragraphs.append(text)

        # 如果没有 <p> 标签，直接取整个 abstract 的文本
        if not paragraphs:
            text = self._get_all_text(abstract_el)
            if text:
                paragraphs.append(text)

        return "\n\n".join(paragraphs)

    def _extract_sections(self, root: ET.Element) -> list[dict]:
        """提取论文正文各章节"""
        sections = []
        body = root.find(f".//{{{TEI_NS}}}body")
        if body is None:
            return sections

        for div in body.findall(f"{{{TEI_NS}}}div"):
            section = self._parse_div(div, level=1)
            if section and section["content"].strip():
                sections.append(section)

        return sections

    def _parse_div(self, div: ET.Element, level: int) -> Optional[dict]:
        """递归解析一个 div 节点（章节）"""
        # 提取章节标题
        head_el = div.find(f"{{{TEI_NS}}}head")
        title = ""
        if head_el is not None:
            title = self._get_all_text(head_el).strip()
            # 去除标号（如 "1.", "2.1"）
            n = head_el.get("n", "")
            if n and title.startswith(n):
                title = title[len(n):].strip()

        # 提取段落内容
        paragraphs = []
        for p in div.findall(f"{{{TEI_NS}}}p"):
            text = self._get_all_text(p)
            if text:
                paragraphs.append(text)

        content = "\n\n".join(paragraphs)

        # 递归处理子章节
        for sub_div in div.findall(f"{{{TEI_NS}}}div"):
            sub_section = self._parse_div(sub_div, level + 1)
            if sub_section and sub_section["content"].strip():
                sub_title = sub_section.get("title", "")
                if sub_title:
                    content += f"\n\n### {sub_title}\n\n{sub_section['content']}"
                else:
                    content += f"\n\n{sub_section['content']}"

        return {
            "title": title,
            "content": content,
            "level": level,
        }

    def _extract_references(self, root: ET.Element) -> list[dict]:
        """提取引用列表"""
        refs = []
        for bibl in root.findall(f".//{{{TEI_NS}}}listBibl/{{{TEI_NS}}}biblStruct"):
            ref = {}

            # 标题
            title_el = bibl.find(f".//{{{TEI_NS}}}title[@level='a']")
            if title_el is None:
                title_el = bibl.find(f".//{{{TEI_NS}}}title")
            if title_el is not None:
                ref["title"] = self._get_all_text(title_el).strip()
            else:
                ref["title"] = ""

            # 作者（简化，只取名字）
            author_names = []
            for author_el in bibl.findall(f".//{{{TEI_NS}}}author"):
                persname = author_el.find(f"{{{TEI_NS}}}persName")
                if persname is not None:
                    surname = persname.find(f"{{{TEI_NS}}}surname")
                    if surname is not None and surname.text:
                        author_names.append(surname.text.strip())
            ref["authors"] = ", ".join(author_names)

            # 年份
            date_el = bibl.find(f".//{{{TEI_NS}}}date[@type='published']")
            if date_el is None:
                date_el = bibl.find(f".//{{{TEI_NS}}}date")
            ref["year"] = date_el.get("when", "")[:4] if date_el is not None else ""

            if ref["title"]:
                refs.append(ref)

        return refs

    def _extract_keywords(self, root: ET.Element) -> list[str]:
        """提取关键词"""
        keywords = []
        for kw_el in root.findall(
            f".//{{{TEI_NS}}}profileDesc/{{{TEI_NS}}}textClass/{{{TEI_NS}}}keywords/{{{TEI_NS}}}term"
        ):
            if kw_el.text and kw_el.text.strip():
                keywords.append(kw_el.text.strip())
        return keywords

    def _get_all_text(self, element: ET.Element) -> str:
        """
        递归获取元素的所有文本内容（包括子元素中的文本）。
        TEI XML 中文本经常分散在多个子标签内。
        """
        parts = []
        if element.text:
            parts.append(element.text)
        for child in element:
            parts.append(self._get_all_text(child))
            if child.tail:
                parts.append(child.tail)
        return " ".join(parts).strip()

    async def close(self):
        """关闭 HTTP 客户端"""
        await self.client.aclose()


# 全局单例
_grobid_client: Optional[GrobidClient] = None


def get_grobid_client() -> GrobidClient:
    global _grobid_client
    if _grobid_client is None:
        _grobid_client = GrobidClient()
    return _grobid_client
