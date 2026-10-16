# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Hybrid HR Policy Retrieval Engine for Altostrat Singapore Employee Handbook."""

from __future__ import annotations

import math
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

HANDBOOK_PATH = Path(__file__).resolve().parent / "data" / "hr_policy_handbook.md"

# Domain-specific synonym mappings to boost retrieval on natural queries
SYNONYM_EXPANSIONS: dict[str, list[str]] = {
    "pto": ["vacation", "annual", "leave", "time off", "1.2"],
    "annual leave": ["vacation", "pto", "1.2"],
    "vacation": ["pto", "annual leave", "holidays", "1.2"],
    "mc": ["medical certificate", "outpatient", "sick", "doctor", "clinic", "1.1"],
    "doctor": ["medical certificate", "mc", "outpatient", "sick", "1.1"],
    "sick": ["outpatient", "hospitalization", "medical", "mc", "illness", "1.1"],
    "monitor": ["remote", "equipment", "allowance", "home office", "hardware", "5.4"],
    "ergonomic": ["equipment", "allowance", "home office", "chair", "5.4"],
    "laptop": ["equipment", "hardware", "assets", "remote", "5.4"],
    "headphone": ["remote", "equipment", "allowance", "5.4"],
    "home office": ["remote", "telework", "allowance", "equipment", "500", "5.4"],
    "equipment": ["home office", "allowance", "hardware", "facilities", "500", "5.4"],
    "remote work": ["telework", "home office", "equipment", "allowance", "5.4"],
    "wfh": ["remote work", "telework", "home office", "equipment", "5.4"],
    "500": ["home office equipment allowance", "5.4"],
    "moving": ["relocation", "transfer", "london", "allowance", "badge", "5.5"],
    "transfer": ["relocation", "london", "allowance", "badge", "facilities", "5.5"],
    "london": ["relocation", "transfer", "hq", "badge", "facilities", "10,000", "5.5"],
    "relocation": ["transfer", "london", "allowance", "badge", "facilities", "10,000", "5.5"],
    "badge": ["badging", "facilities", "physical security", "access", "london", "5.5"],
    "badging": ["badge", "facilities", "physical security", "access", "london", "5.5"],
    "10000": ["relocation allowance", "10,000", "5.5"],
    "10,000": ["relocation allowance", "london", "5.5"],
    "squeaky": ["chair", "minor facility", "cosmetic", "priority", "4 - low", "5.5"],
    "chair": ["squeaky", "minor facility", "facilities", "priority", "4 - low", "5.5"],
    "ticket state": ["ticket lifecycle", "itsm", "sequential", "bypass", "5.5"],
    "bypass": ["ticket lifecycle", "sequential", "prohibited", "5.5"],
    "lifecycle": ["ticket lifecycle", "itsm", "new", "in progress", "resolved", "closed", "5.5"],
    "itsm": ["service ticket", "facilities", "ticket lifecycle", "priority", "5.5"],
    "parental": ["maternity", "baby bonding", "childcare", "family", "2.1", "2.2"],
    "paternity": ["baby bonding", "parental", "leave", "2.2"],
    "baby": ["baby bonding", "childcare", "parental", "2.2"],
    "bonding": ["baby bonding", "parental", "non-birthing", "2.2", "2.1"],
    "baby bonding": ["2.2", "2.1", "parental", "leave", "18 weeks", "90 days"],
    "ramp-back": ["transition", "reduced hours", "full pay", "2.3"],
    "maternity": ["maternity leave", "birthing parent", "24 weeks", "16 weeks", "2.1"],
    "child": ["childcare", "maternity", "baby bonding", "1.3", "2.1", "2.2"],
    "death": ["bereavement", "funeral", "grief", "loss", "compassionate", "3.1"],
    "funeral": ["bereavement", "death", "loss", "grief", "3.1"],
    "pet": ["pet loss", "bereavement", "not covered", "animal", "3.1"],
    "bereavement": ["compassionate", "death", "funeral", "loss", "pet loss", "3.1"],
    "meal": ["meals", "daily meal limit", "120", "travel", "dining", "4.4"],
    "120": ["meal limit", "travel cap", "daily meal limit", "4.4"],
    "food": ["meal", "daily meal limit", "120", "dining", "4.4"],
    "gift": ["host gift", "commercial gifts", "50", "hotel", "friend", "relative", "4.3", "4.4", "5.1", "5.2"],
    "host gift": ["gift", "50", "friend", "relative", "hotel", "lodging", "travel", "4.3", "4.4"],
    "50": ["host gift", "gift limit", "4.3", "4.4", "5.2"],
    "friend": ["host gift", "hotel", "lodging", "relative", "4.3"],
    "relative": ["host gift", "hotel", "lodging", "friend", "4.3"],
    "hotel": ["lodging", "host gift", "friend", "relative", "4.3"],
    "bribe": ["anti-bribery", "government ethics", "corruption", "5.1"],
    "bribery": ["anti-bribery", "government ethics", "corruption", "5.1"],
    "relationship": ["workplace personal relationships", "dating", "consensual", "5.3", "8.1"],
    "dating": ["workplace personal relationships", "romance", "manager", "5.3"],
    "card": ["company card", "corporate card", "concur", "expense", "4.2"],
    "company card": ["corporate card", "concur", "expense", "10,000", "4.2"],
    "overtime": ["toil", "time off in lieu", "1.4"],
    "toil": ["time off in lieu", "overtime", "comp time", "1.4"],
}


