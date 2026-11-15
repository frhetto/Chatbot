"""Unit and regression tests for Subsystem 5.1 & 6.1: LanguageDetectionEngine.

Verifies:
1. Normalization of colloquial Azerbaijani text strings (ASCII substitutes, Baku colloquialisms).
2. Accurate classification of Russian text with Cyrillic script as LanguageCode.RU.
3. Accurate classification of Azerbaijani text containing native Latin characters as LanguageCode.AZ.
4. Accurate classification of Turkish and English texts.
5. Accurate detection of English escalation requests as True.
6. Accurate detection of colloquial Azerbaijani informal variants as True.
7. Zero false positives on routine merchant queries (menus, hours, prices, etc.).
8. UML 2.5 CamelCase interface compliance.
"""

from typing import List, Tuple
import pytest

from app.models.enums import LanguageCode
from app.services import LanguageDetectionEngine, get_language_engine


@pytest.fixture
def engine() -> LanguageDetectionEngine:
    """Fixture providing a fresh LanguageDetectionEngine instance."""
    return LanguageDetectionEngine()


# =============================================================================
# 1. Initialization and Interface Verification
# =============================================================================


class TestLanguageEngineInitialization:
    """Verifies default constructor attributes, properties, and singleton factory."""

    def test_default_supported_languages(self, engine: LanguageDetectionEngine) -> None:
        expected = [LanguageCode.AZ, LanguageCode.RU, LanguageCode.TR, LanguageCode.EN]
        assert engine.supported_languages == expected
        assert engine.supportedLanguages == expected
        assert engine._supported_languages == expected

    def test_default_escalation_keywords_structure(self, engine: LanguageDetectionEngine) -> None:
        keywords = engine.escalation_keywords
        assert LanguageCode.AZ in keywords
        assert LanguageCode.RU in keywords
        assert LanguageCode.TR in keywords
        assert LanguageCode.EN in keywords

        # Verify key baseline seeds
        assert "menecer" in keywords[LanguageCode.AZ]
        assert "şikayət" in keywords[LanguageCode.AZ]
        assert "менеджер" in keywords[LanguageCode.RU]
        assert "yetkili" in keywords[LanguageCode.TR]
        assert "manager" in keywords[LanguageCode.EN]

    def test_char_normalization_map(self, engine: LanguageDetectionEngine) -> None:
        c_map = engine.char_normalization_map
        assert c_map["@"] == "ə"
        assert c_map["sh"] == "ş"
        assert c_map["ch"] == "ç"
        assert c_map["gh"] == "ğ"

    def test_singleton_factory(self) -> None:
        inst1 = get_language_engine()
        inst2 = get_language_engine()
        assert inst1 is inst2
        assert isinstance(inst1, LanguageDetectionEngine)

    def test_custom_constructor_injection(self) -> None:
        custom_langs = [LanguageCode.AZ, LanguageCode.EN]
        custom_kw = {LanguageCode.AZ: ["komek"], LanguageCode.EN: ["help"]}
        custom_map = {"@": "ə"}
        custom_engine = LanguageDetectionEngine(
            supported_languages=custom_langs,
            escalation_keywords=custom_kw,
            char_normalization_map=custom_map,
        )
        assert custom_engine.supported_languages == custom_langs
        assert custom_engine.escalation_keywords == custom_kw
        assert custom_engine.char_normalization_map == custom_map


# =============================================================================
# 2. Azerbaijani Character Normalization & Baku Colloquialisms
# =============================================================================


