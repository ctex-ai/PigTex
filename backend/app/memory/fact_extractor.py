"""
Fact Extractor - Automatically extract facts from user conversations.

Uses:
1. Pattern-based extraction (fast, no API cost)
2. Optional AI-based extraction (currently disabled until user-scoped
   credentials exist for background jobs)

Facts are stored with confidence scores:
- Pattern-matched: 0.7-0.95
- AI-extracted: 0.8
- User-confirmed: 1.0
"""

import json
import re
import uuid
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from ..local_storage.local_models import LocalFact
from ..upstream_request import UpstreamRequestConfig


@dataclass(frozen=True)
class ExtractionPattern:
    """Pattern for extracting specific fact types."""
    category: str
    predicate: str
    pattern: str
    confidence: float = 0.7
    subject: str = "User"
    scope: str = "system"  # system | workspace
    split_values: bool = False


@dataclass(frozen=True)
class PreferenceSignal:
    """Signal pattern that maps directly to a normalized preference value."""
    category: str
    key: str
    value: str
    pattern: str
    confidence: float = 0.85


@dataclass
class ExtractedPreference:
    """Preference extracted from user text or derived from facts."""
    category: str
    key: str
    value: str
    confidence: float = 0.8


@dataclass
class ExtractionResult:
    """Combined extraction output for one message."""
    facts: List[LocalFact]
    preferences: List[ExtractedPreference]


