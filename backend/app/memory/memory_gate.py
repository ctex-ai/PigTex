"""
Memory Gate - Classifies user messages into extraction streams.

This is the single entry point that decides:
1. PROFILE → Extract user identity info (name, age, etc.)
2. CONTEXT → Extract project/workspace context
3. SKIP    → Don't extract anything (smalltalk, code, questions)

Design principles:
- Clear separation: profile vs context extraction never overlap
- Conservative: when in doubt, SKIP
- Fast: no API calls, pure heuristic
"""

import re
import unicodedata
import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class MemoryStream(Enum):
    """Which extraction stream to route the message to."""
    PROFILE = "profile"     # → UserProfileStore
    CONTEXT = "context"     # → ContextMemoryStore
    BOTH = "both"           # → Both stores
    SKIP = "skip"           # → No extraction


class MemoryGate:
    """
    Classifies a user message to determine which memory extraction
    pipeline(s) should process it.
    """

    # Minimum message length to consider for extraction
    MIN_MESSAGE_LENGTH = 8

    # ─── Profile triggers ───
    # First-person identity statements (EN + VI)
    PROFILE_TRIGGERS = [
        # English
        r"\b(?:change|update)\s+my\s+name\s+to\b",
        r"\b(?:change|update)\s+my\s+nickname\s+to\b",
        r"\b(?:from now on,\s*)?(?:please\s+)?call me\b",
        r"\bfrom now on\b.*\b(?:answer|reply|respond)\b",
        r"\b(?:answer|reply|respond)\b.*\b(?:bullet points?|emoji|action:)\b",
        r"\b(?:my legal name is|my full name is)\b",
        r"\b(?:my name is|call me|i'm called|you can call me)\b",
        r"\b(?:i am|i'm)\s+\d+\s+(?:years old|yr|yrs)\b",
        r"\b(?:i am|i'm)\s+(?:male|female|non-binary)\b",
        r"\b(?:i live in|i am from|i'm from|i am based in)\b",
        r"\b(?:i work as|my job is|my role is|i am an?)\s+(?:developer|engineer|designer|student|teacher|doctor|freelancer)\b",
        r"\b(?:i work at|i work for|my company is)\b",
        r"\b(?:i studied at|i graduated from)\b",
        r"\b(?:my birthday is|i was born on)\b",
        r"\b(?:my native language is|my mother tongue is)\b",
        r"\b(?:respond|reply|answer)\s+(?:in\s+)?(?:a\s+)?(?:concise|brief|detailed|short)\b",
        r"\b(?:respond|reply|answer|speak)\s+(?:to me\s+)?in\s+(?:english|vietnamese)\b",

        # Vietnamese
        r"(?:tên tôi là|tôi tên|tên em là|em tên|tên mình là|mình tên)",
        r"(?:đổi tên tôi thành|cập nhật tên tôi thành|đổi biệt danh tôi thành)",
        r"(?:tôi|em|mình)\s+\d+\s+tuổi",
        r"(?:năm nay tôi|năm nay em|năm nay mình)\s+\d+",
        r"(?:tôi là|em là|mình là)\s+(?:nam|nữ|con trai|con gái)",
        r"(?:giới tính)\s+(?:của\s+)?(?:tôi|em|mình)\s+là",
        r"(?:tôi sống ở|tôi ở|em ở|mình ở|tôi đến từ|em đến từ)",
        r"(?:tôi làm|em làm|mình làm|nghề của tôi)",
        r"(?:tôi làm ở|tôi đang làm ở|công ty của tôi)",
        r"(?:tôi học ở|em học ở|tôi tốt nghiệp)",
        r"(?:tôi là người)\s+[A-Za-zÀ-ỹ]",
        r"(?:ngày sinh của tôi|tôi sinh ngày|em sinh ngày)",
        r"(?:trả lời|phản hồi)\s+(?:theo\s+)?(?:kiểu\s+)?(?:ngắn gọn|chi tiết|từng bước)",
        r"(?:trả lời|nói|phản hồi)\s+(?:bằng\s+)?(?:tiếng việt|tiếng anh)",

        # Normalized Vietnamese (no diacritics)
        r"(?:ten toi la|toi ten|ten em la|em ten|ten minh la|minh ten)",
        r"(?:doi ten toi thanh|cap nhat ten toi thanh|doi biet danh toi thanh)",
        r"(?:toi la|em la|minh la)\s+(?:nam|nu|con trai|con gai)",
        r"(?:toi song o|toi o|em o|minh o|toi den tu|em den tu)",
        r"(?:toi lam|em lam|minh lam|nghe cua toi)",
        r"(?:nam nay toi|nam nay em|nam nay minh)\s+\d+",
    ]

    # ─── Context triggers ───
    # Workspace/project signals
    CONTEXT_TRIGGERS = [
        r"\b(?:project|codebase|workspace|repository|repo)\b",
        r"\b(?:dự án|trong dự án|quy ước|convention)\b",
        r"\b(?:du an|trong du an|quy uoc)\b",
        r"\b(?:deadline|tech stack|architecture|framework)\b",
        r"\b(?:remember that|ghi nhớ|nhớ rằng|hay nhớ|đừng quên|please remember)\b",
        r"\b(?:project uses|we use|using|dùng|đang dùng)\s+(?:react|vue|angular|next|django|flask|fastapi|express|spring|laravel)\b",
        r"\b(?:written in|coded in|viết bằng)\s+(?:python|javascript|typescript|java|go|rust)\b",
    ]

    # ─── Skip triggers ───
    # Messages that should NEVER trigger extraction
    SKIP_PATTERNS = [
        # Transient queries
        r"\b(?:giá|gia|price|cost|rate|tỷ giá)\b.*\b(?:hôm nay|today|bao nhiêu|how much)\b",
        r"\b(?:thời tiết|thoi tiet|weather|forecast)\b",
        r"\b(?:tin tức|tin tuc|news)\b.*\b(?:hôm nay|today|mới nhất|latest)\b",
        r"\b(?:mấy giờ|may gio|what time|bây giờ)\b",
        # Code blocks
        r"```",
        # Very short messages (greetings, etc.)
    ]

    # Pure smalltalk
    SMALLTALK_PATTERNS = [
        r"^(?:hi|hello|hey|xin chào|chào|ổn không|ok|okay|thanks|cảm ơn|bye|tạm biệt)\s*[!?.]*$",
        r"^(?:what's up|how are you|bạn khỏe không|ơi)\s*[!?.]*$",
    ]

    def classify(
        self,
        message: str,
        workspace_id: Optional[str] = None,
    ) -> MemoryStream:
        """
        Classify a user message into a memory extraction stream.

        Returns:
            MemoryStream.PROFILE  – extract user identity only
            MemoryStream.CONTEXT  – extract project/workspace context only
            MemoryStream.BOTH     – extract both (rare: explicit memory + identity)
            MemoryStream.SKIP     – don't extract anything
        """
        text = (message or "").strip()
        if len(text) < self.MIN_MESSAGE_LENGTH:
            return MemoryStream.SKIP

        lowered = text.lower()
        normalized = self._normalize(text)

        # 1. Check SKIP patterns first
        for pattern in self.SKIP_PATTERNS:
            if re.search(pattern, lowered, re.IGNORECASE):
                return MemoryStream.SKIP

        # 2. Check smalltalk
        for pattern in self.SMALLTALK_PATTERNS:
            if re.search(pattern, lowered, re.IGNORECASE):
                return MemoryStream.SKIP

        # 3. Check if message is mostly code
        if self._is_code_message(text):
            return MemoryStream.SKIP

        # 4. Check both trigger types
        has_profile = any(
            re.search(p, lowered, re.IGNORECASE) or re.search(p, normalized, re.IGNORECASE)
            for p in self.PROFILE_TRIGGERS
        )
        has_context = any(
            re.search(p, lowered, re.IGNORECASE) or re.search(p, normalized, re.IGNORECASE)
            for p in self.CONTEXT_TRIGGERS
        )

        # Context without workspace: allow tech-stack and explicit memory commands
        # (they will be saved as "temporary" scope in ContextMemoryStore)
        if has_context and not workspace_id:
            strong_signal_re = (
                r"\b(?:remember|ghi nhớ|nhớ rằng|hay nhớ|đừng quên|please remember|"
                r"project uses|we use|dùng|đang dùng|dang dung|dung|written in|coded in|viết bằng|viet bang|"
                r"tech stack|framework|deadline)\b"
            )
            has_strong_signal = bool(
                re.search(strong_signal_re, lowered, re.IGNORECASE)
                or re.search(strong_signal_re, normalized, re.IGNORECASE)
            )
            if not has_strong_signal:
                has_context = False

        # 5. Route
        if has_profile and has_context:
            return MemoryStream.BOTH
        if has_profile:
            return MemoryStream.PROFILE
        if has_context:
            return MemoryStream.CONTEXT

        return MemoryStream.SKIP

    def _is_code_message(self, text: str) -> bool:
        """Check if message is primarily code."""
        has_code_block = "```" in text
        has_path = bool(re.search(r"([A-Za-z]:\\|[./]{1,2}/)", text))
        has_symbols = len(re.findall(r"[{}();=<>\[\]]", text)) >= 6
        return has_code_block or (has_path and has_symbols)

    def _normalize(self, text: str) -> str:
        """Remove diacritics for Vietnamese matching."""
        lowered = (text or "").strip().lower()
        if not lowered:
            return ""
        normalized = unicodedata.normalize("NFD", lowered)
        normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
        return normalized.replace("đ", "d")