class TestAzerbaijaniCharacterNormalization:
    """Verifies canonical normalization of ASCII, digraphs, and informal Baku chat variants."""

    @pytest.mark.parametrize(
        "raw_input, expected_output",
        [
            ("M@n sizinl@ daniSHmaq ist@yir@m", "mən sizinlə danışmaq istəyirəm"),
            ("chox sagolun, zehmet olmasa komek edin", "çox sağ olun, zəhmət olmasa kömək edin"),
            ("menecerle danismaq isteyirem", "menecerlə danışmaq istəyirəm"),
            ("operatora baglayin", "operatora bağlayın"),
            ("sikayetim var", "şikayətim var"),
            ("islemir", "işləmir"),
            ("qiymetler haqqinda melumat verin", "qiymətlər haqqında məlumat verin"),
            ("saatlariniz neceye qederdir", "saatlarınız neçəyə qədərdir"),
            ("menyunuzu gonderin", "menyunuzu göndərin"),
        ],
    )
    def test_normalization_scenarios(
        self,
        engine: LanguageDetectionEngine,
        raw_input: str,
        expected_output: str,
    ) -> None:
        assert engine.normalize_azerbaijani_chars(raw_input) == expected_output

    def test_normalization_with_turkish_dotless_i(self, engine: LanguageDetectionEngine) -> None:
        # Uppercase I should become ı in Azerbaijani Latin script
        assert engine.normalize_azerbaijani_chars("DANISMAQ") == "danışmaq"
        assert engine.normalize_azerbaijani_chars("İSTƏYİRƏM") == "istəyirəm"

    def test_normalization_empty_and_whitespace(self, engine: LanguageDetectionEngine) -> None:
        assert engine.normalize_azerbaijani_chars("") == ""
        assert engine.normalize_azerbaijani_chars("   ") == "   "

    def test_normalization_preserves_punctuation_and_numbers(self, engine: LanguageDetectionEngine) -> None:
        result = engine.normalize_azerbaijani_chars("Kabab 15 AZN-dir? Chox yaxshi!")
        assert "15 azn-dir?" in result
        assert "çox yaxşı!" in result


# =============================================================================
# 3. Multi-Lingual Classification (AZ, RU, TR, EN)
# =============================================================================


class TestMultiLanguageClassification:
    """Verifies strict acceptance criteria 2 and 3 and general multi-language detection."""

    # AC-2: Russian Cyrillic classification
    @pytest.mark.parametrize(
        "text",
        [
            "Здравствуйте, сколько стоит доставка?",
            "Какое у вас меню?",
            "Мне нужен менеджер",
            "Где вы находитесь?",
            "Покажите пожалуйста цены на десерты",
            "Хочу сделать заказ на вечер",
            "У вас есть свободные столики на двоих?",
        ],
    )
    def test_ac2_russian_classification(self, engine: LanguageDetectionEngine, text: str) -> None:
        assert engine.detect_language(text) == LanguageCode.RU

    # AC-3: Azerbaijani with native Latin characters
    @pytest.mark.parametrize(
        "text",
        [
            "Qiymətlər haqqında məlumat verin",
            "İş saatlarınız neçəyə qədərdir?",
            "Menyunuzu göndərin",
            "Sabah saat 2-yə rezervasiya etmək olar?",
            "Kababın qiyməti neçəyədir?",
            "Çatdırılma xidmətiniz var?",
            "Filialınız harada yerləşir?",
            "Zəhmət olmasa menyunu atın",
            "Şikayətim var, rəhbərliklə danışmaq istəyirəm",
        ],
    )
    def test_ac3_azerbaijani_native_latin_classification(
        self,
        engine: LanguageDetectionEngine,
        text: str,
    ) -> None:
        assert engine.detect_language(text) == LanguageCode.AZ

    # Azerbaijani colloquial ASCII (no native diacritics)
    @pytest.mark.parametrize(
        "text",
        [
            "Salam, qiymetler haqqinda melumat verin",
            "Menecerle danismaq isteyirem",
            "Operatora baglayin",
            "Sikayetim var",
            "Kabab neceye qederdir?",
            "Catdirilma var sizde?",
        ],
    )
    def test_azerbaijani_informal_ascii_classification(
        self,
        engine: LanguageDetectionEngine,
        text: str,
    ) -> None:
        assert engine.detect_language(text) == LanguageCode.AZ

    # Turkish language classification
    @pytest.mark.parametrize(
        "text",
        [
            "Merhaba, fiyatlar hakkında bilgi alabilir miyim?",
            "Müşteri temsilcisiyle görüşmek istiyorum",
            "Çalışma saatleriniz nedir?",
            "Menüyü görebilir miyim acaba?",
            "Teşekkürler, sipariş vermek istiyorum",
            "Restoranınız kaçta açılıyor?",
        ],
    )
    def test_turkish_classification(self, engine: LanguageDetectionEngine, text: str) -> None:
        assert engine.detect_language(text) == LanguageCode.TR

    # English language classification
    @pytest.mark.parametrize(
        "text",
        [
            "I want to speak with a human manager",
            "What are your business hours?",
            "Can you please send me the menu?",
            "Where is your restaurant located?",
            "I have a complaint about my delivery order",
            "Hello, could you help me with pricing?",
        ],
    )
    def test_english_classification(self, engine: LanguageDetectionEngine, text: str) -> None:
        assert engine.detect_language(text) == LanguageCode.EN

    def test_fallback_on_empty_and_unknown(self, engine: LanguageDetectionEngine) -> None:
        assert engine.detect_language("") == LanguageCode.AZ
        assert engine.detect_language("   ") == LanguageCode.AZ
        assert engine.detect_language("123456789") == LanguageCode.AZ