class PolicySubsection:
    """Represents a discrete section or subsection of the HR policy handbook."""

    def __init__(
        self,
        section_id: str,
        title: str,
        parent_section: str,
        content: str,
    ) -> None:
        self.section_id = section_id
        self.title = title
        self.parent_section = parent_section
        self.content = content

        # Searchable corpus: boost section_id and title heavily
        self.search_text = (
            f"{section_id} {section_id} {title} {title} {title} "
            f"{parent_section} {content}"
        ).lower()
        self.tokens = self._tokenize(self.search_text)
        self.token_set = set(self.tokens)
        self.token_len = max(len(self.tokens), 1)

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        cleaned = re.sub(r"[^\w\.\-\$]", " ", text.lower())
        return [w for w in cleaned.split() if len(w) > 1]


@lru_cache(maxsize=1)
def load_policy_subsections() -> list[PolicySubsection]:
    """Parses hr_policy_handbook.md into discrete structured subsections."""
    if not HANDBOOK_PATH.exists():
        raise FileNotFoundError(f"Handbook file not found at: {HANDBOOK_PATH}")

    text = HANDBOOK_PATH.read_text(encoding="utf-8")
    subsections: list[PolicySubsection] = []

    current_parent = "General HR Policy"
    current_section_id: str | None = None
    current_title: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        parent_match = re.match(
            r"^\*{0,2}SECTION\s+(\d+)\s*:\s*([^*]+)\*{0,2}", line.strip(), re.IGNORECASE
        )
        sub_match = re.match(r"^\*{0,2}(\d+\.\d+)\s+([^*]+?)\*{0,2}$", line.strip())

        if parent_match:
            if current_section_id and current_title:
                subsections.append(
                    PolicySubsection(
                        section_id=current_section_id,
                        title=current_title,
                        parent_section=current_parent,
                        content="\n".join(current_lines).strip(),
                    )
                )
                current_lines = []
                current_section_id = None
            current_parent = (
                f"SECTION {parent_match.group(1)}: {parent_match.group(2).strip()}"
            )
        elif sub_match:
            if current_section_id and current_title:
                subsections.append(
                    PolicySubsection(
                        section_id=current_section_id,
                        title=current_title,
                        parent_section=current_parent,
                        content="\n".join(current_lines).strip(),
                    )
                )
                current_lines = []
            current_section_id = sub_match.group(1).strip()
            current_title = sub_match.group(2).strip()
        else:
            if current_section_id:
                current_lines.append(line)

    if current_section_id and current_title:
        subsections.append(
            PolicySubsection(
                section_id=current_section_id,
                title=current_title,
                parent_section=current_parent,
                content="\n".join(current_lines).strip(),
            )
        )

    return subsections


