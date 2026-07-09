"""Filing analyst — extracts business/section context from filings and data-room text."""
from __future__ import annotations

import re

from src.agents.base import BaseAgent


class FilingAnalyst(BaseAgent):
    name = "filing_analyst"
    role = "Reads filings and data-room documents; extracts sections and business context."

    @staticmethod
    def split_sections(markdown_text: str) -> list[tuple[str, str]]:
        """Split a markdown data-room / filing into (section_title, body) pairs by '## ' headings."""
        sections: list[tuple[str, str]] = []
        current_title = "Preamble"
        buffer: list[str] = []
        for line in markdown_text.splitlines():
            heading = re.match(r"^##\s+(.*)$", line)
            if heading:
                if buffer and "".join(buffer).strip():
                    sections.append((current_title, "\n".join(buffer).strip()))
                current_title = heading.group(1).strip()
                buffer = []
            else:
                buffer.append(line)
        if buffer and "".join(buffer).strip():
            sections.append((current_title, "\n".join(buffer).strip()))
        return sections