# =============================================================================
# 4. Strict Escalation Intent Detection & AC Verification
# =============================================================================


class TestEscalationIntentDetection:
    """Verifies strict acceptance criteria 1, 4 and multi-lingual human escalation."""

    # AC-1: Azerbaijani informal spelling variants
    @pytest.mark.parametrize(
        "query",
        [
            "menecerle danismaq isteyirem",
            "operatora baglayin",
            "sikayetim var",
            "canli destege qosun",
            "rehberlikle elaqe saxlayin",
            "sistem islemir",
            "zehmet olmasa komek edin",
            "insanla danismaq isteyirem",
            "administratora yonlendirin",
            "menecer cagir",
            "mene zeng vursunlar",
            "şikayətim var, rəhbərliklə danışmaq istəyirəm",
            "canlı dəstək xidmətinə bağlayın",
            "proqram işləmir kömək edin",
        ],
    )
    def test_ac1_azerbaijani_informal_escalation(
        self,
        engine: LanguageDetectionEngine,
        query: str,
    ) -> None:
        assert engine.is_escalation_intent(query) is True
        assert engine.is_escalation_intent(query, lang=LanguageCode.AZ) is True
        assert engine.is_escalation_intent(query, lang="AZ") is True

    # AC-4: English escalation requests
    @pytest.mark.parametrize(
        "query",
        [
            "I want to speak with a human manager",
            "Can you connect me to an operator?",
            "I have a complaint about my order",
            "Please transfer me to a human agent",
            "I need customer support immediately",
            "Connect me with a customer representative",
            "Speak to a real person please",
            "I need to talk to someone right now",
        ],
    )
    def test_ac4_english_escalation(
        self,
        engine: LanguageDetectionEngine,
        query: str,
    ) -> None:
        assert engine.is_escalation_intent(query) is True
        assert engine.is_escalation_intent(query, lang=LanguageCode.EN) is True
        assert engine.is_escalation_intent(query, lang="en") is True

    # Russian escalation requests
    @pytest.mark.parametrize(
        "query",
        [
            "Позовите менеджера",
            "Соедините с оператором",
            "У меня жалоба на обслуживание",
            "Хочу поговорить с живым человеком",
            "Свяжите с администратором",
            "Позвоните мне",
        ],
    )
    def test_russian_escalation(
        self,
        engine: LanguageDetectionEngine,
        query: str,
    ) -> None:
        assert engine.is_escalation_intent(query) is True
        assert engine.is_escalation_intent(query, lang=LanguageCode.RU) is True

    # Turkish escalation requests
    @pytest.mark.parametrize(
        "query",
        [
            "Yetkili biriyle görüşmek istiyorum",
            "Müşteri temsilcisine bağlar mısınız?",
            "Bir şikayetim var",
            "Canlı destek istiyorum",
            "Bir insanla konuşmak istiyorum",
        ],
    )
    def test_turkish_escalation(
        self,
        engine: LanguageDetectionEngine,
        query: str,
    ) -> None:
        assert engine.is_escalation_intent(query) is True
        assert engine.is_escalation_intent(query, lang=LanguageCode.TR) is True

    def test_code_switching_cross_lingual_escalation(self, engine: LanguageDetectionEngine) -> None:
        # User in Azerbaijani session using English escalation term
        assert engine.is_escalation_intent("Mənə human manager lazımdır", lang=LanguageCode.AZ) is True
        # User in Russian session using English keyword
        assert engine.is_escalation_intent("Срочно позвать human agent", lang=LanguageCode.RU) is True


# =============================================================================
# 5. Routine Non-Escalation Queries (Strict AC-5, Zero False Positives)
# =============================================================================