def _expand_query(query: str) -> list[str]:
    """Expands query terms using domain synonym mappings."""
    q_lower = query.lower()
    expanded_terms: list[str] = []

    for phrase, synonyms in SYNONYM_EXPANSIONS.items():
        if phrase in q_lower:
            expanded_terms.extend(synonyms)

    tokens = [w for w in re.sub(r"[^\w\.\-\$]", " ", q_lower).split() if len(w) > 1]
    for token in tokens:
        if token in SYNONYM_EXPANSIONS:
            expanded_terms.extend(SYNONYM_EXPANSIONS[token])

    all_terms = tokens + expanded_terms
    # Preserve order while deduplicating
    seen = set()
    deduped = []
    for t in all_terms:
        for word in t.split():
            clean_word = word.lower().strip()
            if clean_word and clean_word not in seen and len(clean_word) > 1:
                seen.add(clean_word)
                deduped.append(clean_word)
    return deduped


def score_subsection(sub: PolicySubsection, query: str, query_tokens: list[str]) -> float:
    """Computes a deterministic BM25 / lexical hybrid relevance score."""
    score = 0.0
    q_lower = query.lower()
    title_lower = sub.title.lower()
    parent_lower = sub.parent_section.lower()
    content_lower = sub.content.lower()

    # Exact section ID match (e.g. user asks "Section 3.1" or "3.1")
    if sub.section_id in query:
        score += 25.0

    # Exact phrase matches
    if len(query.strip()) > 3 and query.strip().lower() in sub.content.lower():
        score += 8.0

    # Specific critical topic boosters
    if ("pet" in q_lower or "bereavement" in q_lower) and sub.section_id == "3.1":
        score += 15.0
    if ("monitor" in q_lower or "home office" in q_lower or "500" in q_lower or "remote" in q_lower) and sub.section_id == "5.4":
        score += 15.0
    if ("relocation" in q_lower or "london" in q_lower or "10,000" in q_lower or "10000" in q_lower or "badge" in q_lower) and sub.section_id == "5.5":
        score += 15.0
    if ("squeaky" in q_lower or "chair" in q_lower or "priority" in q_lower or "ticket state" in q_lower or "lifecycle" in q_lower or "bypass" in q_lower) and sub.section_id == "5.5":
        score += 15.0
    if ("host gift" in q_lower or "friend" in q_lower or "relative" in q_lower) and sub.section_id == "4.3":
        score += 20.0
    if ("120" in q_lower or "meal" in q_lower or "dining" in q_lower) and sub.section_id == "4.4":
        score += 15.0
    if ("sick" in q_lower or "mc" in q_lower or "doctor" in q_lower or "hospitalization" in q_lower) and sub.section_id == "1.1":
        score += 12.0
    if ("vacation" in q_lower or "pto" in q_lower or "annual leave" in q_lower) and sub.section_id == "1.2":
        score += 12.0
    if ("maternity" in q_lower) and sub.section_id == "2.1":
        score += 12.0
    if ("baby bonding" in q_lower or "bonding" in q_lower or "paternity" in q_lower) and sub.section_id == "2.2":
        score += 12.0
    if ("ramp-back" in q_lower or "ramp back" in q_lower) and sub.section_id == "2.3":
        score += 12.0

    # Token-level scoring with location weighting
    avg_len = 250.0
    k1 = 1.2
    b = 0.75
    len_norm = 1.0 - b + b * (sub.token_len / avg_len)

    for term in query_tokens:
        term_freq = 0
        if term in title_lower:
            term_freq += 5
        if term in parent_lower:
            term_freq += 2
        term_freq += content_lower.count(term)

        if term_freq > 0:
            tf = (term_freq * (k1 + 1)) / (term_freq + k1 * len_norm)
            score += tf

    return score