class FactExtractor:
    """
    Extracts facts from user messages using patterns and AI.

    Supports bilingual extraction (English + Vietnamese) for:
    - Personal profile: name, age, location, timezone, birthday
    - Work profile: occupation, company
    - Preferences: likes/dislikes/hobbies/favorites
    - Skills: known stack and learning goals
    - Communication style: response language/style/tone
    - Coding style: language/editor/os/practices
    - Workspace context: project stack, rules, goals, deadlines
    """

    # Pattern definitions for common fact types
    PATTERNS: List[ExtractionPattern] = [
        # ---------------------------
        # Personal profile
        # ---------------------------
        # Name
        ExtractionPattern("personal", "name", r"\b(?:from now on,\s*)?(?:please\s+)?(?:change|update)\s+my\s+name\s+to\s+([A-Za-z0-9][A-Za-z0-9_-]{2,63})\b", 0.99),
        ExtractionPattern("personal", "name", r"\b(?:from now on,\s*)?(?:please\s+)?(?:change|update)\s+my\s+name\s+to\s+([A-Za-zÀ-ỹ][A-Za-zÀ-ỹ' -]{1,60})", 0.97),
        ExtractionPattern("personal", "name", r"\b(?:từ giờ|tu gio)[, ]+(?:hãy\s+)?(?:đổi|doi|cập nhật|cap nhat)\s+tên\s+(?:tôi|em|mình)\s+thành\s+([A-Za-z0-9][A-Za-z0-9_-]{2,63})\b", 0.99),
        ExtractionPattern("personal", "name", r"\b(?:từ giờ|tu gio)[, ]+(?:hãy\s+)?(?:đổi|doi|cập nhật|cap nhat)\s+tên\s+(?:tôi|em|mình)\s+thành\s+([A-Za-zÀ-ỹ][A-Za-zÀ-ỹ' -]{1,60})", 0.97),
        ExtractionPattern("personal", "name", r"\b(?:my legal name is|my full name is)\s+([A-Za-z0-9][A-Za-z0-9_-]{2,63})\b", 0.98),
        ExtractionPattern("personal", "name", r"\b(?:my legal name is|my full name is)\s+([A-Za-zÀ-ỹ][A-Za-zÀ-ỹ' -]{1,60})", 0.96),
        ExtractionPattern("personal", "name", r"\bmy name is\s+([A-Za-z0-9][A-Za-z0-9_-]{2,63})\b", 0.97),
        ExtractionPattern("personal", "name", r"\bt[oô]i t[êe]n l[aà]\s+([A-Za-z0-9][A-Za-z0-9_-]{2,63})\b", 0.97),
        ExtractionPattern("personal", "name", r"\b(?:em|mình|mình) t[êe]n (?:l[aà]\s+)?([A-Za-z0-9][A-Za-z0-9_-]{2,63})\b", 0.95),
        ExtractionPattern("personal", "name", r"\bmy name is\s+([A-Za-zÀ-ỹ][A-Za-zÀ-ỹ' -]{1,60})", 0.95),
        ExtractionPattern("personal", "name", r"\bt[oô]i t[êe]n l[aà]\s+([A-Za-zÀ-ỹ][A-Za-zÀ-ỹ' -]{1,60})", 0.95),
        ExtractionPattern("personal", "name", r"\b(?:em|mình|mình) t[êe]n (?:l[aà]\s+)?([A-Za-zÀ-ỹ][A-Za-zÀ-ỹ' -]{1,60})", 0.93),
        ExtractionPattern("personal", "name", r"\bt[êe]n (?:tôi|em|mình|mình) (?:l[aà]\s+)?([A-Za-zÀ-ỹ][A-Za-zÀ-ỹ' -]{1,60})", 0.93),
        ExtractionPattern("personal", "name", r"\bI'm\s+([A-Za-zÀ-ỹ][A-Za-zÀ-ỹ' -]{1,40}),?\s+(?:nice to meet|pleased to)", 0.9),
        # "I am [Name]" in greeting context  
        ExtractionPattern("personal", "name", r"\b(?:hello|hi|hey)\s+(?:i am|i'm)\s+([A-Za-zÀ-ỹ][A-Za-zÀ-ỹ' -]{1,40})", 0.9),
        # "tôi/em/mình là [Name] đây/nè/nha" — with context clue  
        ExtractionPattern("personal", "name", r"\b(?:t[oô]i|em|mình)\s+l[aà]\s+([A-Za-zÀ-ỹ][A-Za-zÀ-ỹ' -]{1,40})\s+(?:đây|day|n[eè]|nha|nhé|nhe|here)\b", 0.93),
        # greeting + "tôi/em là [Name]" — accepts optional trailing particles (day/đây/nè...)
        ExtractionPattern(
            "personal",
            "name",
            r"\b(?:hello|hi|hey|ch[aà]o|xin ch[aà]o|alo)\s+(?:t[oô]i|em|mình)\s+l[aà]\s+([A-Za-zÀ-ỹ][A-Za-zÀ-ỹ' -]{1,40}?)(?:\s+(?:đây|day|n[eè]|nha|nhé|nhe|here))?(?=[\s.!?,;:]*$)",
            0.92
        ),
        # pure intro turn: "tôi là Nguyễn Thành Đô"
        ExtractionPattern(
            "personal",
            "name",
            r"^\s*(?:t[oô]i|em|mình)\s+l[aà]\s+([A-Za-zÀ-ỹ][A-Za-zÀ-ỹ' -]{1,60})\s*[.!?,;:]*\s*$",
            0.91
        ),
        ExtractionPattern("communication", "user_nickname", r"\b(?:call me|you can call me|my nickname is)\s+([A-Za-z0-9][A-Za-z0-9_-]{2,63})\b", 0.94),
        ExtractionPattern("communication", "user_nickname", r"\bbạn có thể gọi tôi là\s+([A-Za-z0-9][A-Za-z0-9_-]{2,63})\b", 0.94),
        ExtractionPattern("communication", "user_nickname", r"\b(?:gọi|goi)\s+(?:tôi|em|mình)\s+(?:l[aà]\s+)?([A-Za-z0-9][A-Za-z0-9_-]{2,63})\b", 0.92),
        ExtractionPattern("communication", "user_nickname", r"\b(?:call me|you can call me)\s+([A-Za-zÀ-ỹ][A-Za-zÀ-ỹ' -]{1,40})", 0.92),
        ExtractionPattern("communication", "user_nickname", r"\bbạn có thể gọi tôi là\s+([A-Za-zÀ-ỹ][A-Za-zÀ-ỹ' -]{1,40})", 0.92),
        ExtractionPattern("communication", "user_nickname", r"\b(?:gọi|goi)\s+(?:tôi|em|mình)\s+(?:l[aà]\s+)?([A-Za-zÀ-ỹ][A-Za-zÀ-ỹ' -]{1,40})", 0.9),

        # Age - English
        ExtractionPattern("personal", "age", r"\b(?:i am|i'm)\s+(\d{1,3})\s*(?:years old|yrs? old|yo)\b", 0.93),
        # Age - Vietnamese: "tôi 25 tuổi", "em 20 tuổi", "mình 30 tuổi"
        ExtractionPattern("personal", "age", r"\b(?:t[oô]i|em|mình|mình)\s+(\d{1,3})\s+tu[oổ]i\b", 0.93),
        # "năm nay tôi 25 tuổi", "năm nay em 25"
        ExtractionPattern("personal", "age", r"\bn[aă]m nay\s+(?:t[oô]i|em|mình|mình)\s+(\d{1,3})(?:\s+tu[oổ]i)?\b", 0.92),
        # "tôi năm nay 25 tuổi"
        ExtractionPattern("personal", "age", r"\b(?:t[oô]i|em|mình|mình)\s+n[aă]m nay\s+(\d{1,3})(?:\s+tu[oổ]i)?\b", 0.92),

        # Gender - Vietnamese
        ExtractionPattern("personal", "gender", r"\b(?:t[oô]i|em|mình|mình)\s+l[aà]\s+(nam|nữ|nu|con trai|con g[aá]i|male|female)\b", 0.93),
        ExtractionPattern("personal", "gender", r"\b(?:giới tính|gioi tinh)(?:\s+(?:của|cua))?(?:\s+(?:t[oô]i|em|mình))?\s+(?:l[aà]\s+)?(nam|nữ|nu|male|female|con trai|con g[aá]i)\b", 0.93),
        # English
        ExtractionPattern("personal", "gender", r"\b(?:i am|i'm)\s+(?:a\s+)?(male|female|man|woman|boy|girl)\b", 0.93),
        ExtractionPattern("personal", "gender", r"\bmy gender is\s+(male|female|non-?binary|other)\b", 0.95),

        # Birthday
        ExtractionPattern("personal", "birth_date", r"\b(?:my birthday is|i was born on)\s+([0-9]{1,2}[/-][0-9]{1,2}(?:[/-][0-9]{2,4})?)", 0.9),
        ExtractionPattern("personal", "birth_date", r"\bt[oô]i sinh (?:ng[aà]y\s+)?([0-9]{1,2}[/-][0-9]{1,2}(?:[/-][0-9]{2,4})?)", 0.9),
        ExtractionPattern("personal", "birth_date", r"\b(?:em|mình) sinh (?:ng[aà]y\s+)?([0-9]{1,2}[/-][0-9]{1,2}(?:[/-][0-9]{2,4})?)", 0.88),
        # "sinh nhật tôi ngày 15/3"
        ExtractionPattern("personal", "birth_date", r"\bsinh nh[aậ]t (?:(?:của|cua)?\s*(?:t[oô]i|em|mình))\s+(?:(?:l[aà]|ng[aà]y)\s+)?([0-9]{1,2}[/-][0-9]{1,2}(?:[/-][0-9]{2,4})?)", 0.9),

        # Location
        ExtractionPattern("personal", "location", r"\b(?:i live in|i am from|i'm from|i am based in)\s+([A-Za-zÀ-ỹ0-9 ,.'-]{2,80})", 0.8),
        ExtractionPattern("personal", "location", r"\bt[oô]i (?:sống|ở|đến từ|đang ở)\s+(?:tại|ở)?\s*([A-Za-zÀ-ỹ0-9 ,.'-]{2,80})", 0.8),
        ExtractionPattern("personal", "location", r"\b(?:em|mình) (?:sống|ở|đến từ|đang ở)\s+(?:tại|ở)?\s*([A-Za-zÀ-ỹ0-9 ,.'-]{2,80})", 0.78),
        # "quê tôi ở Đà Nẵng"
        ExtractionPattern("personal", "location", r"\bqu[eê] (?:t[oô]i|em|mình)\s+(?:ở\s+)?([A-Za-zÀ-ỹ0-9 ,.'-]{2,80})", 0.82),

        # Timezone
        ExtractionPattern("personal", "timezone", r"\b(?:my timezone is|i(?:'m| am) in timezone)\s+([A-Za-z0-9_+:/-]{2,40})", 0.88),
        ExtractionPattern("personal", "timezone", r"\bm[uú]i gi[oờ] (?:của tôi|cua toi)?(?: là| la)?\s*([A-Za-z0-9_+:/-]{2,40})", 0.88),

        # Education
        ExtractionPattern("personal", "education", r"\b(?:i (?:study|studied|am studying) at|i go to|i attend)\s+([A-Za-zÀ-ỹ0-9 ,.'-]{2,80})", 0.82),
        ExtractionPattern("personal", "education", r"\b(?:i have a|i got (?:a|my))\s+(bachelor|master|phd|doctorate|degree|diploma)\b", 0.84),
        ExtractionPattern("personal", "education", r"\b(?:t[oô]i|em|mình) (?:học|đang học|đã học)\s+(?:ở|tại)?\s*([A-Za-zÀ-ỹ0-9 ,.'-]{2,80})", 0.8),
        ExtractionPattern("personal", "education", r"\b(?:t[oô]i|em|mình) l[aà]\s+(?:sinh viên|sv|học sinh|hs)(?:\s+(?:trường|trg)?\s*([A-Za-zÀ-ỹ0-9 ,.'-]{2,80}))?", 0.82),
        ExtractionPattern("personal", "education", r"\b(?:t[oô]i|em|mình) (?:tốt nghiệp|tot nghiep)\s+([A-Za-zÀ-ỹ0-9 ,.'-]{2,80})", 0.82),

        # Nationality
        ExtractionPattern("personal", "nationality", r"\b(?:i am|i'm)\s+(vietnamese|american|korean|japanese|chinese|british|french|german|indian|australian|canadian|thai|singaporean|malaysian|indonesian|filipino|taiwanese)\b", 0.88),
        ExtractionPattern("personal", "nationality", r"\b(?:t[oô]i|em|mình) l[aà]\s+(?:người\s+)?(việt nam|viet nam|hàn quốc|nhật bản|trung quốc|mỹ|mĩ|anh|pháp|đức|ấn độ|úc|canada|thái lan|singapore|malaysia|indonesia|philippines|đài loan)\b", 0.88),

        # Native language
        ExtractionPattern("personal", "native_language", r"\b(?:my native language is|my mother tongue is|i speak)\s+([A-Za-zÀ-ỹ ]{2,40})\s+(?:natively|as (?:my )?native)\b", 0.86),
        ExtractionPattern("personal", "native_language", r"\b(?:tiếng mẹ đẻ|tieng me de|ngôn ngữ mẹ đẻ)(?:\s+(?:của|cua))?(?:\s+(?:tôi|em|mình))?\s+(?:l[aà]\s+)?([A-Za-zÀ-ỹ ]{2,40})\b", 0.86),

        # Marital status / relationship
        ExtractionPattern("personal", "marital_status", r"\b(?:i am|i'm)\s+(single|married|divorced|engaged|in a relationship)\b", 0.86),
        ExtractionPattern("personal", "marital_status", r"\b(?:t[oô]i|em|mình) (?:đã|đang)?\s*(?:l[aà]\s+)?(kết hôn|ket hon|độc thân|doc than|đính hôn|dinh hon|ly hôn|ly hon|có gia đình|có gđ|chưa lập gia đình)\b", 0.86),

        # ---------------------------
        # Work profile
        # ---------------------------
        ExtractionPattern("work", "occupation", r"\b(?:i work as|i(?:'m| am) working as)\s+(?:an?\s+)?([A-Za-zÀ-ỹ][A-Za-zÀ-ỹ0-9+/#& .-]{2,60})", 0.88),
        ExtractionPattern("work", "occupation", r"\b(?:my job is|my role is)\s+(?:an?\s+)?([A-Za-zÀ-ỹ][A-Za-zÀ-ỹ0-9+/#& .-]{2,60})", 0.86),
        ExtractionPattern("work", "occupation", r"\bt[oô]i (?:l[aà]|đang l[aà])\s+(?:một\s+)?([A-Za-zÀ-ỹ][A-Za-zÀ-ỹ0-9+/#& .-]{2,60})", 0.82),
        ExtractionPattern("work", "occupation", r"\bt[oô]i (?:l[aà]m|đang làm) (?:vị trí|vi tri|vai trò|vai tro|công việc|cong viec|nghề(?: nghiệp)?|nghe(?: nghiep)?)\s*(?:l[aà]|la)?\s*([A-Za-zÀ-ỹ][A-Za-zÀ-ỹ0-9+/#& .-]{2,60})", 0.84),
        ExtractionPattern("work", "occupation", r"\b(?:nghề(?: nghiệp)?|công việc) (?:của tôi|cua toi) (?:là|la)\s+([A-Za-zÀ-ỹ][A-Za-zÀ-ỹ0-9+/#& .-]{2,60})", 0.86),

        ExtractionPattern("work", "company", r"\b(?:i work at|i work for|my company is)\s+([A-Za-zÀ-ỹ0-9& .,'-]{2,80})", 0.84),
        ExtractionPattern("work", "company", r"\bt[oô]i (?:l[aà]m việc|đang làm) (?:tại|ở|cho)\s+([A-Za-zÀ-ỹ0-9& .,'-]{2,80})", 0.84),

        # ---------------------------
        # Skills
        # ---------------------------
        ExtractionPattern("skill", "has_skill", r"\b(?:i know|i am good at|i'm good at|i have experience with|i can use)\s+([A-Za-zÀ-ỹ0-9+#./& ,'-]{2,80})", 0.78, split_values=True),
        ExtractionPattern("skill", "has_skill", r"\bt[oô]i (?:biết|giỏi|có kinh nghiệm|thành thạo)\s+([A-Za-zÀ-ỹ0-9+#./& ,'-]{2,80})", 0.78, split_values=True),

        ExtractionPattern("skill", "learning", r"\b(?:i am learning|i'm learning|i want to learn)\s+([A-Za-zÀ-ỹ0-9+#./& ,'-]{2,80})", 0.74, split_values=True),
        ExtractionPattern("skill", "learning", r"\bt[oô]i (?:đang học|muốn học)\s+([A-Za-zÀ-ỹ0-9+#./& ,'-]{2,80})", 0.74, split_values=True),

        # ---------------------------
        # Preferences / hobbies
        # ---------------------------
        ExtractionPattern("preference", "likes", r"\b(?:i like|i love|i enjoy|i prefer)\s+([A-Za-zÀ-ỹ0-9+#./& ,'-]{2,80})", 0.74, split_values=True),
        ExtractionPattern("preference", "likes", r"\bt[oô]i(?:\s+r[aấ]t)?\s+(?:th[ií]ch|y[eê]u th[ií]ch|[uư]u ti[eê]n)\s+([A-Za-zÀ-ỹ0-9+#./& ,'-]{2,80})", 0.74, split_values=True),

        ExtractionPattern("preference", "dislikes", r"\b(?:i don't like|i do not like|i hate|i avoid)\s+([A-Za-zÀ-ỹ0-9+#./& ,'-]{2,80})", 0.74, split_values=True),
        ExtractionPattern("preference", "dislikes", r"\bt[oô]i (?:kh[oô]ng th[ií]ch|gh[eé]t|tr[aá]nh)\s+([A-Za-zÀ-ỹ0-9+#./& ,'-]{2,80})", 0.74, split_values=True),

        ExtractionPattern("preference", "favorite", r"\bmy favorite (?:\w+\s+)?is\s+([A-Za-zÀ-ỹ0-9+#./& ,'-]{2,80})", 0.8, split_values=True),
        ExtractionPattern("preference", "favorite", r"\b(?:thứ|điều|m[oó]n) tôi (?:thích|yêu thích) nhất (?:là|la)\s+([A-Za-zÀ-ỹ0-9+#./& ,'-]{2,80})", 0.8, split_values=True),

        ExtractionPattern("preference", "hobby", r"\b(?:my hobbies are|my hobby is)\s+([A-Za-zÀ-ỹ0-9+#./& ,'-]{2,80})", 0.82, split_values=True),
        ExtractionPattern("preference", "hobby", r"\bsở thích của tôi (?:là|la|gồm)\s+([A-Za-zÀ-ỹ0-9+#./& ,'-]{2,80})", 0.82, split_values=True),

        # ---------------------------
        # Coding style
        # ---------------------------
        ExtractionPattern("coding_style", "primary_language", r"\bi(?: mainly| mostly)? (?:code|program|develop) in\s+([A-Za-z0-9+#. -]{2,40})", 0.84, split_values=True),
        ExtractionPattern("coding_style", "primary_language", r"\b(?:ng[oô]n ngữ|ngon ngu) (?:ch[ií]nh|tôi (?:hay|thường) dùng) (?:là|la)\s+([A-Za-z0-9+#. -]{2,40})", 0.84, split_values=True),

        ExtractionPattern("coding_style", "editor", r"\b(?:my daily (?:editor|ide) is|my (?:editor|ide) is)\s+([A-Za-z0-9][A-Za-z0-9+._/-]{1,63})\b", 0.93),
        ExtractionPattern("coding_style", "editor", r"\b(?:my (?:editor|ide) is|i use)\s+(vscode|visual studio code|vim|neovim|cursor|intellij|pycharm|webstorm)\b", 0.88),
        ExtractionPattern("coding_style", "editor", r"\bt[oô]i d[uù]ng\s+(vscode|visual studio code|vim|neovim|cursor|intellij|pycharm|webstorm)\b", 0.88),

        ExtractionPattern("coding_style", "preferred_os", r"\b(?:i use|i am on)\s+(windows|macos|linux|ubuntu|debian|arch)\b", 0.84),
        ExtractionPattern("coding_style", "preferred_os", r"\bt[oô]i d[uù]ng\s+(windows|macos|linux|ubuntu|debian|arch)\b", 0.84),

        ExtractionPattern("coding_style", "coding_practice", r"\b(?:i (?:always|usually) use)\s+(type hints?|docstrings?|unit tests?|comments?|async)\b", 0.82),
        ExtractionPattern("coding_style", "coding_practice", r"\bt[oô]i (?:luôn|thường) d[uù]ng\s+(type hints?|docstrings?|unit tests?|comments?|async)\b", 0.82),

        # ---------------------------
        # Communication style
        # ---------------------------
        ExtractionPattern("communication", "response_language", r"\b(?:reply|respond|answer) (?:in|using)\s+(english|vietnamese|tiếng anh|tiếng việt)\b", 0.9),
        ExtractionPattern("communication", "response_language", r"\b(?:trả lời|phản hồi) (?:bằng|bằng tiếng)?\s*(english|vietnamese|tiếng anh|tiếng việt)\b", 0.9),
        ExtractionPattern("communication", "response_language", r"\b(?:tra loi|phan hoi) (?:bang|bang tieng)?\s*(english|vietnamese|tieng anh|tieng viet)\b", 0.9),

        ExtractionPattern("communication", "response_style", r"\b(?:keep it|be|reply)\s+(short|concise|brief|detailed|step-by-step)\b", 0.88),
        ExtractionPattern("communication", "response_style", r"\b(?:trả lời )?(ngắn gọn|chi tiết|từng bước)\b", 0.88),
        ExtractionPattern("communication", "response_style", r"\b(?:tra loi )?(ngan gon|chi tiet|tung buoc)\b", 0.88),

        ExtractionPattern("communication", "tone_preference", r"\b(?:use a|be)\s+(formal|casual|friendly|professional)\s+tone\b", 0.86),
        ExtractionPattern("communication", "tone_preference", r"\b(?:xưng hô|giọng điệu)\s+(lịch sự|thoải mái|thân thiện|chuyên nghiệp)\b", 0.86),

        # ---------------------------
        # Workspace/project scoped memory
        # ---------------------------
        ExtractionPattern("tech_stack", "project_uses_tech", r"\b(?:my project|our project|this project|project này|dự án này)\s+(?:uses|use|dùng|sử dụng)\s+([A-Za-z0-9+#./& ,'-]{2,120})", 0.82, subject="Project", scope="workspace", split_values=True),
        ExtractionPattern("tech_stack", "project_framework", r"\b(?:we use|we are using|chúng tôi dùng|team dùng)\s+([A-Za-z0-9+#./& ,'-]{2,80})\s+(?:for frontend|for backend|framework|library)\b", 0.78, subject="Project", scope="workspace", split_values=True),

        ExtractionPattern("project_context", "project_goal", r"\b(?:project goal is|goal of this project is|mục tiêu (?:dự án|project) (?:là|la))\s+([^.;!\n]{2,120})", 0.84, subject="Project", scope="workspace"),
        ExtractionPattern("project_context", "project_deadline", r"\b(?:deadline is|due date is|hạn chót(?: của dự án)? (?:là|la))\s+([A-Za-z0-9/:-]{2,40})", 0.84, subject="Project", scope="workspace"),

        ExtractionPattern("rule", "project_rule", r"\b(?:for this project|trong dự án này),?\s*(?:always|hãy luôn|luôn)\s+([^.;!\n]{2,120})", 0.78, subject="Project", scope="workspace"),
        ExtractionPattern("convention", "naming_convention", r"\b(?:we use|this project uses|dự án dùng)\s+(camelcase|pascalcase|snake_case|kebab-case)\b", 0.8, subject="Project", scope="workspace"),
    ]

    PREFERENCE_SIGNALS: List[PreferenceSignal] = [
        PreferenceSignal("communication", "emoji_usage", "off", r"\b(?:no emoji|don't use emoji|do not use emoji|avoid emoji|đừng dùng emoji|không dùng emoji)\b", 0.95),
        PreferenceSignal("communication", "emoji_usage", "on", r"\b(?:use emoji|có thể dùng emoji|dùng emoji)\b", 0.8),
        PreferenceSignal("communication", "response_format", "bullet_points", r"\b(?:bullet points?|gạch đầu dòng)\b", 0.9),
        PreferenceSignal("communication", "response_style", "concise", r"\b(?:exactly\s+\d+\s+)?concise\s+bullet points?\b", 0.94),
        PreferenceSignal("communication", "response_action_line", "required", r"\b(?:end with|ending with).{0,40}\baction:\b", 0.94),
        PreferenceSignal("communication", "response_format", "table", r"\b(?:as a table|dạng bảng)\b", 0.88),
        PreferenceSignal("coding_style", "code_examples", "on", r"\b(?:include code examples|show code examples|thêm ví dụ code|có ví dụ code)\b", 0.9),
        PreferenceSignal("coding_style", "code_examples", "off", r"\b(?:no code examples|không cần ví dụ code|đừng đưa ví dụ code)\b", 0.9),
    ]

    CANONICAL_VALUES: Dict[str, str] = {
        "visual studio code": "VS Code",
        "vscode": "VS Code",
        "intellij": "IntelliJ",
        "pycharm": "PyCharm",
        "webstorm": "WebStorm",
        "typescript": "TypeScript",
        "javascript": "JavaScript",
        "nodejs": "Node.js",
        "node.js": "Node.js",
        "reactjs": "React",
        "nextjs": "Next.js",
        "next.js": "Next.js",
        "nestjs": "NestJS",
        "postgres": "PostgreSQL",
        "postgresql": "PostgreSQL",
        "mongo": "MongoDB",
        "mongodb": "MongoDB",
        "macos": "macOS",
        "windows": "Windows",
        "linux": "Linux",
        "ubuntu": "Ubuntu",
        "debian": "Debian",
        "arch": "Arch Linux",
        "tiếng việt": "Vietnamese",
        "vietnamese": "Vietnamese",
        "tiếng anh": "English",
        "tieng viet": "Vietnamese",
        "tieng anh": "English",
        "english": "English",
        "short": "concise",
        "brief": "concise",
        "concise": "concise",
        "ngắn gọn": "concise",
        "ngan gon": "concise",
        "detailed": "detailed",
        "chi tiết": "detailed",
        "chi tiet": "detailed",
        "step-by-step": "step_by_step",
        "từng bước": "step_by_step",
        "tung buoc": "step_by_step",
        "formal": "formal",
        "lịch sự": "formal",
        "casual": "casual",
        "thoải mái": "casual",
        "friendly": "friendly",
        "thân thiện": "friendly",
        "professional": "professional",
        "chuyên nghiệp": "professional",
        # Gender
        "nam": "Nam",
        "male": "Nam",
        "man": "Nam",
        "boy": "Nam",
        "con trai": "Nam",
        "nữ": "Nữ",
        "nu": "Nữ",
        "female": "Nữ",
        "woman": "Nữ",
        "girl": "Nữ",
        "con gái": "Nữ",
        "con gai": "Nữ",
        # Marital status
        "single": "Độc thân",
        "độc thân": "Độc thân",
        "doc than": "Độc thân",
        "chưa lập gia đình": "Độc thân",
        "married": "Đã kết hôn",
        "kết hôn": "Đã kết hôn",
        "ket hon": "Đã kết hôn",
        "có gia đình": "Đã kết hôn",
        "có gđ": "Đã kết hôn",
        "divorced": "Ly hôn",
        "ly hôn": "Ly hôn",
        "ly hon": "Ly hôn",
        "engaged": "Đính hôn",
        "đính hôn": "Đính hôn",
        "dinh hon": "Đính hôn",
        "in a relationship": "Đang hẹn hò",
        # Nationality
        "việt nam": "Việt Nam",
        "viet nam": "Việt Nam",
        "vietnamese": "Vietnamese",
        "american": "American",
        "korean": "Korean",
        "japanese": "Japanese",
        "chinese": "Chinese",
    }

    _STOPWORDS = {
        "a", "an", "the", "it", "this", "that", "something", "someone",
        "này", "kia", "đó", "đấy", "thứ", "điều"
    }

    _NAME_DISALLOWED_TOKENS = {
        "my", "name", "is", "toi", "tôi", "ten", "tên", "la", "là",
        "and", "with", "va", "và", "hello", "hi", "hey", "xin", "chao", "chào",
    }

    _OCCUPATION_BAD_SNIPPETS = {
        "bài thơ", "bai tho", "poem", "story", "câu chuyện", "cau chuyen",
        "tin nhắn", "tin nhan", "message", "hello", "xin chào", "xin chao",
    }

    _COMMON_VIETNAMESE_SURNAMES = {
        "nguyen", "tran", "le", "pham", "hoang", "huynh", "phan", "vu", "vo",
        "dang", "bui", "do", "ho", "ngo", "duong", "ly", "dinh", "mai", "trinh",
        "luu", "cao", "chau", "truong", "ha", "ta", "tong",
    }

    _OCCUPATION_HINT_PHRASES = {
        "bac si", "ky su", "lap trinh vien", "sinh vien", "hoc sinh", "giao vien",
        "nhan vien", "quan ly", "ke toan", "luat su", "duoc si", "y ta",
        "software engineer", "backend developer", "frontend developer",
        "product manager", "project manager", "data analyst", "data scientist",
        "ux designer", "ui designer",
    }

    _OCCUPATION_HINT_TOKENS = {
        "developer", "engineer", "designer", "manager", "analyst", "scientist",
        "architect", "consultant", "intern", "teacher", "doctor", "nurse",
        "lawyer", "accountant", "student", "freelancer", "programmer", "coder",
        "bac", "si", "ky", "su", "lap", "trinh", "vien", "nhan", "quan", "ly",
        "ke", "toan", "luat", "duoc", "y", "ta",
    }

    _FACT_TO_PREFERENCE = {
        "name": ("personal", "user_name"),
        "user_nickname": ("communication", "user_nickname"),
        "age": ("personal", "user_age"),
        "gender": ("personal", "gender"),
        "location": ("personal", "location"),
        "timezone": ("personal", "timezone"),
        "education": ("personal", "education"),
        "nationality": ("personal", "nationality"),
        "native_language": ("personal", "native_language"),
        "marital_status": ("personal", "marital_status"),
        "occupation": ("work", "occupation"),
        "company": ("work", "company"),
        "primary_language": ("coding_style", "primary_language"),
        "editor": ("coding_style", "editor"),
        "preferred_os": ("coding_style", "preferred_os"),
        "response_language": ("communication", "response_language"),
        "response_style": ("communication", "response_style"),
        "tone_preference": ("communication", "tone"),
    }

    AI_ALLOWED_CATEGORIES = {
        "personal", "preference", "work", "skill",
        "coding_style", "communication", "relationship",
        "project_context", "tech_stack", "architecture",
        "convention", "domain", "team", "rule", "reference", "note", "general"
    }

    def __init__(self, user_id: str):
        self.user_id = user_id
        self._compiled_patterns = [
            (pattern, re.compile(pattern.pattern, re.IGNORECASE | re.UNICODE))
            for pattern in self.PATTERNS
        ]
        self._compiled_preference_signals = [
            (signal, re.compile(signal.pattern, re.IGNORECASE | re.UNICODE))
            for signal in self.PREFERENCE_SIGNALS
        ]

    def extract_enriched(
        self,
        message: str,
        workspace_id: Optional[str] = None,
        source_id: Optional[str] = None,
    ) -> ExtractionResult:
        """Extract both facts and normalized preferences from one user message."""
        facts = self.extract_with_patterns(
            message=message,
            workspace_id=workspace_id,
            source_id=source_id
        )
        preferences = self.extract_preferences(message=message, facts=facts)
        return ExtractionResult(facts=facts, preferences=preferences)

    def extract_with_patterns(
        self,
        message: str,
        workspace_id: Optional[str] = None,
        source_id: Optional[str] = None
    ) -> List[LocalFact]:
        """
        Extract facts using regex patterns.
        Fast and doesn't require API calls.

        Args:
            message: User message to analyze
            workspace_id: Active workspace ID for workspace-scoped memories
            source_id: Conversation/message source ID

        Returns:
            List of extracted facts
        """
        text = self._normalize_space(message)
        if not text:
            return []

        facts: List[LocalFact] = []
        now = datetime.now()

        for pattern, regex in self._compiled_patterns:
            if pattern.scope == "workspace" and not workspace_id:
                continue

            for match in regex.finditer(text):
                if self._is_truncated_capture(text, match, 1):
                    continue
                raw_value = match.group(1).strip() if match.lastindex else match.group(0).strip()
                candidates = self._split_values(raw_value) if pattern.split_values else [raw_value]

                for value in candidates:
                    normalized = self._normalize_fact_value(
                        value=value,
                        predicate=pattern.predicate,
                        category=pattern.category
                    )
                    if not normalized:
                        continue

                    fact = LocalFact(
                        id=str(uuid.uuid4()),
                        source_type="pattern_extraction",
                        source_id=source_id,
                        workspace_id=workspace_id if pattern.scope == "workspace" else None,
                        subject=pattern.subject,
                        predicate=pattern.predicate,
                        object=normalized,
                        category=pattern.category,
                        confidence=pattern.confidence,
                        created_at=now,
                        updated_at=now
                    )
                    facts.append(fact)

        return self._deduplicate_facts(facts)

    def extract_preferences(
        self,
        message: str,
        facts: List[LocalFact]
    ) -> List[ExtractedPreference]:
        """Extract normalized user preferences from both facts and direct signals."""
        prefs: List[ExtractedPreference] = []

        for fact in facts:
            pref_meta = self._FACT_TO_PREFERENCE.get(fact.predicate)
            if not pref_meta:
                continue

            category, key = pref_meta
            value = fact.object

            if fact.predicate == "coding_practice":
                key = f"practice_{self._slugify(fact.object)}"
                value = "on"
                category = "coding_style"

            prefs.append(
                ExtractedPreference(
                    category=category,
                    key=key,
                    value=value,
                    confidence=min(0.95, fact.confidence + 0.05)
                )
            )

        text = self._normalize_space(message)
        for signal, regex in self._compiled_preference_signals:
            if regex.search(text):
                prefs.append(
                    ExtractedPreference(
                        category=signal.category,
                        key=signal.key,
                        value=signal.value,
                        confidence=signal.confidence
                    )
                )

        bullet_count_match = re.search(r"\bexactly\s+([2-9])\s+(?:concise\s+)?bullet points?\b", text, re.IGNORECASE)
        if bullet_count_match:
            prefs.append(
                ExtractedPreference(
                    category="communication",
                    key="response_bullet_count",
                    value=str(bullet_count_match.group(1)),
                    confidence=0.95,
                )
            )
        if (
            re.search(r"\baction\s*:", text, re.IGNORECASE)
            and re.search(r"\b(?:end with|ending with|starts? with|line that starts? with)\b", text, re.IGNORECASE)
        ):
            prefs.append(
                ExtractedPreference(
                    category="communication",
                    key="response_action_line",
                    value="required",
                    confidence=0.96,
                )
            )

        # Keep strongest value per (category, key)
        dedup: Dict[Tuple[str, str], ExtractedPreference] = {}
        for pref in prefs:
            dedup_key = (pref.category, pref.key)
            existing = dedup.get(dedup_key)
            if existing is None or pref.confidence >= existing.confidence:
                dedup[dedup_key] = pref

        return list(dedup.values())

    def extract_from_conversation(
        self,
        messages: List[Tuple[str, str]]  # [(role, content), ...]
    ) -> List[LocalFact]:
        """
        Extract facts from a conversation.
        Only processes user messages.

        Args:
            messages: List of (role, content) tuples

        Returns:
            All extracted facts
        """
        all_facts: List[LocalFact] = []

        for role, content in messages:
            if role == "user":
                facts = self.extract_with_patterns(content)
                all_facts.extend(facts)

        return self._deduplicate_facts(all_facts)

    def _deduplicate_facts(self, facts: List[LocalFact]) -> List[LocalFact]:
        """Remove duplicate facts by scope + subject + predicate + object."""
        seen = set()
        unique: List[LocalFact] = []

        for fact in facts:
            key = (
                (fact.workspace_id or "").strip().lower(),
                fact.subject.strip().lower(),
                fact.predicate.strip().lower(),
                fact.object.strip().lower(),
            )
            if key not in seen:
                seen.add(key)
                unique.append(fact)

        return unique

    def _split_values(self, value: str) -> List[str]:
        """Split list-like captured text into candidate values."""
        text = self._normalize_space(value)
        if not text:
            return []

        # Keep only the first sentence-like span to avoid capturing
        # subsequent instructions (e.g. "..., trả lời ngắn gọn").
        text = re.split(r"[.!?\n]+", text, maxsplit=1)[0].strip()
        if not text:
            return []

        if "," in text or ";" in text or re.search(r"\b(?:and|và|va)\b|&", text, re.IGNORECASE):
            parts = re.split(r"\s*(?:,|;|\band\b|\bvà\b|\bva\b|&)\s*", text, flags=re.IGNORECASE)
        else:
            parts = [text]

        results: List[str] = []
        for part in parts:
            cleaned = self._normalize_space(part).strip(" \t\r\n'\"`.,;:!?()[]{}")
            if cleaned:
                results.append(cleaned)
        return results

    def _normalize_fact_value(self, value: str, predicate: str, category: str) -> Optional[str]:
        cleaned = self._normalize_space(value).strip(" \t\r\n'\"`.,;:!?()[]{}")
        if not cleaned:
            return None

        # Drop obvious stopwords and tiny artifacts
        lowered = cleaned.lower()
        if len(lowered) < 2 or lowered in self._STOPWORDS:
            return None

        if predicate == "age":
            digits = re.sub(r"[^\d]", "", cleaned)
            if not digits:
                return None
            age = int(digits)
            if age < 5 or age > 110:
                return None
            return str(age)

        if predicate in {"name", "user_nickname"}:
            return self._normalize_name(cleaned)

        if predicate == "occupation":
            return self._normalize_occupation(cleaned)

        if predicate == "gender":
            return self._canonicalize(cleaned)

        if predicate == "marital_status":
            return self._canonicalize(cleaned)

        if predicate == "nationality":
            return self._canonicalize(cleaned)

        if predicate in {"response_language", "response_style", "tone_preference"}:
            return self._canonicalize(cleaned)

        if predicate in {
            "primary_language", "editor", "preferred_os", "coding_practice",
            "project_uses_tech", "project_framework"
        }:
            return self._canonicalize(cleaned)

        if category in {"preference", "skill", "tech_stack"}:
            return lowered

        if len(cleaned) > 120:
            return cleaned[:120].rstrip()

        return cleaned

    def _normalize_name(self, value: str) -> Optional[str]:
        tokens = [t for t in value.split() if t]
        if not tokens or len(tokens) > 5:
            return None

        if self._looks_like_identifier_name(value):
            return value.strip()

        lowered_tokens = [t.lower() for t in tokens]
        if any(any(ch.isdigit() for ch in t) for t in lowered_tokens):
            return None
        if any(t in self._NAME_DISALLOWED_TOKENS for t in lowered_tokens):
            return None
        if self._looks_like_occupation_phrase(value):
            return None
        if len(tokens) >= 4 and len(set(lowered_tokens)) < len(lowered_tokens):
            # Repeated words in long names are usually extraction noise.
            return None

        normalized_tokens = []
        for token in tokens:
            if len(token) > 24:
                return None
            if len(token) == 1:
                normalized_tokens.append(token.upper())
            elif token.islower():
                normalized_tokens.append(token.capitalize())
            else:
                normalized_tokens.append(token)

        return " ".join(normalized_tokens)

    def _looks_like_identifier_name(self, value: str) -> bool:
        compact = self._normalize_space(value).strip(" \t\r\n'\"`.,;:!?()[]{}")
        if not compact or " " in compact:
            return False
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,63}", compact):
            return False
        return bool(re.search(r"[A-Za-z]", compact))

    def _is_truncated_capture(self, text: str, match: re.Match[str], group: int) -> bool:
        """Reject regex captures that stop before the full identifier/token ends."""
        try:
            end = match.end(group)
        except (IndexError, ValueError):
            return False
        if end >= len(text):
            return False
        if not re.match(r"[A-Za-z0-9_-]", text[end]):
            return False
        captured = (match.group(group) or "").strip()
        if not captured:
            return False
        return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{1,63}", captured))

    def _normalize_occupation(self, value: str) -> Optional[str]:
        text = self._normalize_space(value).strip(" \t\r\n'\"`.,;:!?()[]{}")
        if not text:
            return None

        lowered = text.lower()
        lowered = re.sub(r"^(?:a|an|the|một|mot)\s+", "", lowered).strip()
        if not lowered:
            return None

        # In intro phrases like "hello tôi là ... day/đây", trailing particles
        # are not occupations and should be discarded.
        lowered_tokens = lowered.split()
        if lowered_tokens and lowered_tokens[-1] in {"đây", "day", "nè", "ne", "nha", "nhé", "nhe", "here"}:
            return None
        if self._looks_like_person_name(text):
            return None

        if any(snippet in lowered for snippet in self._OCCUPATION_BAD_SNIPPETS):
            return None

        if len(lowered.split()) > 6:
            return None

        # Phrases like "một bài thơ với", "write a ...", "làm ... với"
        # are actions, not occupations.
        if re.search(r"\b(with|với|for|để|de|to)\b", lowered) and len(lowered.split()) >= 3:
            return None
        if re.match(r"^(?:l[aà]m|lam|vi[ếe]t|viet|create|write|build|make)\b", lowered):
            return None

        if len(lowered) < 2:
            return None

        return self._canonicalize(lowered)

    def _normalize_for_matching(self, value: str) -> str:
        normalized = unicodedata.normalize("NFD", value or "")
        normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
        normalized = normalized.replace("đ", "d").replace("Đ", "D")
        normalized = normalized.lower()
        normalized = re.sub(r"[^a-z0-9' -]+", " ", normalized)
        return re.sub(r"\s+", " ", normalized).strip()

    def _looks_like_occupation_phrase(self, value: str) -> bool:
        normalized = self._normalize_for_matching(value)
        if not normalized:
            return False

        if any(phrase in normalized for phrase in self._OCCUPATION_HINT_PHRASES):
            return True

        tokens = [token for token in normalized.split() if token]
        return any(token in self._OCCUPATION_HINT_TOKENS for token in tokens)

    def _looks_like_person_name(self, value: str) -> bool:
        raw = self._normalize_space(value).strip(" \t\r\n'\"`.,;:!?()[]{}")
        if not raw:
            return False

        tokens = [token.strip(" \t\r\n'\"`.,;:!?()[]{}") for token in raw.split() if token.strip()]
        if len(tokens) < 2 or len(tokens) > 5:
            return False

        for token in tokens:
            if len(token) < 2 or len(token) > 24:
                return False
            if re.search(r"\d", token):
                return False
            if not re.fullmatch(r"[A-Za-zÀ-ỹ'-]+", token):
                return False

        normalized = self._normalize_for_matching(raw)
        if not normalized:
            return False
        norm_tokens = [token for token in normalized.split() if token]
        if not norm_tokens:
            return False
        if norm_tokens[0] in self._COMMON_VIETNAMESE_SURNAMES:
            return True

        title_like = 0
        for token in tokens:
            lead = token[0]
            if lead.isalpha() and lead.isupper():
                title_like += 1
        return title_like >= max(1, len(tokens) - 1)

    def _canonicalize(self, value: str) -> str:
        normalized = value.strip()
        mapped = self.CANONICAL_VALUES.get(normalized.lower())
        return mapped if mapped else normalized

    def _normalize_space(self, text: Optional[str]) -> str:
        if not text:
            return ""
        return re.sub(r"\s+", " ", text).strip()

    def _slugify(self, value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
        return slug[:48] if slug else "unknown"

    def _build_upstream_url(self, upstream_config: UpstreamRequestConfig, path: str) -> str:
        base = (upstream_config.base_url or "").rstrip("/")
        normalized_path = (path or "").strip()
        if not normalized_path.startswith("/"):
            normalized_path = f"/{normalized_path}"

        parsed = urlparse(base)
        base_segments = [segment for segment in (parsed.path or "").split("/") if segment]
        path_segments = [segment for segment in normalized_path.split("/") if segment]
        if (
            base_segments
            and path_segments
            and base_segments[-1].lower() == path_segments[0].lower()
            and re.fullmatch(r"v\d+[a-z0-9._-]*", path_segments[0].lower())
        ):
            path_segments = path_segments[1:]
            normalized_path = "/" + "/".join(path_segments) if path_segments else ""

        return f"{base}{normalized_path}"

    def _build_upstream_headers(self, upstream_config: UpstreamRequestConfig) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        provider = (upstream_config.api_provider or "openai").strip().lower()
        if provider == "anthropic":
            headers["x-api-key"] = upstream_config.api_key
            headers["anthropic-version"] = "2023-06-01"
        elif provider == "gemini":
            headers["x-goog-api-key"] = upstream_config.api_key
        else:
            headers["Authorization"] = f"Bearer {upstream_config.api_key}"
        return headers

    def _extract_response_text(
        self,
        upstream_config: UpstreamRequestConfig,
        payload: dict,
    ) -> str:
        provider = (upstream_config.api_provider or "openai").strip().lower()
        if provider == "anthropic":
            content = payload.get("content")
            if not isinstance(content, list):
                return ""
            texts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != "text":
                    continue
                value = str(item.get("text") or "").strip()
                if value:
                    texts.append(value)
            return "\n".join(texts).strip()

        if provider == "gemini":
            candidates = payload.get("candidates")
            if not isinstance(candidates, list) or not candidates:
                return ""
            first = candidates[0] if isinstance(candidates[0], dict) else {}
            content = first.get("content") if isinstance(first, dict) else {}
            parts = content.get("parts") if isinstance(content, dict) else None
            if not isinstance(parts, list):
                return ""
            texts: list[str] = []
            for item in parts:
                if not isinstance(item, dict):
                    continue
                value = str(item.get("text") or "").strip()
                if value:
                    texts.append(value)
            return "\n".join(texts).strip()

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first, dict) else {}
        return str(message.get("content") or "").strip() if isinstance(message, dict) else ""

    def _build_ai_extraction_request(
        self,
        *,
        upstream_config: UpstreamRequestConfig,
        prompt: str,
        model_hint: str,
    ) -> tuple[str, dict, dict[str, str]]:
        provider = (upstream_config.api_provider or "openai").strip().lower()
        system_prompt = "Output strictly valid JSON."
        headers = self._build_upstream_headers(upstream_config)

        if provider == "anthropic":
            return (
                self._build_upstream_url(upstream_config, "/v1/messages"),
                {
                    "model": model_hint,
                    "messages": [
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": prompt}],
                        }
                    ],
                    "system": system_prompt,
                    "temperature": 0.1,
                    "max_tokens": 900,
                    "stream": False,
                },
                headers,
            )

        if provider == "gemini":
            model_id = (model_hint or "").strip() or "gemini-2.5-flash"
            return (
                self._build_upstream_url(upstream_config, f"/v1beta/models/{model_id}:generateContent"),
                {
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "generationConfig": {
                        "temperature": 0.1,
                        "maxOutputTokens": 900,
                    },
                },
                headers,
            )

        return (
            self._build_upstream_url(upstream_config, "/v1/chat/completions"),
            {
                "model": model_hint,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
                "stream": False,
                "max_tokens": 900,
            },
            headers,
        )

    async def extract_with_ai(
        self,
        messages: List[str],
        existing_facts: List[LocalFact],
        workspace_id: Optional[str] = None,
        source_id: Optional[str] = None,
        max_facts: int = 8,
        upstream_config: Optional[UpstreamRequestConfig] = None,
        model_hint: Optional[str] = None,
    ) -> List[LocalFact]:
        """
        Use AI to extract complex facts that patterns miss.

        This is called periodically or on-demand, not for every message.

        Args:
            messages: Recent conversation messages
            existing_facts: Already known facts (to avoid duplicates)

        Returns:
            New facts extracted by AI
        """
        if not messages:
            return []
        if upstream_config is None:
            return []

        api_key = (upstream_config.api_key or "").strip()
        base_url = (upstream_config.base_url or "").strip().rstrip("/")
        selected_model = (model_hint or "").strip()
        if not api_key or not base_url or not selected_model:
            return []

        conversation = "\n".join(messages[-12:])
        existing_str = "\n".join([
            f"- {f.subject} | {f.predicate} | {f.object} | {f.category}"
            for f in existing_facts[:30]
        ]) or "- NONE"

        scope_hint = (
            f"workspace_id='{workspace_id}' (use for project/workspace facts)"
            if workspace_id else
            "workspace_id=null (system facts only)"
        )

        prompt = f"""You are an information extraction engine for a personal AI assistant.
Extract ONLY stable, long-term facts about the user. Do NOT infer.

Conversation:
{conversation}

Already known facts (avoid duplicates):
{existing_str}

Return ONLY JSON array (no markdown), each object:
{{
  "subject": "User|Project",
  "predicate": "short_snake_case (e.g. name, occupation, has_skill, prefers, uses_tech)",
  "object": "fact value (concise, stable info)",
  "category": "personal|preference|work|skill|coding_style|communication|relationship|project_context|tech_stack|architecture|convention|domain|team|rule|reference|note|general",
  "confidence": 0.60-0.95,
  "scope": "system|workspace"
}}

Strict Rules:
- Max {max_facts} objects.
- Keep object concise (< 90 chars).
- If fact is project/workspace-specific, use scope=\"workspace\"; otherwise scope=\"system\".
- {scope_hint}
- If nothing new, return [].
- NEVER extract: search queries, news topics, prices, weather, current events, temporary questions.
- NEVER extract: what the user asked about (e.g. "asked about gold price" is NOT a fact).
- ONLY extract: personal info (name, age, location), skills, preferences, work info, project details.
- predicate must be a meaningful English snake_case verb/noun (e.g. name, has_skill, prefers, uses, works_at).
- object must be a stable, enduring piece of information, NOT a date-specific event or query topic.
"""

        upstream_url, payload, headers = self._build_ai_extraction_request(
            upstream_config=upstream_config,
            prompt=prompt,
            model_hint=selected_model,
        )

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    upstream_url,
                    json=payload,
                    headers=headers,
                )
            if response.status_code != 200:
                return []

            data = response.json()
            content = self._extract_response_text(upstream_config, data)
            raw_items = self._parse_ai_json_array(content)
            if not raw_items:
                return []

            now = datetime.now()
            facts: List[LocalFact] = []
            for item in raw_items[:max_facts]:
                fact = self._ai_item_to_fact(
                    item=item,
                    now=now,
                    source_id=source_id,
                    default_workspace_id=workspace_id,
                )
                if fact:
                    facts.append(fact)

            return self._deduplicate_facts(facts)
        except Exception:
            # Non-fatal: silently fallback to pattern extraction.
            return []

    def _parse_ai_json_array(self, content: str) -> List[dict]:
        """Parse JSON array output from model response robustly."""
        text = (content or "").strip()
        if not text:
            return []

        # Fast path: plain JSON array
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
        except json.JSONDecodeError:
            pass

        # Fallback: extract first JSON array block from markdown/noisy output
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            parsed = json.loads(text[start:end + 1])
            if isinstance(parsed, list):
                return [item for item in parsed if isinstance(item, dict)]
        except json.JSONDecodeError:
            return []
        return []

    def _ai_item_to_fact(
        self,
        item: dict,
        now: datetime,
        source_id: Optional[str],
        default_workspace_id: Optional[str],
    ) -> Optional[LocalFact]:
        """Normalize one AI-extracted item into LocalFact."""
        subject = str(item.get("subject", "User")).strip() or "User"
        raw_predicate = str(item.get("predicate", "")).strip()
        predicate = self._slugify(raw_predicate)
        obj = str(item.get("object", "")).strip()
        category = str(item.get("category", "general")).strip().lower()
        scope = str(item.get("scope", "system")).strip().lower()

        if not predicate or not obj:
            return None

        # Reject predicates that are too short or nonsensical after slugify
        if len(predicate) < 3:
            return None

        # Reject objects that look like search queries or ephemeral content
        if self._is_ephemeral_object(obj):
            return None

        if category not in self.AI_ALLOWED_CATEGORIES:
            category = "general"

        normalized_obj = self._normalize_fact_value(
            value=obj,
            predicate=predicate,
            category=category
        )
        if not normalized_obj:
            return None

        if not self._is_stable_fact_candidate(predicate, category, normalized_obj):
            return None

        confidence_raw = item.get("confidence", 0.78)
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError):
            confidence = 0.78
        confidence = max(0.6, min(0.95, confidence))

        resolved_workspace_id = None
        if scope == "workspace":
            resolved_workspace_id = default_workspace_id
            if not resolved_workspace_id:
                return None

        return LocalFact(
            id=str(uuid.uuid4()),
            source_type="ai_extraction",
            source_id=source_id,
            workspace_id=resolved_workspace_id,
            subject=subject,
            predicate=predicate,
            object=normalized_obj,
            category=category,
            confidence=confidence,
            created_at=now,
            updated_at=now,
        )

    def _is_ephemeral_object(self, obj: str) -> bool:
        """Check if the object is ephemeral/transient content (query topic, news, etc.)."""
        lowered = (obj or "").strip().lower()
        if not lowered or len(lowered) < 2:
            return True

        # Reject objects that are date/time-specific queries
        ephemeral_patterns = [
            r"\b(?:hôm nay|hom nay|today|yesterday|hôm qua|this morning|sáng nay)\b",
            r"\b(?:ngày|ngay|tháng|thang|năm|nam)\s*\d",
            r"\b(?:giá|gia|price|cost|rate|tỷ giá|ti gia|thời tiết|thoi tiet|weather)\b",
            r"\b(?:tin tức|tin tuc|news|breaking|trending|hot)\b",
            r"\b(?:kết quả|ket qua|result|score|xổ số|xo so|lottery)\b",
            r"\b(?:bao nhiêu|bao nhieu|how much|how many|what is the)\b",
            r"\b(?:mấy giờ|may gio|what time|when is)\b",
        ]
        for pattern in ephemeral_patterns:
            if re.search(pattern, lowered, re.IGNORECASE):
                return True

        return False

    def _is_stable_fact_candidate(self, predicate: str, category: str, obj: str) -> bool:
        lowered_predicate = (predicate or "").strip().lower()
        lowered_category = (category or "").strip().lower()
        lowered_obj = (obj or "").strip().lower()

        if not lowered_predicate or not lowered_obj:
            return False

        # Reject predicates that are too short or nonsensical
        if len(lowered_predicate) < 3:
            return False

        # Reject ephemeral sentiment/state tracking from AI extraction.
        volatile_keywords = (
            "emotion", "sentiment", "mood", "feeling", "frustration",
            "insult", "compliment", "greeting", "farewell", "urgent",
            "current_", "recent_", "latest_", "last_message",
            "asked_about", "inquired", "searched", "queried",
            "wants_to_know", "curious_about", "interested_in_knowing",
        )
        if any(keyword in lowered_predicate for keyword in volatile_keywords):
            return False

        # Reject predicates that indicate transient actions, not stable facts
        transient_action_predicates = (
            "asked", "requested", "mentioned", "said", "told",
            "discussed", "talked_about", "brought_up", "chatted_about",
        )
        if lowered_predicate in transient_action_predicates:
            return False

        # Boolean-style facts are usually unstable diagnostics, not user memory.
        if lowered_obj in {"true", "false", "yes", "no"}:
            if lowered_predicate.startswith(("is_", "was_", "did_", "has_")):
                return False
            if lowered_category in {"general", "communication", "relationship"}:
                return False

        # Reject ephemeral objects (prices, weather, news, date-specific)
        if self._is_ephemeral_object(lowered_obj):
            return False

        return True

    def merge_facts(
        self,
        new_fact: LocalFact,
        existing_facts: List[LocalFact]
    ) -> Optional[LocalFact]:
        """
        Merge new fact with existing if they're about the same thing.
        Updates confidence if new source is stronger.

        Returns:
            Updated fact if merged, None if new
        """
        for existing in existing_facts:
            same_scope = (existing.workspace_id or "") == (new_fact.workspace_id or "")
            if (
                same_scope
                and existing.subject == new_fact.subject
                and existing.predicate == new_fact.predicate
                and existing.object.lower() == new_fact.object.lower()
            ):
                if new_fact.confidence > existing.confidence:
                    existing.confidence = new_fact.confidence
                    existing.updated_at = datetime.now()
                    return existing
                return existing

        return None  # New fact


# Convenience function
def get_fact_extractor(user_id: str) -> FactExtractor:
    """Get a fact extractor instance for a user."""
    return FactExtractor(user_id)