class TestRoutineNonEscalationQueries:
    """Verifies strict acceptance criterion 5: zero false positive escalations on standard business inquiries."""

    # AC-5: Standard non-escalation queries
    @pytest.mark.parametrize(
        "query",
        [
            "Qiymətlər haqqında məlumat verin",
            "İş saatlarınız neçəyə qədərdir?",
            "Menyunuzu göndərin",
            "Salam, necəsiniz?",
            "Kababın qiyməti neçəyədir?",
            "Sabah saat 2-yə rezervasiya etmək olar?",
            "Çatdırılma xidmətiniz var?",
            "Filialınız harada yerləşir?",
            "Menyuda hansı salatlar var?",
            "Sifarişim nə vaxt çatdırılacaq?",
            "Kofe və desertləriniz var?",
            "Bazar günləri açıqsınız?",
        ],
    )
    def test_ac5_azerbaijani_routine_queries_return_false(
        self,
        engine: LanguageDetectionEngine,
        query: str,
    ) -> None:
        assert engine.is_escalation_intent(query) is False
        assert engine.is_escalation_intent(query, lang=LanguageCode.AZ) is False

    @pytest.mark.parametrize(
        "query",
        [
            "What are your business hours?",
            "Where is your restaurant located?",
            "Can you send me the price list?",
            "How much is the beef burger?",
            "Do you have delivery to downtown?",
            "Can I book a table for tomorrow evening?",
            "What kind of drinks do you have on the menu?",
        ],
    )
    def test_english_routine_queries_return_false(
        self,
        engine: LanguageDetectionEngine,
        query: str,
    ) -> None:
        assert engine.is_escalation_intent(query) is False
        assert engine.is_escalation_intent(query, lang=LanguageCode.EN) is False

    @pytest.mark.parametrize(
        "query",
        [
            "Здравствуйте, сколько стоит доставка?",
            "Какое время работы вашего ресторана?",
            "Покажите меню пожалуйста",
            "Где находится ваш филиал?",
            "Какие десерты у вас есть?",
            "Можно забронировать столик на четверых?",
        ],
    )
    def test_russian_routine_queries_return_false(
        self,
        engine: LanguageDetectionEngine,
        query: str,
    ) -> None:
        assert engine.is_escalation_intent(query) is False
        assert engine.is_escalation_intent(query, lang=LanguageCode.RU) is False

    @pytest.mark.parametrize(
        "query",
        [
            "Merhaba, çalışma saatleriniz nedir?",
            "Menüyü görebilir miyim?",
            "Fiyat listenizi gönderir misiniz?",
            "Restoranınız nerede?",
            "Paket servisiniz var mı?",
            "Rezervasyon yaptırmak istiyorum",
        ],
    )
    def test_turkish_routine_queries_return_false(
        self,
        engine: LanguageDetectionEngine,
        query: str,
    ) -> None:
        assert engine.is_escalation_intent(query) is False
        assert engine.is_escalation_intent(query, lang=LanguageCode.TR) is False

    def test_empty_and_whitespace_queries_return_false(self, engine: LanguageDetectionEngine) -> None:
        assert engine.is_escalation_intent("") is False
        assert engine.is_escalation_intent("   ") is False


# =============================================================================
# 6. UML 2.5 Compliance & Aliases
# =============================================================================


class TestUMLInterfaceCompliance:
    """Verifies UML 2.5 CamelCase method and attribute aliases."""

    def test_uml_camel_case_aliases(self, engine: LanguageDetectionEngine) -> None:
        # supportedLanguages
        assert engine.supportedLanguages == engine.supported_languages

        # normalizeAzerbaijaniChars
        raw = "M@n menecerl@ daniSHmaq ist@yir@m"
        assert engine.normalizeAzerbaijaniChars(raw) == engine.normalize_azerbaijani_chars(raw)

        # detectLanguage
        ru_text = "Здравствуйте"
        assert engine.detectLanguage(ru_text) == engine.detect_language(ru_text)
        assert engine.detectLanguage(ru_text) == LanguageCode.RU

        # isEscalationIntent
        esc_text = "menecerle danismaq isteyirem"
        assert engine.isEscalationIntent(esc_text) is True
        assert engine.isEscalationIntent(esc_text, lang=LanguageCode.AZ) is True
        assert engine.isEscalationIntent("Qiymətlər haqqında məlumat verin") is False