def search_hr_policy(query: str, top_k: int = 4) -> dict[str, Any]:
    """Agent Tool: Search the official Altostrat Singapore Employee Policy Handbook.

    Use this tool for ANY question about Altostrat HR policies, leave entitlements
    (paid vacation, outpatient sick leave, hospitalization, bereavement, pet loss,
    maternity, baby bonding, ramp-back, childcare, TOIL), travel & expense guidelines
    ($120 daily meal limit, $50 host gift, Company Card requirements), remote work
    policies ($500 home office equipment allowance for monitors/hardware), international
    relocation ($10,000 allowance & Facilities badging pre-configuration for London HQ),
    ITSM ticket lifecycle rules (New -> In Progress -> Resolved -> Closed, sequential
    progression requirement) and priority rules (e.g. minor squeaky chair = '4 - Low'),
    ethics, anti-bribery, workplace personal relationships, and conduct perimeters.

    Args:
        query: The natural language search query (e.g., "bereavement leave policy pet loss",
            "home office monitor allowance remote work", "relocation allowance london transfer badge",
            "sick leave medical certificate requirement", "ticket priority squeaky chair").
        top_k: Number of most relevant policy sections to return (default: 4).

    Returns:
        A dict with status, query, source_document citation, and list of matched
        policy sections with section_id, title, citation string, and content snippet.
    """
    subsections = load_policy_subsections()
    query_tokens = _expand_query(query)

    scored: list[tuple[float, PolicySubsection]] = []
    for sub in subsections:
        score = score_subsection(sub, query, query_tokens)
        if score > 0.05:
            scored.append((score, sub))

    scored.sort(key=lambda x: x[0], reverse=True)
    top_matches = scored[:top_k]

    # Optional Vertex AI Search integration hook
    vertex_hits: list[dict[str, Any]] = []
    if os.getenv("ENABLE_VERTEX_SEARCH", "false").lower() == "true":
        try:
            from google.cloud import discoveryengine_v1 as discoveryengine

            project_id = os.getenv("GOOGLE_CLOUD_PROJECT", "dc-test2-489503")
            location = os.getenv("VERTEX_AI_SEARCH_LOCATION", "global")
            data_store_id = os.getenv("VERTEX_AI_DATA_STORE_ID", "hr-policies-datastore")
            serving_config = (
                f"projects/{project_id}/locations/{location}/collections/default_collection/"
                f"dataStores/{data_store_id}/servingConfigs/default_search"
            )

            client = discoveryengine.SearchServiceClient()
            request = discoveryengine.SearchRequest(
                serving_config=serving_config,
                query=query,
                page_size=top_k,
            )
            response = client.search(request=request, timeout=2.0)
            for result in response.results:
                data = result.document.derived_struct_data or {}
                vertex_hits.append(
                    {
                        "section_id": "VertexSearch",
                        "title": data.get("title", "Vertex AI Search Document"),
                        "citation": "[Vertex AI Search Grounding]",
                        "content": data.get("snippet", str(data)),
                        "score": 0.95,
                    }
                )
        except Exception:
            # Seamless fallback to local deterministic indexed handbook
            pass

    matches = [
        {
            "section_id": m[1].section_id,
            "title": m[1].title,
            "citation": f"[Altostrat Singapore Employee Policy Handbook, Section {m[1].section_id}: {m[1].title}]",
            "content": m[1].content,
            "score": round(m[0], 3),
        }
        for m in top_matches
    ]

    # Prepend any Vertex hits if present
    all_matches = vertex_hits + matches

    return {
        "status": "success",
        "query": query,
        "source_document": "Altostrat Singapore Employee Policy Handbook & Conduct Guidelines (go/elevate-apac-m3-policydoc)",
        "matches": all_matches[:top_k],
    }


def list_hr_policy_sections() -> dict[str, Any]:
    """Agent Tool: List all available sections and subsections in the HR policy handbook.

    Returns the complete directory of topics covered in the Altostrat Singapore
    Employee Policy Handbook. Use this when the user asks what policies exist or
    when needing to browse available compliance topics.

    Returns:
        A dict containing status, total count, and a list of section metadata.
    """
    subsections = load_policy_subsections()
    sections_list = [
        {
            "section_id": s.section_id,
            "title": s.title,
            "parent_section": s.parent_section,
        }
        for s in subsections
    ]
    return {
        "status": "success",
        "total_sections": len(sections_list),
        "source_document": "Altostrat Singapore Employee Policy Handbook & Conduct Guidelines",
        "sections": sections_list,
    }
