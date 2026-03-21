"""
User Profile Store - Stream 1: Strict identity-only memory.

Stores ONLY personal identity fields (name, age, gender, etc.)
with a strict whitelist. No context/project data allowed here.

Design principles:
- Single-value per key: one user_name, one user_age, etc.
- Whitelist-only: keys not in PROFILE_VALID_KEYS are rejected
- Explicit extraction: only first-person statements trigger storage
- Confidence-gated updates: high-confidence existing values require confirmation
"""

import re
import uuid
import unicodedata
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from ..local_storage import LocalDatabase
from ..local_storage.local_models import (
    UserProfileField,
    PROFILE_VALID_KEYS,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Extraction patterns (bilingual: EN + VI)
# ─────────────────────────────────────────────────────────────

@dataclass
class ProfilePattern:
    key: str           # Must be in PROFILE_VALID_KEYS
    pattern: str       # Regex
    confidence: float  # Extraction confidence
    group: int = 1     # Capture group index


# Strict patterns — only match when user EXPLICITLY states about themselves
PROFILE_PATTERNS: List[ProfilePattern] = [
    # ── Name ──
    ProfilePattern("user_name", r"\b(?:from now on,\s*)?(?:please\s+)?(?:change|update)\s+my\s+name\s+to\s+([A-Za-z0-9][A-Za-z0-9_-]{2,63})\b", 0.99),
    ProfilePattern("user_name", r"\b(?:from now on,\s*)?(?:please\s+)?(?:change|update)\s+my\s+name\s+to\s+([A-Za-zÀ-Ỹ][A-Za-zÀ-ỹ' -]{1,60})", 0.97),
    ProfilePattern("user_name", r"\b(?:từ giờ|tu gio)[, ]+(?:hãy\s+)?(?:đổi|doi|cập nhật|cap nhat)\s+tên\s+(?:tôi|em|mình)\s+thành\s+([A-Za-z0-9][A-Za-z0-9_-]{2,63})\b", 0.99),
    ProfilePattern("user_name", r"\b(?:từ giờ|tu gio)[, ]+(?:hãy\s+)?(?:đổi|doi|cập nhật|cap nhat)\s+tên\s+(?:tôi|em|mình)\s+thành\s+([A-Za-zÀ-Ỹ][A-Za-zÀ-ỹ' -]{1,60})", 0.97),
    ProfilePattern("user_name", r"\b(?:my legal name is|my full name is)\s+([A-Za-z0-9][A-Za-z0-9_-]{2,63})\b", 0.98),
    ProfilePattern("user_name", r"\b(?:my legal name is|my full name is)\s+([A-Za-zÀ-Ỹ][A-Za-zÀ-ỹ' -]{1,60})", 0.96),
    ProfilePattern("user_name", r"\b(?:my name is|i'm called|i am)\s+([A-Za-z0-9][A-Za-z0-9_-]{2,63})\b", 0.96),
    ProfilePattern("user_name", r"\b(?:tên tôi là|tôi tên|tên em là|em tên|tên mình là|mình tên|tôi là|tên tôi)\s+([A-Za-z0-9][A-Za-z0-9_-]{2,63})\b", 0.96),
    ProfilePattern("user_name", r"\b(?:ten toi la|toi ten|ten em la|em ten|ten minh la|minh ten|toi la)\s+([A-Za-z0-9][A-Za-z0-9_-]{2,63})\b", 0.94),
    ProfilePattern("user_name", r"\b(?:my name is|i'm called|call me|i am)\s+([A-ZÀ-Ỹ][a-zà-ỹ]+(?:\s+[A-ZÀ-Ỹ][a-zà-ỹ]+){0,4})", 0.95),
    ProfilePattern("user_name", r"\b(?:tên tôi là|tôi tên|tên em là|em tên|tên mình là|mình tên|tôi là|tên tôi|bạn có thể gọi tôi là)\s+([A-ZÀ-Ỹ][a-zà-ỹ]+(?:\s+[A-ZÀ-Ỹ][a-zà-ỹ]+){0,4})", 0.95),
    # Normalized (no diacritics)
    ProfilePattern("user_name", r"\b(?:ten toi la|toi ten|ten em la|em ten|ten minh la|minh ten|toi la|ban co the goi toi la)\s+([A-Za-z]+(?:\s+[A-Za-z]+){0,4})", 0.90),

    # ── Nickname ──
    ProfilePattern("user_nickname", r"\b(?:you can call me|call me|my nickname is)\s+([A-Za-z0-9][A-Za-z0-9_-]{2,63})\b", 0.93),
    ProfilePattern("user_nickname", r"\b(?:gọi tôi là|gọi em là|gọi mình là|biệt danh của tôi là)\s+([A-Za-z0-9][A-Za-z0-9_-]{2,63})\b", 0.93),
    ProfilePattern("user_nickname", r"\b(?:you can call me|call me|my nickname is)\s+([A-Za-zÀ-ỹ]{2,20})", 0.90),
    ProfilePattern("user_nickname", r"\b(?:gọi tôi là|gọi em là|gọi mình là|biệt danh của tôi là)\s+([A-Za-zÀ-ỹ]{2,20})", 0.90),

    # ── Age ──
    ProfilePattern("user_age", r"\b(?:i am|i'm)\s+(\d{1,3})\s+(?:years old|yr|yrs)", 0.92),
    ProfilePattern("user_age", r"\b(?:tôi|em|mình)\s+(\d{1,3})\s+(?:tuổi)", 0.92),
    ProfilePattern("user_age", r"\b(?:năm nay tôi|năm nay em|năm nay mình)\s+(\d{1,3})\s*(?:tuổi)?", 0.90),
    # Normalized
    ProfilePattern("user_age", r"\b(?:nam nay toi|nam nay em|nam nay minh)\s+(\d{1,3})\s*(?:tuoi)?", 0.88),

    # ── Gender ──
    ProfilePattern("gender", r"\b(?:i am|i'm)\s+(male|female|non-binary)\b", 0.92),
    ProfilePattern("gender", r"\b(?:tôi là|em là|mình là)\s+(nam|nữ|con trai|con gái)\b", 0.92),
    ProfilePattern("gender", r"\b(?:giới tính(?:\s+(?:của\s+)?(?:tôi|em|mình))?\s+là)\s+(nam|nữ)\b", 0.90),
    # Normalized
    ProfilePattern("gender", r"\b(?:toi la|em la|minh la)\s+(nam|nu|con trai|con gai)\b", 0.88),

    # ── Location ──
    ProfilePattern("location", r"\b(?:i live in|i am from|i'm from|i am based in)\s+([A-Za-zÀ-ỹ0-9 ,.'-]{2,60})", 0.88),
    ProfilePattern("location", r"\b(?:tôi sống (?:ở|tại)|tôi ở|em ở|mình ở|tôi đến từ|em đến từ)\s+([A-Za-zÀ-ỹ0-9 ,.'-]{2,60})", 0.88),
    # Normalized
    ProfilePattern("location", r"\b(?:toi song o|toi o|em o|minh o|toi den tu|em den tu)\s+([A-Za-z0-9 ,.'-]{2,60})", 0.85),

    # ── Occupation ──
    ProfilePattern("occupation", r"\b(?:i work as(?: an?)?|my job is|my role is|i am(?: an?)?)\s+((?:software |web |mobile |full.?stack |front.?end |back.?end )?(?:developer|engineer|designer|manager|student|teacher|doctor|nurse|lawyer|accountant|analyst|consultant|freelancer|writer|artist|architect|scientist|researcher|professor|intern|sinh viên|giáo viên|bác sĩ|kỹ sư|lập trình viên|nhà thiết kế))\b", 0.90),
    ProfilePattern("occupation", r"\b(?:tôi làm|em làm|mình làm|tôi là|nghề của tôi là|tôi đang làm)\s+((?:kỹ sư|lập trình viên|nhà thiết kế|giáo viên|bác sĩ|sinh viên|developer|engineer|designer|student|freelancer)[A-Za-zÀ-ỹ ]*)", 0.88),
    # Normalized
    ProfilePattern("occupation", r"\b(?:toi lam|em lam|minh lam|nghe cua toi la)\s+([a-z ]{3,50})", 0.85),

    # ── Company ──
    ProfilePattern("company", r"\b(?:i work at|i work for|my company is|i am at)\s+([A-Za-zÀ-ỹ0-9 .&'-]{2,50})", 0.88),
    ProfilePattern("company", r"\b(?:tôi làm (?:ở|tại)|tôi đang làm ở|công ty của tôi là|em làm ở)\s+([A-Za-zÀ-ỹ0-9 .&'-]{2,50})", 0.88),

    # ── Education ──
    ProfilePattern("education", r"\b(?:i studied at|i graduated from|i go to|i attend)\s+([A-Za-zÀ-ỹ0-9 ,.'-]{3,80})", 0.85),
    ProfilePattern("education", r"\b(?:tôi học (?:ở|tại)|em học (?:ở|tại)|tôi tốt nghiệp (?:từ|ở))\s+([A-Za-zÀ-ỹ0-9 ,.'-]{3,80})", 0.85),

    # ── Nationality ──
    ProfilePattern("nationality", r"\b(?:i am|i'm)\s+(vietnamese|american|japanese|korean|chinese|british|french|german|australian|canadian|indian|thai|filipino|indonesian|malaysian|singaporean)\b", 0.88),
    ProfilePattern("nationality", r"\b(?:tôi là người|em là người|mình là người)\s+([A-Za-zÀ-ỹ ]{2,30})", 0.88),

    # ── Birthday ──
    ProfilePattern("birthday", r"\b(?:my birthday is|i was born on)\s+(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)", 0.90),
    ProfilePattern("birthday", r"\b(?:ngày sinh của tôi là|tôi sinh ngày|em sinh ngày)\s+(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)", 0.90),

    # ── Native language ──
    ProfilePattern("native_language", r"\b(?:my native language is|my mother tongue is|i speak)\s+([a-zA-Z]{3,20})\b", 0.88),
    ProfilePattern("native_language", r"\b(?:ngôn ngữ mẹ đẻ của tôi là|tiếng mẹ đẻ của tôi là)\s+([A-Za-zÀ-ỹ ]{3,20})", 0.88),

    # ── Response style ──
    ProfilePattern("response_style", r"\b(?:respond|reply|answer)\s+(?:in\s+)?(?:a\s+)?(concise|brief|detailed|step.by.step|short)\b", 0.90),
    ProfilePattern("response_style", r"\b(?:trả lời|phản hồi)\s+(?:theo\s+)?(?:kiểu\s+)?(ngắn gọn|chi tiết|từng bước)\b", 0.90),

    # ── Response language ──
    ProfilePattern("response_language", r"\b(?:respond|reply|answer|speak)\s+(?:to me\s+)?in\s+(english|vietnamese|tiếng việt|tiếng anh)\b", 0.92),
    ProfilePattern("response_language", r"\b(?:trả lời|nói|phản hồi)\s+(?:bằng\s+)?(tiếng việt|tiếng anh|english|vietnamese)\b", 0.92),

    # ── Tone preference ──
    ProfilePattern("tone_preference", r"\b(?:use|speak|respond)\s+(?:in\s+)?(?:a\s+)?(formal|casual|friendly|professional)\s+(?:tone|manner|style)\b", 0.88),
]


# Keys that need NAME collision guard (don't save occupation if it matches name)
NAME_COLLISION_KEYS = {"occupation", "company"}


class UserProfileStore:
    """
    Stream 1: Strict user identity storage.

    - Only keys in PROFILE_VALID_KEYS are accepted
    - Single-value per key (upsert semantics)
    - High-confidence existing values are protected from overwrite
    """

    OVERWRITE_CONFIDENCE_THRESHOLD = 0.90  # Don't overwrite above this without explicit signal

    def __init__(self, local_db: LocalDatabase, user_id: str):
        self.local = local_db
        self.user_id = local_db.user_id
        self._ensure_table()

    def _ensure_table(self):
        """Create user_profile table if not exists."""
        with self.local._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS user_profile (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    confidence REAL DEFAULT 0.8,
                    source TEXT DEFAULT 'pattern',
                    confirmed INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_profile_user_key
                    ON user_profile(user_id, key);
                CREATE INDEX IF NOT EXISTS idx_profile_user
                    ON user_profile(user_id);
            """)

    # ─────────────────────────────────────────────────────────
    # CRUD
    # ─────────────────────────────────────────────────────────

    def get_all(self) -> List[UserProfileField]:
        """Get all profile fields for the user."""
        with self.local._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM user_profile WHERE user_id = ? ORDER BY key",
                (self.user_id,)
            ).fetchall()
            return [self._row_to_field(row) for row in rows]

    def get(self, key: str) -> Optional[UserProfileField]:
        """Get a single profile field by key."""
        if key not in PROFILE_VALID_KEYS:
            return None
        with self.local._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM user_profile WHERE user_id = ? AND key = ?",
                (self.user_id, key)
            ).fetchone()
            return self._row_to_field(row) if row else None

    def set(
        self,
        key: str,
        value: str,
        confidence: float = 0.8,
        source: str = "pattern",
        force: bool = False,
    ) -> Optional[UserProfileField]:
        """
        Set a profile field. Returns the saved field, or None if rejected.

        Rejection reasons:
        - Key not in PROFILE_VALID_KEYS
        - Value collision with existing name (for occupation/company)
        - Existing value has higher confidence and not forced
        """
        if key not in PROFILE_VALID_KEYS:
            logger.debug("Profile key rejected (not in whitelist): %s", key)
            return None

        value = value.strip()
        if not value:
            return None

        # Name collision guard
        if key in NAME_COLLISION_KEYS:
            name_field = self.get("user_name")
            nickname_field = self.get("user_nickname")
            name_values = set()
            if name_field:
                name_values.add(self._normalize(name_field.value))
            if nickname_field:
                name_values.add(self._normalize(nickname_field.value))
            if name_values and self._normalize(value) in name_values:
                logger.debug("Profile value rejected (name collision): key=%s value=%s", key, value)
                return None

        existing = self.get(key)

        if existing and not force:
            # Don't overwrite high-confidence confirmed values with lower confidence
            if existing.confirmed and confidence < 0.95:
                logger.debug(
                    "Profile update skipped (confirmed): key=%s old=%s new=%s",
                    key, existing.value, value
                )
                return existing
            # Don't overwrite with lower confidence
            if existing.confidence >= self.OVERWRITE_CONFIDENCE_THRESHOLD and confidence < existing.confidence:
                logger.debug(
                    "Profile update skipped (higher existing confidence): key=%s",
                    key
                )
                return existing
            # Same value, just bump confidence
            if self._normalize(existing.value) == self._normalize(value):
                if confidence > existing.confidence:
                    existing.confidence = confidence
                    existing.updated_at = datetime.now()
                    self._save(existing)
                return existing

        now = datetime.now()
        field = UserProfileField(
            id=existing.id if existing else str(uuid.uuid4()),
            user_id=self.user_id,
            key=key,
            value=value,
            confidence=confidence,
            source=source,
            confirmed=existing.confirmed if existing else False,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        self._save(field)
        logger.info("Profile field saved: key=%s value=%s confidence=%.2f", key, value, confidence)
        return field

    def delete(self, key: str) -> bool:
        """Delete a profile field."""
        with self.local._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM user_profile WHERE user_id = ? AND key = ?",
                (self.user_id, key)
            )
            return cursor.rowcount > 0

    def delete_by_id(self, field_id: str) -> bool:
        """Delete a profile field by ID."""
        with self.local._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM user_profile WHERE id = ? AND user_id = ?",
                (field_id, self.user_id)
            )
            return cursor.rowcount > 0

    def confirm(self, key: str) -> Optional[UserProfileField]:
        """Mark a profile field as confirmed by user."""
        field = self.get(key)
        if not field:
            return None
        field.confirmed = True
        field.confidence = max(field.confidence, 0.95)
        field.updated_at = datetime.now()
        self._save(field)
        return field

    def clear_all(self):
        """Delete all profile fields for this user."""
        with self.local._get_connection() as conn:
            conn.execute(
                "DELETE FROM user_profile WHERE user_id = ?",
                (self.user_id,)
            )

    # ─────────────────────────────────────────────────────────
    # Extraction
    # ─────────────────────────────────────────────────────────

    def extract_from_message(self, message: str) -> List[UserProfileField]:
        """
        Extract profile fields from a user message using strict patterns.
        Returns list of newly saved/updated fields.
        """
        if not message or len(message.strip()) < 8:
            return []

        text = message.strip()
        saved: List[UserProfileField] = []

        for pattern in PROFILE_PATTERNS:
            match = re.search(pattern.pattern, text, re.IGNORECASE)
            if not match:
                continue
            if self._is_truncated_capture(text, match, pattern.group):
                continue

            raw_value = match.group(pattern.group).strip()
            value = self._clean_value(raw_value, pattern.key)
            if not value:
                continue

            result = self.set(
                key=pattern.key,
                value=value,
                confidence=pattern.confidence,
                source="pattern",
            )
            if result:
                saved.append(result)

        return saved

    # ─────────────────────────────────────────────────────────
    # Context injection
    # ─────────────────────────────────────────────────────────

    def format_for_context(self) -> str:
        """
        Format all profile fields for injection into system prompt.
        Always injected — AI should always know who it's talking to.
        """
        fields = self.get_all()
        if not fields:
            return ""

        lines = [f"- {field.to_display()}" for field in fields]
        return "### User Identity\n" + "\n".join(lines)

    # ─────────────────────────────────────────────────────────
    # Migration from legacy
    # ─────────────────────────────────────────────────────────

    def migrate_from_legacy(self):
        """
        One-time migration from legacy facts/preferences/assertions
        into the new user_profile table.
        """
        KEY_MAPPING = {
            # Legacy fact predicates → profile keys
            "name": "user_name",
            "user_name": "user_name",
            "user_nickname": "user_nickname",
            "age": "user_age",
            "user_age": "user_age",
            "gender": "gender",
            "location": "location",
            "occupation": "occupation",
            "company": "company",
            "education": "education",
            "nationality": "nationality",
            "birth_date": "birthday",
            "birthday": "birthday",
            "native_language": "native_language",
            "marital_status": "marital_status",
            "response_style": "response_style",
            "response_language": "response_language",
            "tone_preference": "tone_preference",
        }

        existing_fields = {f.key for f in self.get_all()}
        if existing_fields:
            logger.info("Profile already has data, skipping migration")
            return

        migrated = 0

        # Migrate from memory_assertions (identity type)
        try:
            with self.local._get_connection() as conn:
                rows = conn.execute("""
                    SELECT key, value, confidence, type
                    FROM memory_assertions
                    WHERE user_id = ?
                      AND status = 'active'
                      AND type IN ('identity', 'preference')
                      AND (expires_at IS NULL OR expires_at > ?)
                    ORDER BY confidence DESC
                """, (self.user_id, datetime.now().isoformat())).fetchall()

                for row in rows:
                    legacy_key = str(row["key"]).strip().lower()
                    profile_key = KEY_MAPPING.get(legacy_key)
                    if not profile_key:
                        continue
                    value = str(row["value"]).strip()
                    if not value:
                        continue
                    result = self.set(
                        key=profile_key,
                        value=value,
                        confidence=float(row["confidence"] or 0.8),
                        source="migration",
                    )
                    if result:
                        migrated += 1
        except Exception as e:
            logger.warning("Migration from assertions failed: %s", e)

        # Migrate from legacy facts
        try:
            with self.local._get_connection() as conn:
                rows = conn.execute("""
                    SELECT predicate, object, confidence
                    FROM facts
                    WHERE workspace_id IS NULL
                    ORDER BY confidence DESC
                """).fetchall()

                for row in rows:
                    legacy_key = str(row["predicate"]).strip().lower()
                    profile_key = KEY_MAPPING.get(legacy_key)
                    if not profile_key:
                        continue
                    value = str(row["object"]).strip()
                    if not value:
                        continue
                    result = self.set(
                        key=profile_key,
                        value=value,
                        confidence=float(row["confidence"] or 0.7),
                        source="migration",
                    )
                    if result:
                        migrated += 1
        except Exception as e:
            logger.warning("Migration from facts failed: %s", e)

        if migrated:
            logger.info("Profile migration completed: %d fields migrated", migrated)

    # ─────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────

    def _save(self, field: UserProfileField):
        """Upsert a profile field into DB."""
        with self.local._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO user_profile
                    (id, user_id, key, value, confidence, source, confirmed, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                field.id,
                field.user_id,
                field.key,
                field.value,
                field.confidence,
                field.source,
                1 if field.confirmed else 0,
                field.created_at.isoformat() if isinstance(field.created_at, datetime) else field.created_at,
                field.updated_at.isoformat() if isinstance(field.updated_at, datetime) else field.updated_at,
            ))

    def _row_to_field(self, row) -> UserProfileField:
        """Convert DB row to UserProfileField."""
        return UserProfileField(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            key=str(row["key"]),
            value=str(row["value"]),
            confidence=float(row["confidence"] or 0.8),
            source=str(row["source"] or "pattern"),
            confirmed=bool(row["confirmed"]),
            created_at=self._parse_datetime(row["created_at"]),
            updated_at=self._parse_datetime(row["updated_at"]),
        )

    def _parse_datetime(self, value) -> datetime:
        if isinstance(value, datetime):
            return value
        if not value:
            return datetime.now()
        try:
            return datetime.fromisoformat(str(value))
        except (ValueError, TypeError):
            return datetime.now()

    def _normalize(self, text: str) -> str:
        """Normalize text for comparison (lowercase, remove diacritics)."""
        lowered = (text or "").strip().lower()
        if not lowered:
            return ""
        normalized = unicodedata.normalize("NFD", lowered)
        normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
        normalized = normalized.replace("đ", "d")
        return re.sub(r"\s+", " ", normalized).strip()

    def _clean_value(self, value: str, key: str) -> str:
        """Clean an extracted value based on key type."""
        value = value.strip()
        if not value:
            return ""

        # Remove trailing punctuation
        value = re.sub(r"[.,;:!?]+$", "", value).strip()

        if key in ("user_name", "user_nickname"):
            if self._is_identifier_like_name(value):
                return value[:64]
            # Title case for names, max 5 words
            parts = value.split()[:5]
            return " ".join(p.capitalize() for p in parts if p)

        if key == "user_age":
            # Must be numeric and reasonable
            try:
                age = int(value)
                if 1 <= age <= 150:
                    return str(age)
            except ValueError:
                pass
            return ""

        if key == "gender":
            value_lower = self._normalize(value)
            gender_map = {
                "male": "male", "nam": "male", "con trai": "male",
                "female": "female", "nu": "female", "con gai": "female",
                "non-binary": "non-binary",
            }
            return gender_map.get(value_lower, value)

        if key == "response_style":
            style_map = {
                "concise": "concise", "brief": "concise", "short": "concise",
                "ngan gon": "concise",
                "detailed": "detailed", "chi tiet": "detailed",
                "step by step": "step_by_step", "step-by-step": "step_by_step",
                "tung buoc": "step_by_step",
            }
            return style_map.get(self._normalize(value), value)

        if key == "response_language":
            lang_map = {
                "english": "english", "tieng anh": "english",
                "vietnamese": "vietnamese", "tieng viet": "vietnamese",
            }
            return lang_map.get(self._normalize(value), value)

        return value

    def _is_identifier_like_name(self, value: str) -> bool:
        compact = value.strip()
        if not compact or " " in compact:
            return False
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,63}", compact):
            return False
        return bool(re.search(r"[A-Za-z]", compact))

    def _is_truncated_capture(self, text: str, match: re.Match[str], group: int) -> bool:
        """Reject regex captures that stop mid-token, e.g. NAME-M before 03-NEW."""
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
