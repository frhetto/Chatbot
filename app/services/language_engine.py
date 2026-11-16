"""Subsystem 5.1 & 6.1: Azerbaijani Language Detection, Dialect Normalization & Escalation Classifier.

This module provides the `LanguageDetectionEngine` control class as defined in
project_documentation.md and system_analysis_and_design.md.
It normalizes Azerbaijani Latin character variations and Baku colloquialisms,
classifies customer language into supported locales (AZ, RU, TR, EN), and
evaluates customer intent for human handoff escalation triggers.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Union

from app.models.enums import LanguageCode


# Pre-compiled regex patterns for script & morphological analysis
_CYRILLIC_PATTERN = re.compile(r"[\u0400-\u04FF]")
_AZ_NATIVE_CHARS = set("əƏ")
_AZ_TR_SHARED_CHARS = set("ıİğĞşŞçÇöÖüÜ")

# Distinctive lexical dictionaries for language disambiguation
_AZ_VOCABULARY: Set[str] = {
    "salam", "necəsiz", "necesiz", "necəsiniz", "necesiniz", "çox", "cox",
    "sağ", "sag", "olun", "ol", "təşəkkür", "tesekkur", "edirəm", "edirem",
    "zəhmət", "zehmet", "olmasa", "bəli", "beli", "xeyr", "hə", "he", "yox",
    "sabahınız", "sabahiniz", "xeyir", "hər", "her", "vaxtınız", "vaxtiniz",
    "qiymət", "qiymet", "qiymətlər", "qiymetler", "qiyməti", "qiymeti",
    "məlumat", "melumat", "verin", "iş", "is", "saatlarınız", "saatlariniz",
    "neçəyə", "neceye", "neçədir", "necedir", "qədərdir", "qederdir",
    "menyunuzu", "göndərin", "gonderin", "çatdırılma", "catdirilma", "xidmətiniz",
    "xidmetiniz", "var", "yoxdur", "hansı", "hansi", "olar", "olmaz",
    "haradadır", "haradadir", "hardadır", "hardadir", "yerləşir", "yerlesir",
    "rezervasiya", "etmək", "etmek", "istəyirəm", "isteyirem", "istəyirik",
    "isteyirik", "kömək", "komek", "edin", "danışmaq", "danismaq",
    "bağlayın", "baglayin", "bağlan", "baglan", "şikayət", "sikayet",
    "şikayətim", "sikayetim", "rəhbərlik", "rehberlik", "əlaqə", "elaqe",
    "saxlayın", "saxlayin", "işləmir", "islemir", "menecer", "menecerlə",
    "menecerle", "operator", "operatora", "insan", "insanla", "canlı",
    "canli", "dəstək", "destek", "haqqında", "haqqinda", "filialınız",
    "filialiniz", "kabab", "kababın", "kababin", "sualım", "sualim",
    "ünvanınız", "unvaniniz", "şəhər", "seher", "baxın", "baxin",
    "buyurun", "zəng", "zeng", "sifariş", "sifaris", "bunun", "nədir",
    "nedir", "nə", "ne", "bizim", "sizin", "mən", "men", "sizə", "size",
    "meettabb", "meettab", "aktivlik", "aktivliklər", "aktivlikler",
    "kartinq", "karting", "bovlinq", "bowling", "velosiped", "velo",
    "peyntbol", "paintball", "kvest", "kvestlər", "kvestler", "şahmat", "wahmat",
    "futbol", "tədbir", "tedbir", "tədbirlər", "tedbirler", "yoldaş",
    "yoldas", "dostlar", "dostlarımla", "dostlarimnan", "monopoly",
    "mafia", "böyükşor", "boyuksor", "bulvar", "velopark", "oyun", "oyunlar",
}

_TR_VOCABULARY: Set[str] = {
    "merhaba", "selam", "nasılsınız", "nasilsiniz", "teşekkürler", "tesekkurler",
    "lütfen", "lutfen", "evet", "hayır", "hayir", "günaydın", "gunaydin",
    "iyi", "günler", "gunler", "akşamlar", "aksamlar", "fiyat", "fiyatlar",
    "hakkında", "hakkinda", "bilgi", "alabilir", "miyim", "çalışma", "calisma",
    "saatleri", "saatleriniz", "menüyü", "menuyu", "görebilir", "gorebilir",
    "gönderir", "gonderir", "misiniz", "musunuz", "müsünüz", "mısınız",
    "müşteri", "musteri", "temsilcisi", "yetkili", "yardım", "yardim",
    "istiyorum", "edebilir", "sipariş", "siparis", "durumu", "nerede",
    "neredesiniz", "kaçta", "kacta", "kapanıyor", "kapaniyor", "açılıyor",
    "aciliyor", "rezervasyon", "yapmak", "yemek", "restoran", "teslimat",
    "bakar", "bakar mısınız", "rica", "ederim", "hoşça", "kalın", "görüşmek",
    "üzere", "nelerdir", "öğrenebilir", "adresiniz", "şubeniz", "nedir",
}

_EN_VOCABULARY: Set[str] = {
    "the", "be", "to", "of", "and", "a", "in", "that", "have", "i",
    "it", "for", "not", "on", "with", "he", "as", "you", "do", "at",
    "this", "but", "his", "by", "from", "they", "we", "say", "her",
    "she", "or", "an", "will", "my", "one", "all", "would", "there",
    "their", "what", "so", "up", "out", "if", "about", "who", "get",
    "which", "go", "me", "when", "make", "can", "like", "time", "no",
    "just", "him", "know", "take", "people", "into", "year", "your",
    "good", "some", "could", "them", "see", "other", "than", "then",
    "now", "look", "only", "come", "its", "over", "think", "also",
    "back", "after", "use", "two", "how", "our", "work", "first",
    "well", "way", "even", "new", "want", "because", "any", "these",
    "give", "day", "most", "us", "manager", "operator", "agent",
    "human", "complaint", "support", "representative", "hello", "hi",
    "please", "help", "menu", "price", "prices", "hours", "order",
    "status", "delivery", "food", "speak", "talk", "service",
    "contact", "call", "cancel", "refund", "location", "located", "address",
    "send", "information", "info", "working", "open", "close", "closed",
    "tell", "person", "assist", "assistance", "billing", "where", "restaurant",
    "someone", "right", "now",
}

# Colloquial Azerbaijani informal word replacements
_COLLOQUIAL_WORD_MAP: Dict[str, str] = {
    "danismaq": "danışmaq",
    "danismag": "danışmaq",
    "danisiram": "danışıram",
    "danismak": "danışmaq",
    "danişmaq": "danışmaq",
    "danısmaq": "danışmaq",
    "isteyirem": "istəyirəm",
    "istəyirem": "istəyirəm",
    "isteyirəm": "istəyirəm",
    "isteyirik": "istəyirik",
    "isteyir": "istəyir",
    "baglayin": "bağlayın",
    "bağlayin": "bağlayın",
    "baglayın": "bağlayın",
    "bagla": "bağla",
    "baglan": "bağlan",
    "baglanmaq": "bağlanmaq",
    "bagli": "bağlı",
    "sikayet": "şikayət",
    "sikayət": "şikayət",
    "sikayetim": "şikayətim",
    "şikayetim": "şikayətim",
    "sikayetci": "şikayətçi",
    "komek": "kömək",
    "komey": "kömək",
    "rehberlik": "rəhbərlik",
    "rehber": "rəhbər",
    "elaqe": "əlaqə",
    "islemir": "işləmir",
    "işlemir": "işləmir",
    "menecerle": "menecerlə",
    "canli": "canlı",
    "destek": "dəstək",
    "desteye": "dəstəyə",
    "destege": "dəstəyə",
    "qiymet": "qiymət",
    "qiymetler": "qiymətlər",
    "qiymeti": "qiyməti",
    "melumat": "məlumat",
    "saatlariniz": "saatlarınız",
    "neceye": "neçəyə",
    "necedir": "neçədir",
    "qederdir": "qədərdir",
    "menyunuzu": "menyunuzu",
    "gonderin": "göndərin",
    "gonder": "göndər",
    "catdirilma": "çatdırılma",
    "cox": "çox",
    "sagol": "sağ ol",
    "sagolun": "sağ olun",
    "tesekkur": "təşəkkür",
    "zehmet": "zəhmət",
    "necesiz": "necəsiz",
    "necesiniz": "necəsiniz",
    "yaxsi": "yaxşı",
    "yaxshi": "yaxşı",
    "yaxşi": "yaxşı",
    "haqqinda": "haqqında",
    "haradadir": "haradadır",
    "hardadir": "hardadır",
    "musteri": "müştəri",
    "temsilcisi": "təmsilçisi",
    "filialiniz": "filialınız",
    "yoxdu": "yoxdur",
    "hardadi": "haradadır",
    "necedi": "neçədir",
    "neciyedi": "neçəyədir",
    "tedbir": "tədbir",
    "tedbirler": "tədbirlər",
    "yaxwi": "yaxşı",
    "bawla": "başla",
    "wexsi": "şəxsi",
    "dostlarimnan": "dostlarımla",
    "dostlarnan": "dostlarla",
    "dostlarimla": "dostlarımla",
    "karting": "kartinq",
    "bowling": "bovlinq",
    "surmey": "sürmək",
    "surmek": "sürmək",
    "neyniyim": "nə edim",
    "neyleyim": "nə edim",
    "wahmat": "şahmat",
    "wahmata": "şahmata",
    "velo": "velosiped",
    "aktivlik": "aktivlik",
    "aktivlikler": "aktivliklər",
    "meettabb": "MeeTTabb",
    "meettab": "MeeTTabb",
    "tovsiyye": "tövsiyə",
    "tovsiye": "tövsiyə",
    "meslehet": "məsləhət",
    "peyntbol": "peyntbol",
    "paintball": "peyntbol",
    "kvest": "kvest",
    "kvestler": "kvestlər",
    "maraqli": "maraqlı",
    "tetbiq": "tətbiq",
    "yukle": "yüklə",
    "yuklemek": "yükləmək",
    "yarad": "yarat",
    "devet": "dəvət",
    "pulsuz": "pulsuz",
    "odenissiz": "ödənişsiz",
}


class LanguageDetectionEngine:
    """Subsystem 5.1 & 6.1 Control Class for localized language processing.

    Provides Azerbaijani Latin character normalization, multi-lingual language
    classification (AZ, RU, TR, EN), and customer escalation intent detection.
    """

    def __init__(
        self,
        supported_languages: Optional[List[LanguageCode]] = None,
        escalation_keywords: Optional[Dict[LanguageCode, List[str]]] = None,
        char_normalization_map: Optional[Dict[str, str]] = None,
    ) -> None:
        self._supported_languages: List[LanguageCode] = (
            supported_languages
            if supported_languages is not None
            else [
                LanguageCode.AZ,
                LanguageCode.RU,
                LanguageCode.TR,
                LanguageCode.EN,
            ]
        )

        self._escalation_keywords: Dict[LanguageCode, List[str]] = (
            escalation_keywords
            if escalation_keywords is not None
            else {
                LanguageCode.AZ: [
                    "menecer",
                    "menecerlə",
                    "operator",
                    "şikayət",
                    "canlı dəstək",
                    "insan",
                    "rəhbərlik",
                    "administrator",
                    "bağlan",
                    "əlaqə saxlayın",
                    "işləmir",
                    "kömək edin",
                ],
                LanguageCode.RU: [
                    "менеджер",
                    "оператор",
                    "жалоба",
                    "человек",
                    "администратор",
                    "позвонить",
                ],
                LanguageCode.TR: [
                    "yetkili",
                    "müşteri temsilcisi",
                    "şikayet",
                    "canlı destek",
                    "insan",
                ],
                LanguageCode.EN: [
                    "manager",
                    "operator",
                    "agent",
                    "human",
                    "complaint",
                    "support",
                    "representative",
                ],
            }
        )

        self._char_normalization_map: Dict[str, str] = (
            char_normalization_map
            if char_normalization_map is not None
            else {
                "@": "ə",
                "sh": "ş",
                "ch": "ç",
                "gh": "ğ",
                "Sh": "ş",
                "SH": "ş",
                "Ch": "ç",
                "CH": "ç",
                "Gh": "ğ",
                "GH": "ğ",
                "w": "ş",
                "W": "ş",
            }
        )

    # -------------------------------------------------------------------------
    # Properties & UML 2.5 Attributes
    # -------------------------------------------------------------------------

    @property
    def supported_languages(self) -> List[LanguageCode]:
        """List of supported languages."""
        return list(self._supported_languages)

    @property
    def supportedLanguages(self) -> List[LanguageCode]:
        """UML 2.5 CamelCase alias for supported_languages."""
        return self.supported_languages

    @property
    def escalation_keywords(self) -> Dict[LanguageCode, List[str]]:
        """Dictionary of escalation keywords partitioned by LanguageCode."""
        return self._escalation_keywords

    @property
    def char_normalization_map(self) -> Dict[str, str]:
        """Mapping dictionary of ASCII / informal digital substitutes."""
        return self._char_normalization_map

    # -------------------------------------------------------------------------
    # Normalization
    # -------------------------------------------------------------------------

    def normalize_azerbaijani_chars(self, text: str) -> str:
        """Lowercases and normalizes transliterated or informal representations into canonical Azerbaijani Latin script.

        Standardizes common ASCII and informal digital substitutes:
        - '@' -> 'ə'
        - 'sh' / 'ch' / 'gh' -> 'ş' / 'ç' / 'ğ'
        - Colloquial Baku chat spellings (e.g. 'danismaq' -> 'danışmaq', 'sikayetim' -> 'şikayətim')

        Args:
            text: Raw input string.

        Returns:
            Normalized canonical Azerbaijani text string.
        """
        if not text:
            return ""

        # Preserve Turkish/Azerbaijani dotless I distinctions before lowercasing
        normalized = text.replace("I", "ı").replace("İ", "i").lower()

        # Word-level normalization for common colloquialisms
        words = re.findall(r"\w+|[^\w\s]|\s+", normalized, re.UNICODE)
        normalized_tokens: List[str] = []

        for token in words:
            token_lower = token.lower()
            if token_lower in _COLLOQUIAL_WORD_MAP:
                normalized_tokens.append(_COLLOQUIAL_WORD_MAP[token_lower])
            else:
                tok = token
                for src, target in self._char_normalization_map.items():
                    tok = tok.replace(src.lower(), target)
                if tok.lower() in _COLLOQUIAL_WORD_MAP:
                    tok = _COLLOQUIAL_WORD_MAP[tok.lower()]
                normalized_tokens.append(tok)

        return "".join(normalized_tokens)

    def normalizeAzerbaijaniChars(self, text: str) -> str:
        """UML 2.5 CamelCase alias for normalize_azerbaijani_chars."""
        return self.normalize_azerbaijani_chars(text)

    def normalize_azerbaijani(self, text: str) -> str:
        """Interface contract alias for normalize_azerbaijani_chars."""
        return self.normalize_azerbaijani_chars(text)

    # -------------------------------------------------------------------------
    # Language Detection
    # -------------------------------------------------------------------------

    def detect_language(self, text: str) -> LanguageCode:
        """Classifies the customer message as AZ, RU, TR, or EN.

        Inspection heuristics:
        1. Cyrillic script -> LanguageCode.RU
        2. Azerbaijani unique character 'ə' / 'Ə' -> LanguageCode.AZ
        3. Lexical frequency and morphological markers (AZ vs TR vs EN)
        4. Fallback to LanguageCode.AZ (primary local commerce language)

        Args:
            text: Raw or normalized customer message.

        Returns:
            Detected LanguageCode.
        """
        if not text or not text.strip():
            return LanguageCode.AZ

        clean = text.strip()

        # 1. Cyrillic script check -> RU
        cyrillic_chars = len(_CYRILLIC_PATTERN.findall(clean))
        if cyrillic_chars > 0:
            total_letters = len(re.findall(r"[A-Za-z\u0400-\u04FFəƏıİğĞşŞçÇöÖüÜ]", clean))
            if total_letters == 0 or (cyrillic_chars / max(1, total_letters)) > 0.25 or cyrillic_chars >= 2:
                return LanguageCode.RU

        # 2. Azerbaijani unique character check: 'ə' / 'Ə' -> strictly Azerbaijani
        if any(c in _AZ_NATIVE_CHARS for c in clean):
            return LanguageCode.AZ

        # Tokenize words for vocabulary matching
        tokens = [t.lower() for t in re.findall(r"\b\w+\b", clean, re.UNICODE)]
        if not tokens:
            return LanguageCode.AZ

        az_score = 0
        tr_score = 0
        en_score = 0

        # Check token membership
        for token in tokens:
            if token in _AZ_VOCABULARY:
                az_score += 2
            if token in _TR_VOCABULARY:
                tr_score += 2
            if token in _EN_VOCABULARY:
                en_score += 2

        # Shared Turkic character presence ('ı', 'ğ', 'ş', 'ç', 'ö', 'ü')
        turkic_char_count = sum(1 for c in clean if c in _AZ_TR_SHARED_CHARS)
        if turkic_char_count > 0:
            az_score += 1
            tr_score += 1

        # Turkish-specific verbal and interrogative patterns
        clean_lower = clean.lower()
        if re.search(r"\bçalışma\s+saatler[a-zçğıöşü]*\b", clean_lower):
            tr_score += 5
        if re.search(r"\b(alabilir\s+miyim|edebilir\s+misiniz|görebilir\s+miyim|misiniz|mısınız|musunuz|müsünüz)\b", clean_lower):
            tr_score += 4
        if re.search(r"[a-zçğıöşü]+(i|ı|u|ü)yorum\b", clean_lower):
            tr_score += 3
        if re.search(r"\b(müşteri\s+temsilcisi|canlı\s+destek)\b", clean_lower):
            tr_score += 3

        # Azerbaijani-specific patterns
        if re.search(r"\b(haqqında|haqqinda|neçəyə|neceye|qədərdir|qederdir|çatdırılma|catdirilma)\b", clean_lower):
            az_score += 3
        if re.search(r"[a-zəıöüşç]+(ıram|irəm|uram|ürəm|ırıq|irik)\b", clean_lower):
            az_score += 3

        # Decision tree
        # If pure Latin without Turkic diacritics and English vocabulary dominates:
        if turkic_char_count == 0:
            if en_score > az_score and en_score > tr_score:
                return LanguageCode.EN

        # Turkish vs Azerbaijani discrimination
        if tr_score > az_score and tr_score >= 3:
            return LanguageCode.TR

        if az_score > 0:
            return LanguageCode.AZ

        # If English has matches without Turkic elements
        if en_score > 0 and turkic_char_count == 0:
            return LanguageCode.EN

        # Default fallback for local commerce
        return LanguageCode.AZ

    def detectLanguage(self, text: str) -> LanguageCode:
        """UML 2.5 CamelCase alias for detect_language."""
        return self.detect_language(text)

    # -------------------------------------------------------------------------
    # Escalation Intent Classifier
    # -------------------------------------------------------------------------

    def is_escalation_intent(
        self,
        text: str,
        lang: Optional[Union[LanguageCode, str]] = None,
        history: Optional[List[Any]] = None,
    ) -> bool:
        """Evaluates whether the customer inquiry requires human agent handoff.

        Normalizes input text and evaluates against escalation keyword
        dictionaries, conversational history context, and sentiment patterns
        for the detected or specified language.

        Args:
            text: Customer message string.
            lang: Optional LanguageCode or string (e.g. 'AZ'). If None,
                  auto-detected.
            history: Optional list of recent ChatMessage entities to inspect
                     prior context (e.g. customer agreeing with "Olar" to an offer).

        Returns:
            True if escalation is indicated, False otherwise.
        """
        if not text or not text.strip():
            return False

        clean = text.strip()
        clean_lower = clean.lower()
        norm_text = self.normalize_azerbaijani_chars(text)
        norm_lower = norm_text.lower()

        # Contextual Affirmation Check:
        # If the bot previously offered manager escalation (e.g. 'sizi menecerimizlə əlaqələndirə bilərəm')
        # and the customer responds with an affirmation ('Olar', 'Bəli', 'Hə', 'Yaxşı', etc.)
        if history:
            last_assistant_msg = None
            for prev_msg in reversed(history):
                direction = getattr(prev_msg, "direction", None)
                dir_val = direction.value if hasattr(direction, "value") else str(direction or "")
                if dir_val.upper() in ("OUTBOUND", "OUT"):
                    last_assistant_msg = getattr(prev_msg, "messageBody", None) or getattr(prev_msg, "message_body", "")
                    break

            if last_assistant_msg:
                last_lower = last_assistant_msg.lower()
                offered_escalation = any(
                    k in last_lower
                    for k in (
                        "menecer",
                        "operator",
                        "əlaqələndir",
                        "elaqelendir",
                        "canlı dəstək",
                        "canli destek",
                        "manager",
                        "human agent",
                    )
                )
                if offered_escalation:
                    # Check if customer response is a concise affirmative confirmation,
                    # and NOT a substantive informational inquiry (e.g. "qiymətləri öyrənmək istəyirəm").
                    tokens = [t for t in re.findall(r"\w+", clean_lower) if t]
                    inquiry_words = {
                        "qiymət", "qiymet", "qiymətlər", "qiymetler", "öyrən", "oyren", "öyrənmək", "oyrenmek",
                        "məlumat", "melumat", "nədir", "nedir", "necə", "nece", "harada", "harda",
                        "kartinq", "bovlinq", "futbol", "tətbiq", "tetbiq", "app", "xəritə", "xerite"
                    }
                    is_inquiry = bool(set(tokens).intersection(inquiry_words))
                    affirmations = {
                        "olar", "bəli", "beli", "hə", "he", "yaxşı", "yaxsi", "ok", "okay",
                        "aha", "əlbəttə", "elbetde", "mümkündürsə", "mumkundurse", "zəhmət",
                        "zehmet", "buyurun", "bağlayın", "baglayin",
                        "əlaqələndirin", "elaqelendirin", "keçirdin", "kecirdin", "sure", "yes",
                        "please", "of course",
                    }
                    if not is_inquiry and len(tokens) <= 4:
                        if tokens and (tokens[0] in affirmations or set(tokens).intersection(affirmations)):
                            return True

        # Resolve language
        target_lang: LanguageCode
        if lang is not None:
            if isinstance(lang, str):
                try:
                    target_lang = LanguageCode(lang.upper())
                except ValueError:
                    target_lang = self.detect_language(text)
            elif isinstance(lang, LanguageCode):
                target_lang = lang
            else:
                target_lang = self.detect_language(text)
        else:
            target_lang = self.detect_language(text)

        # Multi-language Negation Check (AZ, RU, TR, EN):
        # If customer explicitly expresses refusal or negation ("menecer istəmirəm", "lazım deyil",
        # "don't want", "не надо", "istemiyorum"), escalation is strictly False.
        if self._is_negated_escalation(norm_lower, clean_lower):
            return False

        # 1. Primary evaluation in target language
        if self._matches_escalation(norm_lower, clean_lower, target_lang):
            return True

        # 2. Cross-lingual / code-switching fallback (e.g. English keywords in AZ chat)
        for other_lang in self._supported_languages:
            if other_lang != target_lang:
                if self._matches_escalation(norm_lower, clean_lower, other_lang):
                    return True

        return False

    def isEscalationIntent(
        self,
        text: str,
        lang: Optional[Union[LanguageCode, str]] = None,
        history: Optional[List[Any]] = None,
    ) -> bool:
        """UML 2.5 CamelCase alias for is_escalation_intent."""
        return self.is_escalation_intent(text, lang, history)

    def detect_escalation_intent(
        self,
        text: str,
        lang: Optional[Union[LanguageCode, str]] = None,
        history: Optional[List[Any]] = None,
    ) -> bool:
        """Interface contract alias for is_escalation_intent."""
        return self.is_escalation_intent(text, lang, history)

    # -------------------------------------------------------------------------
    # Internal Escalation Keyword Matching Logic & Negation Filtering
    # -------------------------------------------------------------------------

    def _is_negated_escalation(self, norm_text: str, raw_text: str) -> bool:
        """Evaluates whether the query contains negative or refusal intent regarding human escalation."""
        combined = f"{norm_text} {raw_text}".lower()

        # 1. Azerbaijani Negations
        az_negation_patterns = [
            r"\b(?:istəmirəm|istemirem|istəmirik|istemirik)\b",
            r"\b(?:lazım\s+deyil|lazim\s+deyil|lazım\s+yoxdur|lazim\s+yoxdur|lazım\s+deyiləm|lazim\s+deyilem)\b",
            r"\b(?:ehtiyac\s+yoxdur|ehtiyac\s+yoxdu|ehtiyac\s+yox)\b",
            r"\b(?:gərək\s+deyil|gerek\s+deyil)\b",
            r"\b(?:bağlama|baglama|bağlamayın|baglamayin)\b",
            r"\b(?:qoşma|qosma|qoşmayın|qosmayin)\b",
            r"\b(?:yönləndirmə|yonlendirme|yönləndirməyin|yonlendirmeyin)\b",
            r"\b(?:keçirmə|kecirme|keçirməyin|kecirmeyin)\b",
            r"\b(?:əlaqələndirmə|elaqelendirme|əlaqələndirməyin|elaqelendirmeyin)\b",
            r"\b(?:danışmaq\s+istəmirəm|danismaq\s+istemirem)\b",
            r"\b(?:menecer|operator|insan|canlı|canli)\b.*\b(?:istəmirəm|istemirem|lazım\s+deyil|lazim\s+deyil|yoxdur|yoxdu|yox)\b",
            r"\b(?:istəmirəm|istemirem|lazım\s+deyil|lazim\s+deyil|yoxdur|yoxdu|yox)\b.*\b(?:menecer|operator|insan|canlı|canli)\b",
        ]
        for pat in az_negation_patterns:
            if re.search(pat, combined):
                return True

        # 2. English Negations
        en_negation_patterns = [
            r"\b(?:don'?t\s+want|do\s+not\s+want)\b",
            r"\b(?:no\s+manager|no\s+operator|no\s+human|no\s+agent)\b",
            r"\b(?:don'?t\s+need|do\s+not\s+need|no\s+need)\b",
            r"\b(?:don'?t\s+connect|don'?t\s+transfer|don'?t\s+forward)\b",
            r"\b(?:not\s+a\s+human|not\s+an\s+operator|not\s+a\s+manager)\b",
            r"\bwithout\s+(?:a\s+)?(?:manager|operator|human|agent)\b",
        ]
        for pat in en_negation_patterns:
            if re.search(pat, combined):
                return True

        # 3. Russian Negations
        ru_negation_patterns = [
            r"\b(?:не\s+надо|не\s+нужно|не\s+нужен|не\s+нужна|не\s+требуется)\b",
            r"\b(?:не\s+хочу|не\s+соединяйте|не\s+переключайте|не\s+зовите)\b",
            r"\b(?:без\s+менеджера|без\s+оператора|без\s+человека)\b",
            r"\b(?:нет\s+не\s+надо|не\s+надо\s+человека|не\s+надо\s+оператора|не\s+надо\s+менеджера)\b",
        ]
        for pat in ru_negation_patterns:
            if re.search(pat, combined):
                return True

        # 4. Turkish Negations
        tr_negation_patterns = [
            r"\b(?:istemiyorum|istemem|istemiyoruz)\b",
            r"\b(?:gerek\s+yok|lüzum\s+yok|ihtiyaç\s+yok|ihtiyac\s+yok)\b",
            r"\b(?:bağlamayın|baglamayin|aktarmayın|aktarmayin|yönlendirmeyin|yonlendirmeyin)\b",
            r"\b(?:yetkili\s+istemiyorum|temsilci\s+istemiyorum|temsilciye\s+gerek\s+yok|insan\s+istemiyorum)\b",
        ]
        for pat in tr_negation_patterns:
            if re.search(pat, combined):
                return True

        return False

    def _matches_escalation(
        self,
        norm_text: str,
        raw_text: str,
        language: LanguageCode,
    ) -> bool:
        """Evaluates if the text triggers escalation keywords for a specific language."""
        if language == LanguageCode.AZ:
            return self._matches_az_escalation(norm_text, raw_text)
        elif language == LanguageCode.RU:
            return self._matches_ru_escalation(norm_text, raw_text)
        elif language == LanguageCode.TR:
            return self._matches_tr_escalation(norm_text, raw_text)
        elif language == LanguageCode.EN:
            return self._matches_en_escalation(norm_text, raw_text)
        return False

    def _matches_az_escalation(self, norm_text: str, raw_text: str) -> bool:
        """Evaluates Azerbaijani escalation triggers (including Baku colloquialisms)."""
        combined = f"{norm_text} {raw_text}"

        # Multi-word phrases
        multi_word_phrases = [
            "canlı dəstək",
            "canli destek",
            "əlaqə saxlayın",
            "elaqe saxlayin",
            "əlaqə saxla",
            "elaqe saxla",
            "əlaqə qurun",
            "elaqe qurun",
            "əlaqəyə keçirdin",
            "elaqeye kecirdin",
            "əlaqələndirin",
            "elaqelendirin",
            "kömək edin",
            "komek edin",
            "kömək lazımdır",
            "komek lazimdir",
            "köməy edin",
            "zəng vursun",
            "zeng vursun",
            "zəng edin",
            "zeng edin",
            "mənə zəng",
            "mene zeng",
            "şikayətim var",
            "sikayetim var",
            "şikayət etmək",
            "sikayet etmek",
        ]
        for phrase in multi_word_phrases:
            if phrase in combined:
                return True

        # Stem / keyword root checks with word boundaries
        # Prevents matching 'iş' inside 'iş saatlarınız'
        az_stem_patterns = [
            # canli destege / canli desteye
            r"\b(?:canlı|canli)\s+(?:dəstək|destek|dəstəyə|desteye|destege)[a-zəıöüşç]*\b",
            # menecer / menecerlə / menecerle / menecerə / etc.
            r"\bmenecer[a-zəıöüşç]*\b",
            # operator / operatora / operatorla / etc.
            r"\boperator[a-zəıöüşç]*\b",
            # şikayət / sikayet / şikayətim / sikayetim / etc.
            r"\b(?:şikayət|sikayet)[a-zəıöüşç]*\b",
            # rəhbərlik / rehberlik / rəhbər / rehber
            r"\b(?:rəhbərlik|rehberlik|rəhbər|rehber)[a-zəıöüşç]*\b",
            # administrator / admin / adminlə
            r"\b(?:administrator|admin)[a-zəıöüşç]*\b",
            # bağlan / baglan / bağlayın / baglayin / bağla / bagla / qoşun / qosun
            r"\b(?:bağlan|baglan|bağlayın|baglayin|bağla|bagla|qoşun|qosun)[a-zəıöüşç]*\b",
            # insan / insanla / insana (requesting real human)
            r"\binsan[a-zəıöüşç]*\b",
            # işləmir / islemir (system broken / not working complaint)
            r"\b(?:işləmir|islemir)\b",
            # menecer / operator ilə əlaqə / əlaqəyə keçirdin
            r"\b(?:menecer|operator)[a-zəıöüşç]*\s+(?:ilə|ile|lə|le)\s+(?:əlaqə|elaqe)[a-zəıöüşç]*\b",
            r"\b(?:əlaqəyə|elaqeye)\s+(?:keçirdin|kecirdin|bağlayın|baglayin|qoşun|qosun)\b",
            r"\b(?:məni|meni|bizi)\s+(?:menecer|operator)[a-zəıöüşç]*\b",
            r"\b(?:əlaqələndirin|elaqelendirin|keçirdin|kecirdin)\b",
        ]

        for pat in az_stem_patterns:
            if re.search(pat, combined):
                return True

        return False

    def _matches_ru_escalation(self, norm_text: str, raw_text: str) -> bool:
        """Evaluates Russian escalation triggers."""
        multi_word_phrases = [
            "живой человек",
            "позовите человека",
            "позовите оператора",
            "соедините с оператором",
            "свяжите с оператором",
            "хочу поговорить",
            "поговорить с оператором",
            "поговорить с менеджером",
            "оставить жалобу",
        ]
        combined = f"{norm_text} {raw_text}"
        for phrase in multi_word_phrases:
            if phrase in combined:
                return True

        ru_stem_patterns = [
            r"\bменеджер[а-я]*\b",
            r"\bоператор[а-я]*\b",
            r"\bжалоб[а-я]*\b",
            r"\bчеловек[а-я]*\b",
            r"\bадминистратор[а-я]*\b",
            r"\bпозвон[а-я]*\b",
        ]
        for pat in ru_stem_patterns:
            if re.search(pat, combined):
                return True

        return False

    def _matches_tr_escalation(self, norm_text: str, raw_text: str) -> bool:
        """Evaluates Turkish escalation triggers."""
        multi_word_phrases = [
            "müşteri temsilcisi",
            "musteri temsilcisi",
            "canlı destek",
            "canli destek",
            "yetkili biri",
            "insanla görüşmek",
            "insanla konusmak",
        ]
        combined = f"{norm_text} {raw_text}"
        for phrase in multi_word_phrases:
            if phrase in combined:
                return True

        tr_stem_patterns = [
            r"\byetkili[a-zçğıöşü]*\b",
            r"\b(?:şikayet|sikayet)[a-zçğıöşü]*\b",
            r"\binsan[a-zçğıöşü]*\b",
        ]
        for pat in tr_stem_patterns:
            if re.search(pat, combined):
                return True

        return False

    def _matches_en_escalation(self, norm_text: str, raw_text: str) -> bool:
        """Evaluates English escalation triggers."""
        combined = f"{norm_text} {raw_text}"

        multi_word_phrases = [
            "speak with a human",
            "speak to a human",
            "talk to a human",
            "talk to someone",
            "speak to someone",
            "talk to a person",
            "speak to a person",
            "real person",
            "human agent",
            "human manager",
            "customer support",
            "customer service",
            "transfer me",
            "connect me",
        ]
        for phrase in multi_word_phrases:
            if phrase in combined:
                return True

        en_stem_patterns = [
            r"\bmanager[s]?\b",
            r"\boperator[s]?\b",
            r"\bagent[s]?\b",
            r"\bhuman[s]?\b",
            r"\bcomplaint[s]?\b",
            r"\bsupport\b",
            r"\brepresentative[s]?\b",
            r"\b(?:talk|speak)\s+to\s+(?:someone|anybody|somebody|a\s+person)\b",
        ]
        for pat in en_stem_patterns:
            if re.search(pat, combined):
                return True

        return False

    def is_app_feedback(self, text: str) -> bool:
        """Determines if the customer message contains a product recommendation or app feedback."""
        if not text or not text.strip():
            return False
        clean = self.normalize_azerbaijani_chars(text).lower()
        feedback_triggers = [
            "tövsiyə", "tovsiye", "tövsiyəm", "tovsiyem",
            "təklif", "teklif", "təklifim", "teklifim", "təklif edirəm", "teklif edirem",
            "tətbiqə əlavə", "tetbiqe elave", "əlavə etsəniz", "elave etseniz",
            "əlavə etsəz", "elave etsez", "əlavə edin", "elave edin",
            "qoysanız", "qoysaz", "qoyun", "əlavə olunsun", "elave olunsun",
            "olsa əla olar", "olsa ela olar", "olsa yaxşı olar", "olsa yaxsi olar",
            "maraqlı olardı", "maraqli olardi", "yaxşı olardı", "yaxsi olardi",
            "məncə tətbiq", "mence tetbiq", "fikrimcə", "fikrimce", "fikrim var",
            "app recommendation", "feature request", "suggest", "suggestion",
            "tövsiyə edirəm", "tovsiye edirem", "bir təklif", "bir teklif",
            "xəritə əlavə", "xerite elave", "marşrutlar üçün", "marsrutlar ucun",
        ]
        return any(trig in clean for trig in feedback_triggers)


# -----------------------------------------------------------------------------
# Singleton Instance & Factory Function
# -----------------------------------------------------------------------------

_language_engine_instance: Optional[LanguageDetectionEngine] = None


def get_language_engine() -> LanguageDetectionEngine:
    """Returns the singleton instance of the LanguageDetectionEngine."""
    global _language_engine_instance
    if _language_engine_instance is None:
        _language_engine_instance = LanguageDetectionEngine()
    return _language_engine_instance
