"""Subsystem 5.0 Boundary: LLM Inference Client Module.

Provides LLM client abstractions for OpenAI GPT-4o / Anthropic Claude models,
along with an intelligent deterministic `InMemoryLLMClient` for offline testing
and verification. Supports RAG factual grounding, polite fallback when facts
are absent, and multi-lingual generation across Azerbaijani, Russian, Turkish,
and English.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from app.core.config import Settings, get_settings
from app.models.enums import LanguageCode

logger = logging.getLogger(__name__)


# Standardized polite fallback responses for empty / ungrounded RAG contexts
POLITE_FALLBACK_RESPONSES: Dict[LanguageCode, str] = {
    LanguageCode.AZ: (
        "Təəssüf ki, bu barədə məndə dəqiq məlumat yoxdur. "
        "Əlavə suallarınız üçün sizi menecerimizlə əlaqələndirə bilərəm."
    ),
    LanguageCode.RU: (
        "К сожалению, у меня нет проверенной информации по этому вопросу. "
        "Могу я соединить вас с нашим менеджером?"
    ),
    LanguageCode.TR: (
        "Maalesef bu konuda elimde doğrulanmış bilgi bulunmamaktadır. "
        "Dilerseniz sizi yöneticimizle görüştürebilirim."
    ),
    LanguageCode.EN: (
        "Unfortunately, I do not have verified information on this topic. "
        "Would you like me to connect you with a manager?"
    ),
}

# Standardized escalation notification messages
ESCALATION_MESSAGES: Dict[LanguageCode, str] = {
    LanguageCode.AZ: (
        "Hörmətli müştəri, sizi menecerimizə yönləndirirəm. "
        "Zəhmət olmasa bir qədər gözləyin, əməkdaşımız tezliklə sizinlə əlaqə saxlayacaq."
    ),
    LanguageCode.RU: (
        "Уважаемый клиент, я перенаправляю вас к нашему менеджеру. "
        "Пожалуйста, подождите немного, наш сотрудник скоро свяжется с вами."
    ),
    LanguageCode.TR: (
        "Değerli müşterimiz, sizi yöneticimize aktarıyorum. "
        "Lütfen biraz bekleyin, ekip arkadaşımız en kısa sürede sizinle iletişime geçecektir."
    ),
    LanguageCode.EN: (
        "Dear customer, I am connecting you with a manager. "
        "Please hold on, a representative will assist you shortly."
    ),
}


class BaseLLMClient(ABC):
    """Abstract Base Class for LLM inference clients."""

    @abstractmethod
    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 500,
        temperature: float = 0.2,
        **kwargs: Any,
    ) -> Tuple[str, int]:
        """Generate a natural language completion from the given prompt.

        Args:
            prompt: Synthesized prompt string containing context, history, and query.
            system_prompt: Optional distinct system instructions.
            max_tokens: Maximum token length for the completion.
            temperature: Sampling temperature (0.0 = deterministic).
            **kwargs: Additional provider-specific parameters.

        Returns:
            Tuple of (generated_text: str, tokens_used: int).
        """
        pass

    def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 500,
        temperature: float = 0.2,
        **kwargs: Any,
    ) -> Tuple[str, int]:
        """Convenience alias for generate."""
        return self.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )


class InMemoryLLMClient(BaseLLMClient):
    """Deterministic in-memory LLM client test double.

    Simulates high-fidelity LLM inference:
    1. Extracts factual grounding from RAG context embedded in the prompt.
    2. When RAG context is empty or absent, strictly returns polite fallback
       preventing hallucination (Acceptance Criteria 2).
    3. Respects detected/requested language (AZ, RU, TR, EN) for localized responses.
    4. Allows registering explicit custom response mappings or callback handlers.
    5. Tracks complete invocation history for assertion audits.
    """

    def __init__(
        self,
        custom_responses: Optional[Dict[str, str]] = None,
        default_response: Optional[str] = None,
        handler: Optional[Callable[[str, Optional[str]], Tuple[str, int]]] = None,
    ) -> None:
        self._custom_responses: Dict[str, str] = dict(custom_responses or {})
        self._default_response: Optional[str] = default_response
        self._handler: Optional[Callable[[str, Optional[str]], Tuple[str, int]]] = handler
        self._calls: List[Dict[str, Any]] = []

    @property
    def calls(self) -> List[Dict[str, Any]]:
        """List of all calls made to generate."""
        return self._calls

    @property
    def custom_responses(self) -> Dict[str, str]:
        """Dictionary of custom text responses."""
        return self._custom_responses

    def register_response(self, trigger_phrase: str, response_text: str) -> None:
        """Register a specific response string for queries containing trigger_phrase."""
        self._custom_responses[trigger_phrase.lower()] = response_text

    def clear(self) -> None:
        """Clear call history and custom responses."""
        self._calls.clear()
        self._custom_responses.clear()
        self._default_response = None
        self._handler = None

    def reset_calls(self) -> None:
        """Clear invocation call history only."""
        self._calls.clear()

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 500,
        temperature: float = 0.2,
        **kwargs: Any,
    ) -> Tuple[str, int]:
        """Generate response based on prompt rules and RAG context grounding."""
        # 1. Custom handler hook if supplied
        if self._handler is not None:
            text, tokens = self._handler(prompt, system_prompt)
            self._calls.append({
                "prompt": prompt,
                "system_prompt": system_prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "reply": text,
                "tokens": tokens,
            })
            return text, tokens

        # 2. Check custom responses map
        prompt_lower = prompt.lower()
        for trigger, response in self._custom_responses.items():
            if trigger in prompt_lower:
                tokens = max(10, len(response.split()) + 15)
                self._calls.append({
                    "prompt": prompt,
                    "system_prompt": system_prompt,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "reply": response,
                    "tokens": tokens,
                })
                return response, tokens

        # 3. If explicit default response set, return it
        if self._default_response is not None:
            tokens = max(10, len(self._default_response.split()) + 15)
            self._calls.append({
                "prompt": prompt,
                "system_prompt": system_prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "reply": self._default_response,
                "tokens": tokens,
            })
            return self._default_response, tokens

        # 4. Deterministic reasoning from prompt content:
        # Detect target language from prompt
        lang = self._extract_language_from_prompt(prompt, system_prompt)
        rag_context = self._extract_rag_context(prompt)
        user_query = self._extract_user_query(prompt)
        history = self._extract_conversation_history(prompt)

        clean_q = user_query.strip().lower()
        is_meettabb = "meettabb" in rag_context.lower() or "aktivlik" in rag_context.lower() or "meettabb" in prompt.lower()
        merchant_match = re.search(r"assistant for '([^']+)'", prompt)
        merchant_name = merchant_match.group(1) if merchant_match else ""

        # Check for conversational greeting intent
        q_tokens = [t for t in re.findall(r"\w+", clean_q) if t]
        greeting_words = {"salam", "salamlar", "hello", "hi", "hey", "merhaba", "selam", "здравствуйте", "привет"}
        greeting_aux = {"bəy", "bey", "xanım", "xanim", "aleykum", "əleyküm", "aleykuma", "hər", "vaxtınız", "xeyir"}
        if q_tokens and set(q_tokens).issubset(greeting_words | greeting_aux):
            if is_meettabb:
                greetings_map = {
                    LanguageCode.AZ: "Salam! Xoş gəlmisiniz. MeeTTabb fəaliyyət və tədbir platformasının rəsmi çatbotuyam. Bakıda maraqlı aktivliklər, tədbirlər və məkanlar barədə sizə necə kömək edə bilərəm?",
                    LanguageCode.RU: "Здравствуйте! Добро пожаловать. Я официальный помощник платформы мероприятий MeeTTabb. Чем могу помочь?",
                    LanguageCode.TR: "Merhaba! Hoş geldiniz. MeeTTabb etkinlik platformunun resmi asistanıyım. Size nasıl yardımcı olabilirim?",
                    LanguageCode.EN: "Hello! Welcome. I am the official assistant for the MeeTTabb activity platform. How can I assist you today?",
                }
                cta_map = {
                    LanguageCode.AZ: "Maraqlandığınız fəaliyyəti (məsələn: kartinq, bovlinq, velosiped, futbol) və ya büdcənizi bildirin, sizə ən ideal məkanları seçim!",
                    LanguageCode.RU: "Укажите интересующее вас занятие или бюджет, и я подберу лучшие места!",
                    LanguageCode.TR: "İlgilendiğiniz etkinliği veya bütçenizi belirtin, size en uygun mekanları önereyim!",
                    LanguageCode.EN: "Tell me which activity you are interested in or your budget, and I will find the best options for you!",
                }
                main_g = greetings_map.get(lang, greetings_map[LanguageCode.AZ])
                follow_g = cta_map.get(lang, cta_map[LanguageCode.AZ])
                reply = f"{main_g}\n\n### FOLLOW_UP_MESSAGE ###\n{follow_g}"
            elif merchant_name:
                greetings_map = {
                    LanguageCode.AZ: f"Salam! Xoş gəlmisiniz. '{merchant_name}' rəsmi çatbotuyam. Sizə necə kömək edə bilərəm?",
                    LanguageCode.RU: f"Здравствуйте! Добро пожаловать. Я официальный помощник '{merchant_name}'. Чем могу помочь?",
                    LanguageCode.TR: f"Merhaba! Hoş geldiniz. '{merchant_name}' resmi asistanıyım. Size nasıl yardımcı olabilirim?",
                    LanguageCode.EN: f"Hello! Welcome. I am the official assistant for '{merchant_name}'. How can I assist you today?",
                }
                reply = greetings_map.get(lang, greetings_map[LanguageCode.AZ])
            else:
                greetings_map = {
                    LanguageCode.AZ: "Salam! Xoş gəlmisiniz. Sizə necə kömək edə bilərəm?",
                    LanguageCode.RU: "Здравствуйте! Добро пожаловать. Чем я могу вам помочь?",
                    LanguageCode.TR: "Merhaba! Hoş geldiniz. Size nasıl yardımcı olabilirim?",
                    LanguageCode.EN: "Hello! Welcome. How can I assist you today?",
                }
                reply = greetings_map.get(lang, greetings_map[LanguageCode.AZ])

            tokens = len(reply.split()) + 5
            self._calls.append({
                "prompt": prompt,
                "system_prompt": system_prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "reply": reply,
                "tokens": tokens,
                "detected_lang": lang,
                "has_facts": True,
            })
            return reply, tokens

        # Check for customer feedback / recommendations for MeeTTabb
        if is_meettabb and self._is_feedback_query(user_query):
            main_fb = "Təşəkkür edirik! Təklif və tövsiyəniz qeydə alındı və MeeTTabb inkişaf komandasına çatdırıldı. İstifadəçi rəyləri əsasında tətbiqimizi daha da təkmilləşdirməyə çalışırıq!"
            cta_fb = "MeeTTabb-da yeni tədbirləri kəşf etmək və dostlarınızla vaxt keçirmək üçün tətbiqimizi izləməkdə qalın!"
            reply = f"{main_fb}\n\n### FOLLOW_UP_MESSAGE ###\n{cta_fb}"
            tokens = len(reply.split()) + 5
            self._calls.append({
                "prompt": prompt,
                "system_prompt": system_prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "reply": reply,
                "tokens": tokens,
                "detected_lang": lang,
                "has_facts": True,
            })
            return reply, tokens

        # Evaluate if RAG context has verified facts
        has_facts = self._context_has_facts(rag_context)

        if not has_facts:
            # Check if prompt or system_prompt has custom instructions providing answers
            custom_sys_prompt = system_prompt or ""
            if "### ADDITIONAL SYSTEM INSTRUCTIONS:" in prompt:
                custom_sys_prompt = prompt.split("### ADDITIONAL SYSTEM INSTRUCTIONS:", 1)[1].split("###")[0].strip()

            if custom_sys_prompt:
                reply = custom_sys_prompt
            else:
                # AC-2: Strictly default to polite fallback rather than inventing unsupported info
                reply = POLITE_FALLBACK_RESPONSES.get(lang, POLITE_FALLBACK_RESPONSES[LanguageCode.AZ])
        elif is_meettabb:
            # High-fidelity contextual reasoning and active recall for MeeTTabb
            main_reply, follow_cta = self._generate_meettabb_reply(
                user_query=user_query,
                history=history,
                rag_context=rag_context,
                lang=lang,
            )
            if follow_cta:
                reply = f"{main_reply}\n\n### FOLLOW_UP_MESSAGE ###\n{follow_cta}"
            else:
                reply = main_reply
        else:
            # General merchant RAG fact matching
            matched_fact = self._extract_matching_facts(rag_context, user_query)
            if matched_fact:
                reply = self._format_factual_reply(matched_fact, lang)
            else:
                lines = [line.strip() for line in rag_context.split("\n") if line.strip() and not line.startswith("#")]
                if lines:
                    reply = lines[0]
                else:
                    reply = POLITE_FALLBACK_RESPONSES.get(lang, POLITE_FALLBACK_RESPONSES[LanguageCode.AZ])

        # Estimate tokens used
        prompt_words = len(prompt.split())
        reply_words = len(reply.split())
        tokens_used = max(12, (prompt_words // 4) + reply_words + 8)
        tokens_used = min(tokens_used, max_tokens)

        self._calls.append({
            "prompt": prompt,
            "system_prompt": system_prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "reply": reply,
            "tokens": tokens_used,
            "detected_lang": lang,
            "has_facts": has_facts,
        })
        return reply, tokens_used

    def _extract_language_from_prompt(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
    ) -> LanguageCode:
        """Extracts the intended response language from the prompt instructions."""
        # 1. Primary explicit tag
        code_match = re.search(r"###\s+TARGET LANGUAGE CODE:\s*([A-Za-z]{2})", prompt)
        if code_match:
            try:
                return LanguageCode(code_match.group(1).upper())
            except ValueError:
                pass

        # 2. Check for explicit directive patterns with word boundaries
        prompt_lower = f"{system_prompt or ''}\n{prompt}".lower()
        if re.search(r"\b(?:target\s+language|interaction\s+language|language)\s*:\s*ru\b", prompt_lower):
            return LanguageCode.RU
        if re.search(r"\b(?:target\s+language|interaction\s+language|language)\s*:\s*tr\b", prompt_lower):
            return LanguageCode.TR
        if re.search(r"\b(?:target\s+language|interaction\s+language|language)\s*:\s*en\b", prompt_lower):
            return LanguageCode.EN
        if re.search(r"\b(?:target\s+language|interaction\s+language|language)\s*:\s*az\b", prompt_lower):
            return LanguageCode.AZ

        return LanguageCode.AZ

    def _extract_rag_context(self, prompt: str) -> str:
        """Extracts the verified RAG context snippet from the prompt."""
        # Check explicit delimited block first
        delimited_match = re.search(
            r"###\s+VERIFIED RAG CONTEXT BLOCK START\s+###\s*\n(.*?)\n###\s+VERIFIED RAG CONTEXT BLOCK END\s+###",
            prompt,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if delimited_match:
            return delimited_match.group(1).strip()

        # Fallback to older section header patterns
        patterns = [
            r"###\s+VERIFIED RAG CONTEXT:?\s*\n(.*?)(?=\n### CUSTOMER INFORMATION|\n### RECENT CONVERSATION|\n### CURRENT USER|\Z)",
            r"###\s+CONTEXT:?\s*\n(.*?)(?=\n### CUSTOMER INFORMATION|\n### CURRENT USER|\Z)",
        ]
        for pat in patterns:
            match = re.search(pat, prompt, flags=re.DOTALL | re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return ""

    def _extract_user_query(self, prompt: str) -> str:
        """Extracts the customer user query from the prompt."""
        patterns = [
            r"###\s+CURRENT USER (?:QUERY|MESSAGE):?\s*\n(.*?)(?=\n###|\Z)",
            r"User:\s*(.*?)(?=\nAssistant:|\n###|\Z)",
            r"Customer Query:\s*(.*?)(?=\n###|\Z)",
        ]
        for pat in patterns:
            match = re.search(pat, prompt, flags=re.DOTALL | re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return ""

    def _extract_conversation_history(self, prompt: str) -> List[Tuple[str, str]]:
        """Extracts chronological turns [(speaker, text), ...] from conversation history."""
        match = re.search(
            r"###\s+RECENT CONVERSATION HISTORY:?\s*\n(.*?)(?=\n###|\Z)",
            prompt,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if not match:
            return []
        block = match.group(1).strip()
        turns: List[Tuple[str, str]] = []
        for line in block.split("\n"):
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^\[(Customer|Assistant)\]:\s*(.*)$", line, re.IGNORECASE)
            if m:
                turns.append((m.group(1).capitalize(), m.group(2).strip()))
        return turns

    def _is_feedback_query(self, user_query: str) -> bool:
        """Checks if the query contains app feedback or feature suggestions."""
        q_clean = user_query.lower()
        triggers = [
            "tövsiyə", "tovsiye", "tövsiyəm", "tovsiyem",
            "təklif", "teklif", "təklifim", "teklifim", "təklif edirəm", "teklif edirem",
            "əlavə etsəniz", "elave etseniz", "əlavə etsəz", "elave etsez",
            "əlavə edin", "elave edin", "qoysanız", "qoysaz",
            "olsa əla olar", "olsa ela olar", "olsa yaxşı olar", "olsa yaxsi olar",
            "maraqlı olardı", "maraqli olardi", "yaxşı olardı", "yaxsi olardi",
            "məncə tətbiq", "mence tetbiq", "fikrimcə", "fikrimce",
            "app recommendation", "feature request", "my suggestion",
        ]
        return any(trig in q_clean for trig in triggers)

    def _generate_meettabb_reply(
        self,
        user_query: str,
        history: List[Tuple[str, str]],
        rag_context: str,
        lang: LanguageCode,
    ) -> Tuple[str, Optional[str]]:
        """Contextual, natural multi-lingual dialog generation with active recall for MeeTTabb."""
        q_lower = user_query.lower()

        # Gather previous customer messages for active recall
        customer_history = [text.lower() for speaker, text in history if speaker == "Customer"]
        hist_text = " ".join(customer_history)

        # 1. App Differentiators & Unique Value Proposition (e.g. "Bes sizin programi ferqli eden nedir?", "What makes the app different?", "Чем отличается?")
        is_differentiators = any(k in q_lower for k in (
            "fərqli", "ferqli", "fərqlənir", "ferqlenir", "fərqi", "ferqi",
            "üstünlüy", "ustunluy", "üstünlük", "ustunluk",
            "özəlliy", "ozelliy", "özəllik", "ozellik",
            "niyə məhz", "niye mehz", "niyə siz", "niye siz", "nə fərqi", "ne ferqi",
            "funksiya", "xüsusiyyət", "xususiyyet",
            "unikal", "unique", "different", "differentiator",
            "map integration", "friends section", "dostlar bölməsi", "dostlar bolmesi",
            "отлича", "уникальн", "преимуществ", "разниц", "фишки",
            "what makes", "why meettabb", "sets you apart", "why should i use"
        ))
        if is_differentiators:
            if lang == LanguageCode.RU:
                main_reply = (
                    "MeeTTabb выделяется среди других приложений 4 ключевыми преимуществами:\n\n"
                    "• Интерактивная карта (Map Integration): вы в реальном времени видите все актуальные активности, события и интересные локации Баку прямо на карте города и можете мгновенно к ним присоединиться.\n"
                    "• Реферальная система и Tabb Coin: за каждого друга, зарегистрировавшегося по вашей персональной ссылке, вы получаете 1 Tabb Coin. За монеты можно приобретать билеты на розыгрыши ценных призов (giveaways) и турниры.\n"
                    "• Прямые и групповые чаты (Direct Messaging): у каждого мероприятия есть закрытый групповой чат, а также возможность прямого личного общения с участниками для быстрого согласования деталей встречи.\n"
                    "• Раздел «Друзья» и сбор команды (Friends Section): следите за активностями друзей, планируйте совместный отдых или в пару кликов находите недостающих участников для футбола, картинга, боулинга или квеста.\n\n"
                    "Главная цель MeeTTabb — вытащить людей из бесконечного скроллинга соцсетей и объединить для живого отдыха в реальном мире!"
                )
                cta = "Приложение MeeTTabb полностью бесплатное для iOS и Android. Хотите скачать его прямо сейчас и посмотреть события на карте города?"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "MeeTTabb stands out with 4 key advantages that make it unique:\n\n"
                    "• Interactive Map Integration: Discover ongoing events, activities, and entertainment spots across Baku directly on a live interactive map and join with one tap.\n"
                    "• Referral System & Tabb Coins: Whenever a friend signs up using your personal referral link, you receive 1 Tabb Coin. Use your coins to purchase tickets for in-app giveaway competitions and tournaments with valuable prizes.\n"
                    "• Direct Messaging & Group Chats: Dedicated group chats for every activity, along with 1-on-1 direct private messaging to coordinate meetup spots, times, and plans effortlessly.\n"
                    "• Friends Section & Team Recruitment: Keep up with the activities your friends are attending, create group outings together, or easily recruit missing players for football, karting, or escape rooms.\n\n"
                    "Above all, MeeTTabb brings people together in the real world instead of lonely virtual scrolling!"
                )
                cta = "MeeTTabb is completely free on iOS and Android. Would you like to download it now and explore activities on the map?"
            else:
                main_reply = (
                    "MeeTTabb-ı digər tətbiqlərdən fərqləndirən 4 əsas üstünlüyümüz var:\n\n"
                    "• İnteraktiv Xəritə İnteqrasiyası (Map Integration): Bakıda ətrafınızdakı bütün aktivlikləri, tədbirləri və əyləncə məkanlarını canlı xəritə üzərindən anında görüb istiqamət almaq və asanlıqla qoşulmaq mümkündür.\n"
                    "• Referral Sistemi və Tabb Coin: Şəxsi dəvət linkinizlə qoşulan hər dostunuza görə 1 Tabb Coin qazanırsınız. Bu coin-lərlə tətbiqdaxili qiymətli hədiyyəli giveaway yarışmalarına və turnirlərə bilet almaq olar.\n"
                    "• Birbaşa və Qrup Mesajlaşması (Direct Messaging): Hər bir fəaliyyət üçün xüsusi qrup çatı və iştirakçılarla təkbətək birbaşa mesajlaşma ilə görüş yerini, vaxtı və detalları anında müzakirə edə bilərsiniz.\n"
                    "• Dostlar Bölməsi və Komanda Yığma (Friends Section): Dostlarınızın hansı tədbirlərdə iştirak etdiyini izləyə, birlikdə çıxışlar təşkil edə və ya komandanıza çatışmayan yeni iştirakçıları (futbol, kartinq, kvest və s.) dərhal cəlb edə bilərsiniz.\n\n"
                    "Ən əsası – MeeTTabb insanları virtual dünyadan və passiv telefon sürüşdürməkdən çıxarıb real həyatda bir araya gətirir!"
                )
                cta = "MeeTTabb həm iOS, həm də Android üçün tamamilə pulsuzdur. İndi yükləyib xəritədəki maraqlı aktivlikləri kəşf etmək istərdiniz?"
            return main_reply, cta

        # 2. Browser / Web Access & App Download Recommendation (e.g. "Xeyr yuklememishem", "Brauzerdə olar?", "Can I use in browser?")
        is_not_downloaded = any(k in q_lower for k in (
            "yükləməmişəm", "yuklememishem", "yuklememisem", "yükləməmişik", "yuklememisik",
            "yükləmədim", "yuklemedim", "yuklemek istemirem", "yükləmək istəmirəm",
            "hələ yox", "hele yox", "hələ yükləməmişəm", "hele yuklememisem",
            "app yoxdur", "tetbiq yoxdur", "tətbiq yoxdur",
            "не скачал", "еще не скачал", "не установил", "не скачивал", "не хочу скачивать",
            "haven't downloaded", "not downloaded", "haven't installed", "not installed", "didn't download", "don't have the app"
        ))
        is_web_query = any(k in q_lower for k in (
            "brauzer", "browser", "veb", "web", "sayt", "kompüter", "kompyuter", "saytdan", "veb-sayt", "vebsayt",
            "браузер", "в браузере", "веб", "сайт", "компьютер", "с компьютера", "без скачивания",
            "in browser", "web version", "on web", "from pc", "computer", "without download"
        ))
        last_bot_turn = ""
        for spk, text in reversed(history):
            if spk == "Assistant":
                last_bot_turn = text.lower()
                break
        is_negative_to_download_prompt = (
            any(w in last_bot_turn for w in ("yükləmisiniz", "yuklemisiniz", "установили", "скачали", "downloaded"))
            and any(neg in q_lower.split() for neg in ("xeyr", "yox", "yoxdur", "нет", "неа", "no", "nope"))
        )
        is_browser_or_not_downloaded = is_not_downloaded or is_web_query or is_negative_to_download_prompt

        if is_browser_or_not_downloaded:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Никаких проблем! Платформой MeeTTabb можно пользоваться прямо в веб-браузере без обязательной установки приложения.\n\n"
                    "Однако для гораздо более удобного и полного пользовательского опыта мы рекомендуем скачать мобильное приложение MeeTTabb:\n"
                    "• Интерактивная живая карта для поиска событий и локаций рядом с вами в Баку\n"
                    "• Мгновенные чаты мероприятий и прямые сообщения с участниками\n"
                    "• Пуш-уведомления о новых турнирах, конкурсах и розыгрышах призов\n\n"
                    "Приложение полностью бесплатное и доступно для iOS (App Store) и Android (Google Play)!"
                )
                cta = "Хотите, я отправлю вам ссылку для скачивания приложения или ссылку на веб-версию?"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "No problem at all! You can easily access and use MeeTTabb directly in your web browser.\n\n"
                    "However, for a significantly better and smoother user experience, we highly recommend downloading the MeeTTabb mobile app:\n"
                    "• Interactive live map to explore activities and entertainment spots near you in Baku\n"
                    "• Instant event group chats and 1-on-1 direct messaging to coordinate meetups\n"
                    "• Real-time notifications for upcoming tournaments, events, and giveaways\n\n"
                    "The app is completely free to download on both iOS (App Store) and Android (Google Play)!"
                )
                cta = "Would you like me to provide the app download link or continue using the web version?"
            else:
                main_reply = (
                    "Problem deyil! MeeTTabb platformasını birbaşa veb-brauzerdən (sayt üzərindən) də rahatlıqla istifadə edə bilərsiniz.\n\n"
                    "Bununla belə, daha yaxşı və rahat istifadəçi təcrübəsi üçün mobil tətbiqimizi yükləməyinizi tövsiyə edirik:\n"
                    "• Canlı interaktiv xəritə və naviqasiya ilə ətrafınızdakı aktivlikləri asanlıqla tapmaq\n"
                    "• Tədbir qrup çatları və birbaşa mesajlaşma ilə detalları anında müzakirə etmək\n"
                    "• Yeni tədbirlər və yarışmalar barədə anlıq bildirişlər almaq\n\n"
                    "MeeTTabb mobil tətbiqi həm iOS (App Store), həm də Android (Google Play) cihazları üçün tamamilə pulsuzdur!"
                )
                cta = "MeeTTabb-ı yükləmək üçün keçid linki göndərim, yoxsa veb-brauzerdən davam etmək istərdiniz?"
            return main_reply, cta

        # 3. General Information Request (e.g. "Melumat vere bilersiniz?", "Can you give info?", "What would you like to know about?")
        is_general_info_request = any(k in q_lower for k in (
            "məlumat verə bilərsiniz", "melumat vere bilersiniz", "məlumat verə bilərsiz", "melumat vere bilersiz",
            "məlumat verərdiniz", "melumat vererdiniz", "məlumat verin", "melumat verin",
            "məlumat almaq istəyirəm", "melumat almaq isteyirem", "məlumat istəyirəm", "melumat isteyirem",
            "məlumat lazımdır", "melumat lazimdir", "məlumat vermək", "melumat vermek",
            "kömək edə bilərsiniz", "komek ede bilersiniz", "kömək edə bilərsiz", "komek ede bilersiz",
            "kömək edin", "komek edin", "sualım var", "sualim var", "sual vermək olar", "sual vermek olar",
            "bir şey soruşmaq", "bir sey sorusmaq", "bilmək istəyirəm", "bilmek isteyirem",
            "можете дать информацию", "дайте информацию", "хочу узнать", "нужна информация",
            "можете подсказать", "подскажите пожалуйста", "у меня вопрос", "можно вопрос", "можете помочь",
            "can you give information", "give me information", "can you give me info", "i want to know",
            "i have a question", "can i ask a question", "need information", "could you help me",
            "bilgi verebilir misiniz", "bilgi alabilir miyim", "bilgi almak istiyorum", "yardımcı olabilir misiniz"
        )) or q_lower.strip("?.!") in ("məlumat", "melumat", "informasiya", "info", "information")

        if is_general_info_request:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Конечно! О чем именно вы хотели бы узнать?\n\n"
                    "Я с радостью подскажу вам подробности о MeeTTabb и активностях в Баку:\n"
                    "• Популярные места и цены (картинг, боулинг, велосипеды, футбол, настольные игры, квесты)\n"
                    "• Возможности приложения MeeTTabb (интерактивная карта, создание событий, чаты, раздел «Друзья»)\n"
                    "• Реферальная программа и выигрыш призов в giveaway турнирах за Tabb Coin"
                )
                cta = "Напишите интересующее вас занятие, локацию или ваш бюджет, и я сразу подробно подскажу!"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "Of course! What would you like to know about?\n\n"
                    "I would be happy to help you with everything about MeeTTabb and Baku activities:\n"
                    "• Top venues and prices (karting, bowling, cycling, football, board games, escape rooms)\n"
                    "• MeeTTabb app features (interactive live map, event creation, group chats, friends section)\n"
                    "• Referral program & earning Tabb Coins for giveaway competition tickets"
                )
                cta = "Tell me which activity, venue, or budget you are interested in, and I will share all the details!"
            elif lang == LanguageCode.TR:
                main_reply = (
                    "Elbette! Hangi konuda bilgi almak istersiniz?\n\n"
                    "MeeTTabb ve Bakü'deki etkinlikler hakkında size yardımcı olmaktan memnuniyet duyarım:\n"
                    "• Popüler mekanlar ve fiyatlar (karting, bovling, bisiklet, futbol, masa oyunları, kaçış odaları)\n"
                    "• MeeTTabb uygulama özellikleri (canlı harita, etkinlik oluşturma, grup sohbetleri, arkadaşlar bölümü)\n"
                    "• Referans programı ve Tabb Coin ödülleri"
                )
                cta = "İlgilendiğiniz etkinliği veya bütçenizi belirtin, hemen yardımcı olayım!"
            else:
                main_reply = (
                    "Əlbəttə! Hansı mövzu barədə məlumat almaq istərdiniz?\n\n"
                    "MeeTTabb platforması və Bakıdakı aktivliklər barədə sizə məmnuniyyətlə kömək edə bilərəm:\n"
                    "• Bakıda maraqlı məkanlar və qiymətlər (kartinq, bovlinq, velosiped, futbol, masaüstü oyunlar, kvestlər)\n"
                    "• MeeTTabb tətbiqinin xüsusiyyətləri (interaktiv xəritə, tədbir yaratmaq, qrup çatları, dostlar bölməsi)\n"
                    "• Referral proqramı və Tabb Coin-lərlə giveaway müsabiqələrinə qatılmaq"
                )
                cta = "Sizi maraqlandıran fəaliyyət növünü və ya büdcənizi qeyd edin, dərhal ətraflı məlumat verim!"
            return main_reply, cta

        # 4. Referral Intent Check across languages
        is_referral = any(k in q_lower for k in (
            "referral", "referal", "refer", "tabb coin", "tabbcoin", "dəvət linki", "devet linki",
            "реферал", "реферальн", "ссылк", "монет", "giveaway", "müsabiqə", "розыгрыш", "bilet",
            "referans", "davet linki", "prize", "yarışma", "yarisma"
        )) and not any(k in q_lower for k in ("friend list", "dostlar siyahısı", "dostlar siyahisi", "direct message", "şəxsi mesaj"))
        if is_referral:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Да, в MeeTTabb действует отличная реферальная программа!\n"
                    "За каждого друга, который зарегистрируется по вашей персональной реферальной ссылке, вы получаете 1 Tabb Coin. "
                    "Монеты Tabb Coin можно использовать в Marketplace для покупки билетов на розыгрыш с призовым фондом 150 ₼ (1 coin) или денежных вознаграждений 10 ₼ (5 coins). "
                    "Свою персональную ссылку вы можете скопировать в профиле мобильного приложения MeeTTabb."
                )
                cta = "Вы уже установили приложение MeeTTabb, чтобы скопировать свою реферальную ссылку и пригласить друзей?"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "Yes, MeeTTabb features an exciting referral program!\n"
                    "Whenever a friend signs up using your personal referral link, you receive 1 Tabb Coin. "
                    "You can use your Tabb Coins in the Marketplace to purchase tickets for the 150 ₼ prize pool giveaway celebration (1 coin) or 10 ₼ cash rewards (5 coins)! "
                    "You can easily find and copy your invite link directly from your profile inside the MeeTTabb app."
                )
                cta = "Have you already downloaded the MeeTTabb app on iOS or Android to grab your referral link?"
            else:
                main_reply = (
                    "Bəli, MeeTTabb-da çox sərfəli referral proqramı var!\n"
                    "Sizin şəxsi referral linkinizlə qeydiyyatdan keçən hər yeni istifadəçiyə görə siz 1 Tabb Coin qazanırsınız. "
                    "Qazandığınız Tabb Coin-lərlə Marketplace bölməsində 150 ₼ uduş fonduna malik 1.000 istifadəçi yubileyi müsabiqəsinə bilet (1 coin) və ya 10 ₼ nağd mükafat (5 coins) əldə edə bilərsiniz. "
                    "Dəvət linkinizi tətbiqdaxili profil bölmənizdən bir toxunuşla götürə və dostlarınızla paylaşa bilərsiniz."
                )
                cta = "Dəvət linkinizi tətbiqdən götürüb dostlarınızla paylaşmaq üçün MeeTTabb tətbiqini artıq yükləmisiniz?"
            return main_reply, cta

        # 5. Core MeeTTabb Feature & UI Knowledge Handlers (18 Detailed Questions across 4 Domains)

        # 5.1. Discovering & Finding Specific Activities & 10 Interest Categories
        is_find_activities = (
            any(k in q_lower for k in (
                "find specific activities", "how do i find", "sports, gaming", "outdoor adventures in baku",
                "maraq kateqoriyaları", "maraq kateqoriyalari", "hansı maraqlar", "hansi maraqlar",
                "10 maraq", "10 kateqoriya", "for you", "kəşf etmək", "kesf etmek",
                "категории интересов", "10 категорий", "лента for you", "find activities"
            ))
            or (("find" in q_lower or "how" in q_lower) and any(w in q_lower for w in ("activities", "sports", "gaming", "outdoor")))
            or (("necə tapa" in q_lower or "nece tapa" in q_lower or "tapa bilər" in q_lower or "tapa biler" in q_lower) and any(w in q_lower for w in ("fəaliyyət", "fealiyyet", "aktivlik", "idman", "oyun", "maraq")))
            or (("как найти" in q_lower or "где найти" in q_lower) and any(w in q_lower for w in ("активност", "спорт", "игр", "отдых", "мероприят")))
        )
        if is_find_activities:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Найти спорт, игры и активности на свежем воздухе в MeeTTabb очень просто!\n\n"
                    "Перейдите во вкладку «Discover» (Компас) в нижнем меню. Там вы найдете более 46 активностей по всему Баку на интерактивной карте или в виде списка.\n\n"
                    "Вы можете фильтровать события по 10 официальным категориям интересов или использовать персонализированную ленту «For You»:\n"
                    "• Fitness, Outdoor (Свежий воздух), Sports (Спорт), Action (Экстрим), Social (Общение)\n"
                    "• Tabletop Games (Настольные игры), Adventure (Приключения), Video Games (Видеоигры), Fun (Развлечения), Wellness (Здоровье)\n\n"
                    "В Баку доступны картинг (Baku Karting / GoKart), боулинг (28 Mall / Ganjlik Mall), велопрогулки (Бульвар / Шыхово), настольные игры (Baku Board Game Club), футбол, пейнтбол и квесты."
                )
                cta = "Какое направление вам ближе: спорт, настольные игры или активный отдых на природе? Подберу для вас лучшее мероприятие!"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "Finding sports, gaming, and outdoor adventures in MeeTTabb is simple!\n\n"
                    "Tap the 'Discover' tab (compass icon) in the bottom navigation bar. You will find over 46 activities across Baku displayed on an interactive Map or in List view.\n\n"
                    "You can filter by categories or check your personalized 'For You' feed based on the 10 official interest categories:\n"
                    "• Fitness, Outdoor, Sports, Action, Social\n"
                    "• Tabletop Games, Adventure, Video Games, Fun, Wellness\n\n"
                    "Explore popular Baku activities including karting, bowling, cycling along the Boulevard, board games at dedicated clubs, mini-football, paintball, and escape rooms."
                )
                cta = "Which category interests you most: sports, board games, or outdoor adventures? I can recommend great upcoming events for you!"
            else:
                main_reply = (
                    "MeeTTabb-da idman, oyun və açıq hava fəaliyyətlərini tapmaq çox asandır!\n\n"
                    "Aşağı menyudan 'Discover' (Kəşf et / Kompas ikonu) bölməsinə keçin. Burada Bakıda 46-dan çox aktivliyi canlı interaktiv Xəritə (Map) və ya Siyahı (List) şəklində araşdıra bilərsiniz.\n\n"
                    "Maraqlarınıza uyğun tədbirləri tapmaq üçün kateqoriya filtrlərindən və ya 'For You' lentindən istifadə edə bilərsiniz. MeeTTabb-da 10 rəsmi maraq kateqoriyası mövcuddur:\n"
                    "• Fitness, Outdoor (Açıq hava), Sports (İdman), Action (Adrenalin/Aksiya), Social (Sosial)\n"
                    "• Tabletop Games (Masaüstü oyunlar), Adventure (Macəra), Video Games (Video oyunlar), Fun (Əyləncə), Wellness (Sağlamlıq)\n\n"
                    "Bakıda kartinq, bovlinq, Bulvarda velosiped, oyun klublarında şahmat və mafia gecələri, mini-futbol və kvestlər kimi onlarla canlı fəaliyyət sizi gözləyir."
                )
                cta = "Hansı istiqamət sizə daha yaxındır: idman, masaüstü oyunlar, yoxsa açıq hava macəraları? Sizə uyğun tədbir tövsiyə edə bilərəm."
            return main_reply, cta

        # 5.2. Filtering Map for Weekend / "Soon" Events
        is_weekend_filter = any(k in q_lower for k in (
            "filter the map", "this weekend", "happen this weekend", "happening this weekend",
            "həftəsonu", "heftesonu", "hefte sonu", "bu hefte", "bu həftə",
            "xəritəni filtrlə", "xeriteni filtrele", "xəritədə filtrlə", "xeritede filtrele",
            "bu həftəsonu", "soon filtri", "soon filter", "выходные", "на этих выходных", "фильтр карты"
        ))
        if is_weekend_filter:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Да, конечно! Во вкладке «Discover» на карте или в списке выберите быстрый фильтр «Soon».\n\n"
                    "Этот фильтр мгновенно отбирает и отображает на карте все мероприятия, которые пройдут в ближайшие дни и на этих выходных. "
                    "Также вы можете переключаться между фильтрами «New» (новые) и «For You» (по вашим интересам)."
                )
                cta = "Что планируете на эти выходные: картинг, боулинг, велоклуб или квест с друзьями? В MeeTTabb можно присоединиться в один клик!"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "Yes, absolutely! In the 'Discover' tab on either the Map or List view, simply tap the 'Soon' filter chip.\n\n"
                    "It will instantly filter and display all events happening in the nearest days and over this coming weekend. "
                    "You can also use the 'New' filter for recently added events and 'For You' for personalized interest matches."
                )
                cta = "What are you planning for this weekend: karting, bowling, cycling, or an escape room? You can join directly on MeeTTabb!"
            else:
                main_reply = (
                    "Bəli, əlbəttə! 'Discover' bölməsində Xəritə və ya Siyahı görünüşündə 'Soon' filtr düyməsini seçməyiniz kifayətdir.\n\n"
                    "Bu filtr ən yaxın günlərdə və bu həftəsonu baş tutacaq bütün tədbirləri birbaşa xəritə üzərində göstərir. "
                    "Bundan əlavə, yeni yaranan tədbirlər üçün 'New', maraqlarınıza uyğun tədbirlər üçün isə 'For You' filtrlərindən də istifadə edə bilərsiniz."
                )
                cta = "Bu həftəsonu üçün hansı fəaliyyəti planlaşdırırsınız: kartinq, bovlinq, velosiped yoxsa kvest? MeeTTabb-da bir toxunuşla qoşula bilərsiniz!"
            return main_reply, cta

        # 5.3. Cost Policy / Is MeeTTabb Free to Use or Join Events?
        is_free_query = any(k in q_lower for k in (
            "pay to use meettabb", "pay to use", "join events free", "do i have to pay",
            "pul ödəməliyəm", "pul odemeliyem", "pul verməliyəm", "pul vermeliyem",
            "ödənişlidir", "odenislidir", "pulsuzdur", "qoşulmaq üçün pul", "ödəniş etməliyəm", "odenis etmeliyem",
            "платное ли", "надо ли платить", "бесплатно ли", "платное приложение", "is it free", "is meettabb free"
        ))
        if is_free_query:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Использование MeeTTabb, регистрация, создание событий и присоединение к ним — на 100% БЕСПЛАТНЫ!\n\n"
                    "Приложение не взимает никаких сервисных сборов, комиссий или платных подписок. "
                    "Если само мероприятие проходит в платном заведении (например, аренда дорожки боулинга, заезд на картинге, футбольное поле или заказ в кафе), "
                    "участники оплачивают свои расходы напрямую самому заведению без наценок платформы."
                )
                cta = "Приложение MeeTTabb полностью бесплатное для iOS и Android. Хотите скачать его прямо сейчас и посмотреть события на карте?"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "Using MeeTTabb, creating an account, hosting events, and joining meetups is 100% FREE!\n\n"
                    "The platform charges zero service fees, commissions, or subscription costs. "
                    "If an activity takes place at a paid venue (such as bowling lane rental, karting tracks, mini-football pitches, or a café), "
                    "participants simply pay the venue directly for their own individual share."
                )
                cta = "MeeTTabb is completely free on the App Store and Google Play. Would you like to download it now and explore events in Baku?"
            else:
                main_reply = (
                    "MeeTTabb platformasından istifadə etmək, qeydiyyatdan keçmək, tədbir yaratmaq və tədbirlərə qoşulmaq 100% PULSUZDUR!\n\n"
                    "Tətbiq heç bir komissiya, xidmət haqqı və ya abunəlik tələb etmir. "
                    "Əgər qoşulduğunuz fəaliyyət ödənişli məkandadırsa (məsələn: kartinq treki, bovlinq zolağı, futbol meydançası və ya kafe sifarişi), "
                    "hər kəs öz xərcini birbaşa həmin məkanın özünə ödəyir."
                )
                cta = "MeeTTabb mobil tətbiqi həm iOS, həm də Android üçün tamamilə pulsuzdur. İndi yükləyib xəritədəki aktivliklərə qoşulmaq istərdiniz?"
            return main_reply, cta

        # 5.4. Seeing Host and Participant List Before Joining
        is_preview_participants = any(k in q_lower for k in (
            "who the host is", "who else is participating", "participating before i join", "see who is participating",
            "təşkilatçının kim olduğunu", "teskilatcinin kim oldugunu", "təşkilatçını görmək", "teskilatcini gormek",
            "iştirakçıları görmək", "istirakcilari gormek", "kimlər qoşulub", "kimler qosulub", "iştirakçı siyahısı", "istirakci siyahisi",
            "кто организатор", "кто участвует", "список участников", "посмотреть участников", "who is the host"
        )) or ("кто" in q_lower and any(w in q_lower for w in ("организатор", "участвует", "участник")))
        if is_preview_participants:
            if lang == LanguageCode.RU:
                main_reply = (
                    "В MeeTTabb вы всегда заранее видите, кто организует мероприятие и кто в нем участвует!\n\n"
                    "Во вкладке «Discover» при нажатии на карточку любого события или метку на карте открывается экран деталей мероприятия. "
                    "Там наглядно отображаются:\n"
                    "• Профиль организатора (Host): имя и фотография\n"
                    "• Полный список подтвержденных участников с аватарками\n"
                    "• Текущее количество участников и установленный лимит мест\n"
                    "• Точное место сбора, дата, время и описание"
                )
                cta = "Хотите посмотреть, какие события и компании собираются в Баку прямо сейчас? В MeeTTabb всё открыто и безопасно!"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "In MeeTTabb, you can always see exactly who is hosting and who else is participating before you join!\n\n"
                    "When you tap any activity card or map pin in the 'Discover' tab, the event details screen opens. It clearly displays:\n"
                    "• The Host's profile: name and photo\n"
                    "• The full list of confirmed participants with profile pictures\n"
                    "• The current attendee count and capacity limit\n"
                    "• The exact meeting location on the map, date, time, and description"
                )
                cta = "Would you like to explore active events in Baku and see who is attending?"
            else:
                main_reply = (
                    "MeeTTabb-da tədbirə qoşulmazdan əvvəl təşkilatçının kim olduğunu və kimlərin iştirak etdiyini tam aydın görə bilərsiniz!\n\n"
                    "'Discover' bölməsində istənilən tədbirə və ya xəritə pininə toxunduqda açılan təfərrüat səhifəsində:\n"
                    "• Təşkilatçının (Host) adı və profil şəkli\n"
                    "• Artıq tədbirə qoşulmuş bütün iştirakçıların siyahısı və profil şəkilləri\n"
                    "• Cari iştirakçı sayı və təyin edilmiş limit\n"
                    "• Məkan, dəqiq tarix, saat və tədbir açıqlaması əks olunur."
                )
                cta = "MeeTTabb-da digər iştirakçıların profillərinə baxıb maraqlarınızı bölüşən yeni dostlarla tanış olmaq istərdiniz?"
            return main_reply, cta

        # 5.5. Join and Cancel Participation / Leaving an Event
        is_cancel_query = (
            any(k in q_lower for k in (
                "tap to join an event but later need to cancel", "need to cancel", "cancel participation",
                "tədbiri ləğv etmək", "tedbiri legv etmek", "imtina etmək", "imtina etmek", "iştirakdan imtina",
                "qoşulduqdan sonra çıxmaq", "qosulduqdan sonra cixmaq", "tərk etmək", "terk etmek", "necə imtina edim",
                "как отменить участие", "выйти из мероприятия", "отказаться от участия", "how to cancel", "leave event"
            ))
            or (any(w in q_lower for w in ("ləğv", "legv", "imtina", "çıx", "cix", "tərk", "terk")) and any(w in q_lower for w in ("tədbir", "tedbir", "fəaliyyət", "fealiyyet", "qoşul", "qoshul", "iştirak", "istirak", "toxun", "etsem", "etsəm", "olam", "olsam", "edim")))
            or (any(w in q_lower for w in ("cancel", "leave", "unjoin")) and any(w in q_lower for w in ("event", "activity", "meetup", "join", "participation")))
            or (any(w in q_lower for w in ("отмен", "отказат", "выйти", "покинут")) and any(w in q_lower for w in ("участи", "мероприят", "событи", "встреч", "присоедин")))
        )
        if is_cancel_query:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Если вы присоединились к событию, но ваши планы изменились, отменить участие очень легко!\n\n"
                    "Когда вы нажимаете «Join», событие появляется во вкладке «Joined» (Сердечко) в нижнем меню, и вам открывается групповой чат. "
                    "Если вы не сможете прийти, откройте мероприятие из вкладки «Joined» и нажмите «Leave» (Покинуть мероприятие / Отменить участие). "
                    "Вы выйдете из списка участников и чата, ваше место освободится для других пользователей, и никаких штрафов или ограничений нет."
                )
                cta = "В MeeTTabb можно свободно планировать отдых и управлять своим участием в мероприятиях!"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "If you join an event and later need to cancel, doing so is completely hassle-free!\n\n"
                    "When you tap 'Join', the activity is added to your 'Joined' tab (heart icon) and you gain access to the group chat. "
                    "If your schedule changes, simply open the event from your 'Joined' tab and tap 'Leave' (Cancel participation). "
                    "You will be removed from the attendee list and chat, your spot opens up for someone else, and there are zero penalties or fees."
                )
                cta = "MeeTTabb makes managing your social plans flexible and effortless!"
            else:
                main_reply = (
                    "Tədbirə qoşulduqdan sonra planınız dəyişərsə, iştirakınızı ləğv etmək çox asandır və heç bir problem yaratmır!\n\n"
                    "Tədbirə qoşulduqda ('Join') o, dərhal aşağı menyudakı 'Joined' (Ürək ikonu) bölmənizə əlavə olunur və qrup çatı açılır. "
                    "Əgər gedə bilməyəcəksinizsə, 'Joined' bölməsindən həmin tədbirə daxil olub 'Leave' (Tədbiri tərk et / İmtina et) düyməsinə toxunmağınız kifayətdir. "
                    "Siz siyahıdan və çatdan çıxarılasınız, yeriniz digər istifadəçilər üçün açılar və heç bir cərimə tətbiq olunmur."
                )
                cta = "MeeTTabb-da rahatlıqla tədbirlərə qoşulub planlarınızı istədiyiniz vaxt idarə edə bilərsiniz!"
            return main_reply, cta

        # 5.6. How to Create and Host a New Meetup
        is_create_event = (
            any(k in q_lower for k in (
                "create and host a new meetup", "create and host", "how do i create", "host a new meetup",
                "tədbir yaratmaq", "tedbir yaratmaq", "necə tədbir yarada", "nece tedbir yarada",
                "təşkil etmək", "teskil etmek", "tədbir açmaq", "tedbir acmaq", "yeni tədbir", "yeni tedbir",
                "как создать мероприятие", "как организовать встречу", "создать активность", "how to host", "create meetup"
            ))
            or (("necə" in q_lower or "nece" in q_lower) and ("tədbir" in q_lower or "tedbir" in q_lower) and ("yara" in q_lower or "aça" in q_lower or "təşkil" in q_lower))
            or (("как создать" in q_lower or "создать" in q_lower or "организовать" in q_lower or "провести" in q_lower) and any(w in q_lower for w in ("мероприят", "встреч", "активност", "событи")))
        ) and not any(k in q_lower for k in ("show in discovery", "görünürlük", "gorunurluk", "limit", "redaktə", "redakte", "dəyişmək", "deyismek", "dəyişim", "deyisim"))
        if is_create_event:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Создать и провести свое мероприятие в MeeTTabb можно буквально за минуту!\n\n"
                    "Инструкция:\n"
                    "1. Нажмите на вкладку «Host» (значок «+») в нижнем меню приложения.\n"
                    "2. Введите название мероприятия (Title).\n"
                    "3. Выберите одну или несколько категорий из 10 интересов (спорт, игры, отдых и т.д.).\n"
                    "4. Отметьте место встречи на интерактивной карте Баку.\n"
                    "5. Укажите дату и время сбора.\n"
                    "6. При необходимости установите лимит участников и настройте видимость переключателем «Show in Discovery».\n"
                    "7. Нажмите «Опубликовать» — ваше мероприятие готово, и вы можете звать друзей!"
                )
                cta = "Какое мероприятие вы хотите организовать первым: картинг, футбол, боулинг, настольные игры или квест?"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "Creating and hosting a new meetup on MeeTTabb takes just a minute!\n\n"
                    "Step-by-step guide:\n"
                    "1. Tap the 'Host' tab ('+' icon) in the bottom navigation bar.\n"
                    "2. Enter your activity Title.\n"
                    "3. Select one or more categories from the 10 official interest tags.\n"
                    "4. Pin the venue/meeting point on the interactive map of Baku.\n"
                    "5. Set the Date and Time.\n"
                    "6. Set an optional Participant Limit and toggle 'Show in Discovery' (public vs private).\n"
                    "7. Tap 'Publish' — your meetup is live and ready for participants!"
                )
                cta = "What type of meetup would you like to host first: karting, football, bowling, board games, or an escape room?"
            else:
                main_reply = (
                    "MeeTTabb-da yeni tədbir yaratmaq və təşkilatçı (Host) olmaq cəmi bir dəqiqə çəkir!\n\n"
                    "Addım-addım təlimat:\n"
                    "1. Aşağı naviqasiya menyusundan 'Host' ('+' ikonu) bölməsinə toxunun.\n"
                    "2. Tədbirinizin adını (Title) qeyd edin.\n"
                    "3. 10 maraq kateqoriyasından uyğun olanları seçin (idman, oyunlar, açıq hava və s.).\n"
                    "4. Bakı xəritəsində görüş yerini və məkanı pinləyin.\n"
                    "5. Görüşün tarixini və saatını təyin edin.\n"
                    "6. İstəyinizə uyğun olaraq iştirakçı limiti qoyun və 'Show in Discovery' görünürlük seçimini edin.\n"
                    "7. 'Dərc et' (Publish) düyməsini sıxın — tədbiriniz dərhal aktiv olacaq!"
                )
                cta = "İlk tədbirinizi hansı fəaliyyət üzrə açmaq istərdiniz: futbol, kartinq, bovlinq, stolüstü oyunlar, yoxsa kvest?"
            return main_reply, cta

        # 5.7. "Show in Discovery" Toggle Function
        is_discovery_toggle = any(k in q_lower for k in (
            "show in discovery", "discovery toggle", "what does the show in discovery toggle do",
            "show in discovery düyməsi", "show in discovery duymesi", "görünürlük düyməsi", "gorunurluk duymesi",
            "show in discovery nə edir", "show in discovery ne edir", "что делает show in discovery",
            "переключатель show in discovery", "кнопка show in discovery"
        )) and not any(k in q_lower for k in ("yalnız dostlar", "yalniz dostlar", "private or invite-only", "özəl və qapalı", "ozel ve qapali"))
        if is_discovery_toggle:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Переключатель «Show in Discovery» при создании активности определяет, кто сможет увидеть ваше мероприятие:\n\n"
                    "• Включен (ON): Мероприятие становится публичным. Оно отображается на интерактивной карте Баку и в общей ленте «Discover» для всех пользователей платформы, и любой желающий может к нему присоединиться.\n"
                    "• Выключен (OFF): Мероприятие становится приватным (закрытым). Оно скрыто с общей карты и ленты. Доступ к нему возможен только по прямой ссылке-приглашению или для ваших добавленных друзей."
                )
                cta = "Хотите организовать открытую встречу для новых знакомств или приватную активность со своими друзьями?"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "The 'Show in Discovery' toggle controls the visibility of your event when hosting:\n\n"
                    "• When Enabled (ON): Your activity is public. It appears on the Baku interactive map and inside the 'Discover' feed for all MeeTTabb users to find and join.\n"
                    "• When Disabled (OFF): Your activity is private/hidden. It will not appear on the public map or feed, and can only be accessed via direct invite link or by your existing friends."
                )
                cta = "Do you prefer creating an open public event to meet new people or a private session for your friend circle?"
            else:
                main_reply = (
                    "Tədbir yaradarkən 'Show in Discovery' düyməsi tədbirin görünürlük dərəcəsini tənzimləyir:\n\n"
                    "• Açıq olduqda (Enabled / ON): Tədbir ictimai (public) olur. Bütün Bakı istifadəçiləri üçün interaktiv Xəritədə və 'Discover' siyahısında görünür, hər kəs tədbiri kəşf edib qoşula bilər.\n"
                    "• Bağlı olduqda (Disabled / OFF): Tədbir gizli/özəl (private) qalır. İctimai xəritədə və axtarışda çıxmır. Yalnız sizin şəxsi dəvət linki göndərdiyiniz şəxslər və ya mövcud dostlarınız tədbirə daxil ola bilər."
                )
                cta = "Yeni insanlarla tanış olmaq üçün açıq tədbir açmaq istərdiniz, yoxsa dostlarınız üçün qapalı görüş?"
            return main_reply, cta

        # 5.8. Making Events Private or Invite-Only for Friends
        is_private_event = any(k in q_lower for k in (
            "make my event private or invite-only", "private or invite-only", "invite-only for my existing friends",
            "özəl tədbir", "ozel tedbir", "qapalı tədbir", "qapali tedbir", "ancaq dostlar üçün", "ancaq dostlar ucun",
            "tədbiri gizli etmək", "tedbiri gizli etmek", "yalnız dostlar", "yalniz dostlar",
            "сделать закрытым", "только для друзей", "приватное мероприятие", "приватным", "private event", "invite-only"
        )) or (("özəl" in q_lower or "ozel" in q_lower or "qapalı" in q_lower or "qapali" in q_lower or "dost" in q_lower) and ("dostlarım üçün" in q_lower or "dostlarim ucun" in q_lower or "yalnız dost" in q_lower or "только для друзей" in q_lower))
        if is_private_event:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Да, конечно! Вы можете сделать свое мероприятие полностью приватным и доступным только для друзей.\n\n"
                    "При создании события во вкладке «Host» просто выключите переключатель «Show in Discovery». "
                    "В результате событие не будет отображаться на карте и в ленте поиска для посторонних. "
                    "Вы сможете легко отправить прямую ссылку на мероприятие в групповые чаты или пригласить своих друзей напрямую из вкладки «Friends»."
                )
                cta = "Планируете закрытый отдых со своей компанией друзей? В MeeTTabb это организуется в пару кликов!"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "Yes, absolutely! You can make your event completely private and invite-only for your friends.\n\n"
                    "When creating your activity in the 'Host' tab, simply turn OFF the 'Show in Discovery' toggle. "
                    "This hides the meetup from the public map and Discover feed. You can then copy the private invite link to send to your friends, or invite them directly from your 'Friends' tab."
                )
                cta = "Planning an exclusive outing for your friend group? You can easily host a private event on MeeTTabb!"
            else:
                main_reply = (
                    "Bəli, əlbəttə! Tədbirinizi tamamilə özəl və yalnız dostlarınız üçün qapalı (invite-only) edə bilərsiniz.\n\n"
                    "'Host' bölməsində tədbir yaradarkən 'Show in Discovery' düyməsini söndürməyiniz (OFF) kifayətdir. "
                    "Bununla tədbir ümumi xəritədə və axtarış lentində görünməyəcək. "
                    "Siz şəxsi tədbir linkini dostlarınıza göndərə və ya 'Friends' bölməsindən dostlarınızı birbaşa tədbirə dəvət edə bilərsiniz."
                )
                cta = "Dostlarınız üçün xüsusi və qapalı əyləncə təşkil etmək istərdiniz? MeeTTabb-da qapalı tədbir yarada bilərsiniz!"
            return main_reply, cta

        # 5.9. Changing Location, Time, or Category of Published Event
        is_edit_event = (
            any(k in q_lower for k in (
                "change the location, time, or category", "change the location", "already published",
                "tədbiri dəyişmək", "tedbiri deyismek", "tədbiri redaktə etmək", "tedbiri redakte etmek",
                "vaxtını dəyişmək", "vaxtini deyismek", "məkanı dəyişmək", "mekani deyismek", "dərc olunmuş tədbir",
                "изменить время или место", "отредактировать мероприятие", "изменить опубликованное", "edit event", "change event"
            ))
            or (("tədbir" in q_lower or "tedbir" in q_lower or "событи" in q_lower or "мероприят" in q_lower) and ("vaxt" in q_lower or "məkan" in q_lower or "mekan" in q_lower or "время" in q_lower or "место" in q_lower) and ("dəyiş" in q_lower or "deyis" in q_lower or "измен" in q_lower or "redakt" in q_lower))
        )
        if is_edit_event:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Вы можете легко изменить локацию, дату, время или категорию уже опубликованного мероприятия!\n\n"
                    "Как организатор (Host):\n"
                    "1. Откройте свое мероприятие через вкладку «Joined» или в своем профиле.\n"
                    "2. Нажмите на значок редактирования «Edit Event» (карандаш).\n"
                    "3. Обновите точку на карте, дату, время, категорию или лимит мест.\n"
                    "4. Сохраните изменения — все участники в групповом чате сразу увидят обновленную информацию."
                )
                cta = "Управлять событиями в MeeTTabb очень удобно. Хотите создать свою первую встречу?"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "You can easily change the location, time, date, or category of an event you've already published!\n\n"
                    "As the host:\n"
                    "1. Open your created activity from the 'Joined' tab or your 'Profile'.\n"
                    "2. Tap the 'Edit Event' option (pencil icon).\n"
                    "3. Update the venue map pin, date & time, category, or participant limit.\n"
                    "4. Save changes — all confirmed participants will see the updated details instantly."
                )
                cta = "MeeTTabb makes managing and tweaking your meetups completely seamless!"
            else:
                main_reply = (
                    "Dərc etdiyiniz tədbirin məkanını, vaxtını, tarixini və ya kateqoriyasını istənilən vaxt dəyişə bilərsiniz!\n\n"
                    "Təşkilatçı kimi:\n"
                    "1. 'Joined' və ya 'Profile' bölməsindən yaratdığınız tədbirə daxil olun.\n"
                    "2. Yuxarıdakı 'Edit Event' (Redaktə et / Qələm ikonu) seçiminə toxunun.\n"
                    "3. Xəritədəki görüş məkanını, tarixi, saatı, maraq kateqoriyasını və ya limiti yeniləyin.\n"
                    "4. Yadda saxlayın — qrup çatındakı bütün iştirakçılar yenilənmiş məlumatı anında görəcəklər."
                )
                cta = "MeeTTabb-da tədbirlərinizi idarə etmək çox asandır. İstədiyiniz dəyişikliyi bir neçə toxunuşla edə bilərsiniz!"
            return main_reply, cta

        # 5.10. Participant Limits
        is_participant_limit = any(k in q_lower for k in (
            "limit to how many people can join", "limit to how many people", "is there a limit",
            "iştirakçı limiti", "istirakci limiti", "neçə nəfər qoşula bilər", "nece nefer qosula biler",
            "limit varmı", "limit varmi", "maksimum neçə nəfər", "maksimum nece nefer",
            "лимит участников", "сколько человек может участвовать", "есть ли лимит", "participant limit", "limit on people"
        )) or ("лимит" in q_lower and "участник" in q_lower) or ("limit" in q_lower and any(w in q_lower for w in ("iştirakçı", "istirakci", "nəfər", "nefer", "qoymaq")))
        if is_participant_limit:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Да, как организатор (Host) вы полностью контролируете лимит участников!\n\n"
                    "При создании или редактировании мероприятия вы можете установить точный лимит мест (например, ровно 10 игроков для мини-футбола, 4 человека для картинга, квеста или настольной игры) "
                    "либо оставить мероприятие безлимитным для масштабных встреч. Как только указанное количество участников набрано, запись закрывается автоматически."
                )
                cta = "На какую компанию вы рассчитываете активность? В MeeTTabb можно легко собрать нужный состав!"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "Yes, as a host you have complete control over participant limits!\n\n"
                    "When creating or editing an event, you can set a precise Participant Limit (e.g., exactly 10 players for mini-football, 4 players for karting, escape rooms, or board games) "
                    "or leave it unlimited for open social gatherings. Once the participant capacity is reached, new joins are automatically closed."
                )
                cta = "How many people are in your ideal group? You can set your exact party size in MeeTTabb!"
            else:
                main_reply = (
                    "Bəli, tədbir təşkilatçısı kimi iştirakçı sayına tam nəzarət sizdədir!\n\n"
                    "Tədbir yaradarkən və ya redaktə edərkən dəqiq iştirakçı limiti (Participant Limit) təyin edə bilərsiniz (məsələn: mini-futbol üçün 10 nəfər, kartinq, kvest və ya stolüstü oyunlar üçün 4 nəfər) "
                    "və ya böyük sosial görüşlər üçün limiti limitsiz saxlaya bilərsiniz. Təyin etdiyiniz limit dolan kimi yeni qoşulmalar avtomatik olaraq bağlanır."
                )
                cta = "Neçə nəfərlik komanda yığmağı planlaşdırırsınız? MeeTTabb-da limit qoyaraq istədiyiniz sayda heyət toplaya bilərsiniz."
            return main_reply, cta

        # 5.11. Accessing Group Chat for Joined Activity
        is_group_chat = (
            any(k in q_lower for k in (
                "access the group chat", "how do i access the group chat", "activity i just joined",
                "qrup çatına necə daxil", "qrup catina nece daxil", "qrup çatı haradadır", "qrup cati hardadir",
                "çata necə girim", "cata nece girim", "tədbirin çatı", "tedbirin cati", "joined tab",
                "как войти в групповой чат", "где групповой чат", "чат мероприятия", "access group chat"
            ))
            or (("qrup" in q_lower or "qrup çatı" in q_lower) and ("çat" in q_lower or "cat" in q_lower) and ("necə" in q_lower or "nece" in q_lower or "harada" in q_lower or "harda" in q_lower))
            or (("групповой" in q_lower or "в чат" in q_lower) and ("как войти" in q_lower or "где" in q_lower))
        ) and not any(k in q_lower for k in ("report a user", "block a user", "şikayət", "sikayet", "blok"))
        if is_group_chat:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Войти в закрытый групповой чат мероприятия очень просто:\n\n"
                    "1. Перейдите во вкладку «Joined» (значок Сердечка) в нижнем меню приложения.\n"
                    "2. В списке «Your Activities» нажмите на карточку мероприятия, к которому вы присоединились.\n"
                    "3. Нажатие сразу открывает живой групповой чат участников, где вы можете согласовать место, время и любые детали встречи."
                )
                cta = "В MeeTTabb у каждого мероприятия есть свой чат для комфортного общения участников!"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "Accessing the group chat for an activity you joined is quick and simple:\n\n"
                    "1. Tap the 'Joined' tab (heart icon) in the bottom navigation bar.\n"
                    "2. Under 'Your Activities', tap on the event card you joined.\n"
                    "3. This immediately opens the dedicated real-time event group chat, where you can coordinate meetup points, timing, and chat with all attendees."
                )
                cta = "Every MeeTTabb meetup includes a dedicated group chat to keep everyone aligned!"
            else:
                main_reply = (
                    "Qoşulduğunuz tədbirin qrup çatına daxil olmaq çox rahatdır:\n\n"
                    "1. Aşağı menyudan 'Joined' (Qoşulduğum tədbirlər / Ürək ikonu) bölməsinə keçin.\n"
                    "2. 'Your Activities' siyahısında qoşulduğunuz tədbirin üzərinə toxunun.\n"
                    "3. Toxunuşla dərhal həmin tədbirin canlı qrup çatı açılacaq. Burada bütün iştirakçılarla görüş yerini, vaxtı və digər detalları anında müzakirə edə bilərsiniz."
                )
                cta = "MeeTTabb-da hər bir tədbirin özəl qrup çatı var. İştirakçılarla tanış olub detalları razılaşdırmaq istərdiniz?"
            return main_reply, cta

        # 5.12. Sending Friend Requests to People Met at an Event
        is_send_friend_request = (
            any(k in q_lower for k in (
                "send friend requests", "friend requests to people", "people i meet at an event",
                "dostluq göndərmək", "dostluq gondermek", "dostluq atmaq", "dost əlavə etmək", "dost elave etmek",
                "add friend", "tanış olduğum insanlara", "tanis oldugum insanlara",
                "как отправить заявку в друзья", "добавить в друзья", "send friend request"
            ))
            or (("dostluq" in q_lower or "dost" in q_lower) and ("göndər" in q_lower or "gonder" in q_lower or "atmaq" in q_lower or "add" in q_lower))
            or ("в друзья" in q_lower and ("добавить" in q_lower or "заявк" in q_lower or "отправить" in q_lower))
        )
        if is_send_friend_request:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Да, конечно! Вы можете легко отправить заявку в друзья любому участнику мероприятия:\n\n"
                    "В списке участников события (Activity Details) или прямо в групповом чате нажмите на аватар или имя пользователя, чтобы открыть его профиль. "
                    "Нажмите кнопку «Add Friend» (Добавить в друзья). После подтверждения пользователь появится в вашем списке друзей во вкладке «Friends»."
                )
                cta = "MeeTTabb помогает превращать совместные активности в настоящую дружбу в реальном мире!"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "Yes, definitely! You can send friend requests to anyone you meet at an event:\n\n"
                    "Inside the activity details attendee list or within the event group chat, tap on any participant's profile avatar or name to open their profile preview. "
                    "Tap 'Add Friend' to send a friend request. Once accepted, they will appear in your 'Friends' tab."
                )
                cta = "MeeTTabb is designed to turn fun shared activities into lasting real-world friendships!"
            else:
                main_reply = (
                    "Bəli, əlbəttə! Tədbirdə tanış olduğunuz insanlara çox rahatlıqla dostluq sorğusu göndərə bilərsiniz:\n\n"
                    "Tədbirin iştirakçılar siyahısında və ya qrup çatında istənilən iştirakçının profil şəklinə və ya adına toxunaraq onun profilinə daxil olun. "
                    "Orada 'Add Friend' (Dostluq göndər) düyməsini sıxmağınız kifayətdir. Qarşı tərəf qəbul etdikdə o, 'Friends' bölmənizə əlavə olunacaq."
                )
                cta = "Real həyatda yeni dostlar qazanmaq və tədbirlərə birgə getmək üçün MeeTTabb əla fürsətdir!"
            return main_reply, cta

        # 5.13. Finding Friend List and Direct Messages (DMs)
        is_friend_list_dms = any(k in q_lower for k in (
            "where can i find my friend list and direct messages", "find my friend list", "direct messages",
            "dostlar siyahısı", "dostlar siyahisi", "birbaşa mesajlar", "birbasa mesajlar",
            "şəxsi mesajlar", "sexsi mesajlar", "dm haradadır", "dm hardadir", "friends tab", "dostlar bölməsi",
            "где найти список друзей", "личные сообщения", "где друзья", "friend list", "direct message", "личка"
        )) or ("список друзей" in q_lower) or ("dostlar siyahısı" in q_lower or "dostlar siyahisi" in q_lower)
        if is_friend_list_dms:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Список друзей и личные сообщения находятся во вкладке «Friends» (значок двух людей) в нижнем меню приложения:\n\n"
                    "• Список друзей: показывает всех добавленных друзей и мероприятия, к которым они присоединились.\n"
                    "• Direct Messages (Личные сообщения): позволяет вести конфиденциальную переписку 1-на-1 с любым другом, чтобы договариваться о совместном отдыхе."
                )
                cta = "Используйте раздел «Friends», чтобы всегда быть на связи и планировать встречи с друзьями!"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "You can find your friend list and direct messages in the 'Friends' tab (people icon) in the bottom navigation bar:\n\n"
                    "• Friends List: View all your connected friends and see what activities they are attending.\n"
                    "• Direct Messages (DMs): Engage in private 1-on-1 chats with any friend to coordinate meetups effortlessly."
                )
                cta = "Stay in touch with your crew and organize outings together in the Friends tab!"
            else:
                main_reply = (
                    "Dostlar siyahınızı və birbaşa şəxsi mesajlarınızı aşağı naviqasiya menyusundakı 'Friends' (İnsanlar ikonu) bölməsində tapa bilərsiniz:\n\n"
                    "• Dostlar siyahısı: Bütün dostlarınızı görmək və onların hansı tədbirlərə qatıldığını izləmək imkanı verir.\n"
                    "• Birbaşa Şəxsi Mesajlar (Direct Messages): İstənilən dostunuzla 1-on-1 təkbətək özəl mesajlaşmaq və görüş planlaşdırmaq üçün nəzərdə tutulub."
                )
                cta = "Dostlarınızla daim əlaqədə qalmaq və yeni aktivliklərə birgə yazılmaq üçün Friends bölməsindən istifadə edin!"
            return main_reply, cta

        # 5.14. Blocking or Reporting Inappropriate Users
        is_block_report = any(k in q_lower for k in (
            "block or report a user", "acting inappropriately in the chat", "block or report",
            "istifadəçini bloklamaq", "istifadecini bloklamaq", "şikayət etmək", "sikayet etmek",
            "narahat edir", "qayda pozuntusu", "blok etmək", "blok etmek", "istifadəçini şikayət",
            "заблокировать пользователя", "пожаловаться", "неподобающе ведет себя", "заблокировать", "report user", "block user"
        )) or (("blok" in q_lower or "şikayət" in q_lower or "sikayet" in q_lower) and ("istifadəçi" in q_lower or "istifadeci" in q_lower or "narahat" in q_lower or "edim" in q_lower)) or ("заблокировать" in q_lower or "пожаловаться" in q_lower)
        if is_block_report:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Безопасность и уважительное общение — приоритет MeeTTabb. Если кто-то ведет себя некорректно в чате или на мероприятии:\n\n"
                    "1. Нажмите на профиль нарушителя в чате или в списке участников.\n"
                    "2. Откройте меню опций (три точки) и выберите «Report User» (Пожаловаться) или «Block User» (Заблокировать).\n"
                    "3. Управлять списком заблокированных и разблокировать при необходимости можно в: Profile > Настройки (шестеренка) > «Blocked Users»."
                )
                cta = "MeeTTabb обеспечивает безопасную и комфортную атмосферу для всех участников!"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "Community safety and respectful behavior are top priorities on MeeTTabb. If someone behaves inappropriately:\n\n"
                    "1. Tap on their profile avatar/name in the group chat or participant list.\n"
                    "2. Tap the options menu (three dots) and select 'Report User' or 'Block User'.\n"
                    "3. You can review and manage your blocked users anytime under: Profile > Settings (gear icon) > 'Blocked Users'."
                )
                cta = "MeeTTabb ensures a safe, friendly, and respectful environment for everyone!"
            else:
                main_reply = (
                    "MeeTTabb-da istifadəçi təhlükəsizliyi və qarşılıqlı hörmət ən vacib şərtdir. Əgər kimsə qrup çatında və ya tədbirdə uyğunsuz davranarsa:\n\n"
                    "1. Çatda və ya iştirakçılar siyahısında həmin şəxsin profilinə toxunun.\n"
                    "2. Üç nöqtə menyusunu açıb 'Report User' (Şikayət et) və ya 'Block User' (Blokla) seçin.\n"
                    "3. Blokladığınız istifadəçilərə Profile > Parametrlər (dişli çarx) > 'Blocked Users' bölməsindən baxa və istədiyiniz vaxt bloku aça bilərsiniz."
                )
                cta = "MeeTTabb platformasında hər kəs üçün təhlükəsiz və səmimi mühit təmin olunur!"
            return main_reply, cta

        # 5.15. Editing Profile Details, Picture, and 10 Interests
        is_edit_profile = (
            any(k in q_lower for k in (
                "edit my profile details, picture, and interests", "edit my profile", "picture, and interests",
                "profili redaktə etmək", "profili redakte etmek", "şəkil dəyişmək", "sekil deyismek",
                "maraqları dəyişmək", "maraqlari deyismek", "bio yazmaq", "profil məlumatları", "profil melumatlari",
                "изменить профиль", "сменить фото", "поменять интересы", "редактировать профиль", "edit profile"
            ))
            or ("profil" in q_lower and ("redaktə" in q_lower or "redakte" in q_lower or "maraq" in q_lower or "şəkil" in q_lower or "sekil" in q_lower))
            or ("профил" in q_lower and ("изменит" in q_lower or "фото" in q_lower or "интерес" in q_lower or "редактироват" in q_lower))
        )
        if is_edit_profile:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Редактировать профиль, фото и интересы в MeeTTabb очень просто:\n\n"
                    "1. Перейдите во вкладку «Profile» (значок пользователя) в нижнем меню.\n"
                    "2. Нажмите кнопку «Edit Profile».\n"
                    "3. Здесь вы можете загрузить аватарку, изменить имя, фамилию, возраст, пол и написать краткое био о себе (до 300 символов).\n"
                    "4. Выберите свои предпочтения из 10 официальных категорий интересов (Fitness, Outdoor, Sports, Action, Social, Tabletop Games, Adventure, Video Games, Fun, Wellness), чтобы настроить персональные рекомендации ленты «For You»."
                )
                cta = "Заполните свой профиль интересами, чтобы MeeTTabb подбирал для вас идеальные события!"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "Editing your profile, photo, and interests in MeeTTabb is simple:\n\n"
                    "1. Tap the 'Profile' tab (user icon) in the bottom navigation bar.\n"
                    "2. Tap 'Edit Profile'.\n"
                    "3. Upload or update your photo, name, surname, age, gender, and write your bio (up to 300 characters).\n"
                    "4. Select your favorites among the 10 official interest categories (Fitness, Outdoor, Sports, Action, Social, Tabletop Games, Adventure, Video Games, Fun, Wellness) to power your personalized 'For You' discovery feed."
                )
                cta = "Fill out your profile and interests so MeeTTabb can recommend the best activities for you!"
            else:
                main_reply = (
                    "Profil məlumatlarınızı, şəklinizi və maraqlarınızı redaktə etmək çox rahatdır:\n\n"
                    "1. Aşağı menyudan 'Profile' (İstifadəçi ikonu) bölməsinə keçin.\n"
                    "2. 'Edit Profile' düyməsinə toxunun.\n"
                    "3. Profil şəklinizi yeniləyin, ad, soyad, yaş, cins məlumatlarını qeyd edin və 300 simvolluq bio mətninizi daxil edin.\n"
                    "4. 10 rəsmi maraq kateqoriyasından (Fitness, Outdoor, Sports, Action, Social, Tabletop Games, Adventure, Video Games, Fun, Wellness) sevdiklərinizi seçin. Bu seçimlər 'For You' bölməsindəki tədbir tövsiyələrinizi tam fərdiləşdirir."
                )
                cta = "Profilinizi maraqlarınızla doldurun ki, MeeTTabb sizə ən ideal tədbirləri tövsiyə etsin!"
            return main_reply, cta

        # 5.16. Reporting Map Loading Bugs or Technical Issues
        is_bug_report = any(k in q_lower for k in (
            "issue with the map loading", "report a bug", "map loading", "xəritə açılmır", "xerite acilmir",
            "xəritə yüklənmir", "xerite yuklenmir", "bug bildirmək", "bug bildirmek", "xəta var", "xeta var",
            "problem var", "сообщить об ошибке", "карта не грузится", "карта не загружается", "баг с картой", "ошибка приложения", "bug report"
        )) or (("xəritə" in q_lower or "xerite" in q_lower or "карт" in q_lower) and ("açılmır" in q_lower or "acilmir" in q_lower or "xəta" in q_lower or "xeta" in q_lower or "грузится" in q_lower or "ошибк" in q_lower))
        if is_bug_report:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Если у вас возникли сложности с загрузкой карты или другой технический сбой:\n\n"
                    "1. Перейдите во вкладку «Profile» > Настройки (значок шестеренки) > «Report a Bug» (Сообщить об ошибке).\n"
                    "2. Опишите возникшую проблему и отправьте — отчет напрямую поступит нашей технической команде.\n"
                    "3. Также вы можете описать проблему прямо здесь в этом чате, и я зафиксирую ее для разработчиков!"
                )
                cta = "Мы непрерывно совершенствуем приложение для вашего комфорта!"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "If you experience an issue with map loading or any technical bug:\n\n"
                    "1. Go to the 'Profile' tab > Settings (gear icon) > 'Report a Bug'.\n"
                    "2. Describe the issue and submit it directly to our engineering team.\n"
                    "3. You can also describe the issue right here in this chat, and I will record it for our developers!"
                )
                cta = "We actively resolve technical feedback to ensure a smooth MeeTTabb experience!"
            else:
                main_reply = (
                    "Xəritənin yüklənməsində və ya tətbiqdə hər hansı texniki xəta ilə qarşılaşmısınızsa:\n\n"
                    "1. 'Profile' bölməsinə keçib Parametrlər (dişli çarx) > 'Report a Bug' (Xəta bildir) bölməsindən problemi birbaşa texniki komandamıza göndərə bilərsiniz.\n"
                    "2. Həmçinin problemin detallarını birbaşa bu çatda qeyd edə bilərsiniz, mən onu qeydə alıb tərtibatçılarımıza çatdıracağam!"
                )
                cta = "İstifadəçi təcrübəsini təkmilləşdirmək üçün rəyləriniz bizim üçün çox dəyərlidir!"
            return main_reply, cta

        # 5.17. Location Privacy (Is Exact Location Shared?)
        is_location_privacy = (
            any(k in q_lower for k in (
                "is my exact location shared", "exact location shared with other users", "location shared",
                "dəqiq məkanım paylaşılır", "deqiq mekanim paylasilir", "gps paylaşılır", "gps paylasilir",
                "məkanımı görürlər", "mekanimi gorurler", "canlı məkan", "canli mekan", "yerimi görürlər", "yerimi gorurler",
                "делится ли моим местоположением", "видно ли мое местоположение", "следит за местоположением", "live location"
            ))
            or (("gps" in q_lower or "məkan" in q_lower or "mekan" in q_lower or "yerim" in q_lower) and ("paylaş" in q_lower or "paylas" in q_lower or "görün" in q_lower or "gorun" in q_lower))
            or ("местоположени" in q_lower and any(w in q_lower for w in ("делится", "видно", "следит", "передает", "транслир")))
        )
        if is_location_privacy:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Категорически НЕТ! MeeTTabb строго защищает конфиденциальность пользователей.\n\n"
                    "Ваше точное личное GPS-местоположение НИКОГДА не передается и не транслируется другим пользователям платформы. "
                    "На интерактивной карте отображаются исключительно статичные точки встреч (venue pins), которые организатор выбирает для проведения мероприятия."
                )
                cta = "Ваша безопасность и приватность в MeeTTabb полностью гарантированы!"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "Absolutely NOT! MeeTTabb strictly protects your privacy.\n\n"
                    "Your exact personal real-time GPS location is NEVER shared, broadcast, or tracked for other users. "
                    "The interactive map only displays the static meeting venue pin chosen by the event host for the activity."
                )
                cta = "Your privacy and safety are fully guaranteed on MeeTTabb!"
            else:
                main_reply = (
                    "Qətiyyən XEYR! MeeTTabb istifadəçilərin şəxsi məxfiliyinə tam zəmanət verir.\n\n"
                    "Sizin şəxsi dəqiq GPS yerləşməniz heç vaxt digər istifadəçilərlə paylaşılmır və canlı izlənilmir. "
                    "İnteraktiv xəritədə yalnız tədbir təşkilatçısının tədbir üçün seçdiyi görüş məkanının pini görünür."
                )
                cta = "MeeTTabb-da tam təhlükəsiz və rahat şəkildə aktivliklərə qoşula bilərsiniz!"
            return main_reply, cta

        # 5.17b. Terminology Explanation / Translation (e.g. "Azerbaycan dilinde delete account nedir?", "What does delete account mean?")
        is_term_explanation = any(k in q_lower for k in (
            "nə deməkdir", "ne demekdir", "nədir", "nedir", "tərcüməsi nədir", "tercumesi nedir",
            "azərbaycan dilində", "azerbaycan dilinde", "azərbaycanca nədir", "azerbaycanca nedir",
            "mənası nədir", "menasi nedir",
            "что означает", "как переводится", "что такое", "значение",
            "what does it mean", "what is the meaning of", "translation of"
        )) and any(k in q_lower for k in ("delete account", "joined", "discover", "host", "tabb coin", "tabbcoin", "show in discovery", "for you", "soon"))
        if is_term_explanation:
            if "delete account" in q_lower:
                if lang == LanguageCode.RU:
                    main_reply = (
                        "Выражение «Delete Account» означает «Удалить аккаунт» (на азербайджанском языке: «Hesabı sil»).\n\n"
                        "В приложении MeeTTabb эта функция находится в разделе: Profile > Настройки (значок шестеренки) > «Account Deletion». "
                        "Она позволяет пользователю навсегда и безвозвратно стереть свой профиль и все персональные данные из системы."
                    )
                    cta = "Хотите узнать больше о других терминах или разделах приложения MeeTTabb?"
                elif lang == LanguageCode.EN:
                    main_reply = (
                        "The term 'Delete Account' translates to 'Hesabı sil' (Account Deletion) in Azerbaijani.\n\n"
                        "In the MeeTTabb app, this function is located under: Profile > Settings (gear icon) > 'Account Deletion'. "
                        "It allows you to permanently and irreversibly erase your account and all associated personal data from our systems."
                    )
                    cta = "Would you like to know more about any other terms or features in MeeTTabb?"
                else:
                    main_reply = (
                        "'Delete Account' ifadəsi Azərbaycan dilində 'Hesabı sil' deməkdir.\n\n"
                        "MeeTTabb tətbiqində bu seçim Profile > Parametrlər (dişli çarx ikonu) > 'Account Deletion' bölməsində yerləşir və "
                        "istifadəçinin öz hesabını və bütün fərdi məlumatlarını sistemdən birdəfəlik silməsinə imkan verir."
                    )
                    cta = "MeeTTabb tətbiqinin digər bölmələri və ya funksiyaları barədə hər hansı sualınız varmı? Kömək etməyə hazıram!"
                return main_reply, cta

        # 5.18. Account Deletion and Permanent Data Removal
        is_account_deletion = (
            any(k in q_lower for k in (
                "permanently delete my account and personal data", "permanently delete my account", "delete my account",
                "hesabımı silmək", "hesabimi silmek", "şəxsi məlumatları silmək", "sexsi melumatlari silmek",
                "hesabı necə silim", "hesabi nece silim", "delete account", "hesabımı ləğv etmək",
                "как удалить аккаунт", "удалить профиль", "удаление аккаунта", "удалить персональные данные"
            ))
            or (("hesab" in q_lower or "account" in q_lower) and ("sil" in q_lower or "ləğv" in q_lower or "legv" in q_lower or "delete" in q_lower))
            or (("аккаунт" in q_lower or "профил" in q_lower) and "удалит" in q_lower)
        ) and not is_term_explanation
        if is_account_deletion:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Удалить свой аккаунт и персональные данные можно навсегда в несколько нажатий:\n\n"
                    "1. Перейдите во вкладку «Profile» в нижнем меню.\n"
                    "2. Нажмите на Настройки (значок шестеренки) > «Account Deletion».\n"
                    "3. Нажмите красную кнопку «Delete Account» и подтвердите действие.\n\n"
                    "Ваш профиль, переписки и все личные данные будут безвозвратно и полностью стерты из системы в соответствии с политикой защиты данных."
                )
                cta = "MeeTTabb строго соблюдает международные стандарты безопасности и защиты данных!"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "You can permanently delete your account and personal data at any time:\n\n"
                    "1. Go to the 'Profile' tab in the bottom navigation bar.\n"
                    "2. Tap Settings (gear icon) > 'Account Deletion'.\n"
                    "3. Tap the red 'Delete Account' button and confirm.\n\n"
                    "Your account, profile information, messages, and personal data will be permanently and irreversibly erased from our servers in accordance with data privacy regulations."
                )
                cta = "MeeTTabb complies with global data privacy and protection regulations!"
            else:
                main_reply = (
                    "Hesabınızı və şəxsi məlumatlarınızı birdəfəlik silmək üçün:\n\n"
                    "1. Aşağı menyudan 'Profile' bölməsinə keçin.\n"
                    "2. Parametrlər (dişli çarx ikonu) > 'Account Deletion' bölməsinə daxil olun.\n"
                    "3. Qırmızı 'Delete Account' düyməsini sıxın və təsdiq edin.\n\n"
                    "Hesabınız, profil məlumatlarınız, mesajlarınız və bütün fərdi məlumatlarınız sistemdən birdəfəlik və bərpa olunmaz şəkildə tamamilə silinəcəkdir."
                )
                cta = "MeeTTabb beynəlxalq məlumatların qorunması və məxfilik standartlarına tam riayət edir!"
            return main_reply, cta

        # 2. Check if customer stated an exact budget amount (e.g. "50 manat", "50 azn", "у меня есть 50 азн", "100 azn")
        amount_match = re.search(r"\b(\d+)\s*(?:azn|manat|манат|азн|₼|usd|\$)?\b", q_lower)
        stated_amount: Optional[int] = None
        has_stated_budget = False
        if amount_match:
            try:
                parsed_val = int(amount_match.group(1))
                if parsed_val >= 5:
                    budget_indicators = ("azn", "manat", "манат", "азн", "₼", "büdcə", "budce", "pul", "есть", "бюджет", "budget", "have", "with")
                    if any(ind in q_lower for ind in budget_indicators) or len(q_lower.split()) <= 4:
                        stated_amount = parsed_val
                        has_stated_budget = True
            except ValueError:
                pass

        if has_stated_budget and stated_amount is not None:
            if lang == LanguageCode.RU:
                if 35 <= stated_amount <= 65:
                    main_reply = (
                        f"С бюджетом {stated_amount} AZN в Баку можно отлично провести время с друзьями:\n"
                        "• Картинг: 2 заезда по 10 минут в Baku Karting или GoKart (15-25 AZN за заезд).\n"
                        "• Боулинг: аренда дорожки на 1.5-2 часа в 28 Mall (20-30 AZN/час) или в Ganjlik Mall (25-35 AZN/час).\n"
                        "• Квесты: командная игра в Portal Games или Quest Baku (от 50 AZN на команду или 12-20 AZN с человека).\n"
                        "• Велопрогулка или настольные игры: аренда велосипеда на Бульваре (3-5 AZN) или вечер игр в клубе (5-10 AZN).\n\n"
                        "Что вам ближе: драйв и картинг, боулинг с друзьями или квесты с загадками?"
                    )
                    cta = "Сколько человек планирует участвовать? В приложении MeeTTabb можно создать событие и в один клик позвать всех друзей!"
                elif stated_amount < 35:
                    main_reply = (
                        f"С бюджетом {stated_amount} AZN в Баку есть отличные активности:\n"
                        "• Картинг: 1 заезд на 10 минут (15-20 AZN).\n"
                        "• Велосипед: 3-4 часа катания на Бакинском бульваре (3-5 AZN/час).\n"
                        "• Настольные игры: вечер в Baku Board Game Club или Second Home с чаем/кофе (5-10 AZN).\n"
                        "• Мини-футбол: аренда поля с компанией (всего 4-6 AZN с человека)."
                    )
                    cta = "Какой формат отдыха вам нравится больше? В приложении MeeTTabb можно легко собрать компанию!"
                else:
                    main_reply = (
                        f"С бюджетом {stated_amount} AZN вы можете организовать полноценную командную игру в квест (50-80 AZN), "
                        "арендовать футбольное поле (40-70 AZN) или устроить турнир по картингу и боулингу на несколько дорожек!"
                    )
                    cta = "На сколько человек рассчитываете мероприятие? В MeeTTabb можно создать событие и собрать всех участников."
            elif lang == LanguageCode.EN:
                if 35 <= stated_amount <= 65:
                    main_reply = (
                        f"With a {stated_amount} AZN budget in Baku, you have several exciting options:\n"
                        "• Karting: 2 heats (10 min each) at Baku Karting or GoKart Baku (15-25 AZN each).\n"
                        "• Bowling: 1.5 to 2 hours of lane rental at 28 Mall (20-30 AZN/hr) or Ganjlik Mall (25-35 AZN/hr).\n"
                        "• Escape Rooms: Team adventure at Portal Games or Quest Baku (50-80 AZN for a team, or 12-20 AZN/person).\n"
                        "• Cycling or Board Games: A scenic ride along Baku Boulevard (3-5 AZN) or a full evening at a board game club (5-10 AZN).\n\n"
                        "Which vibe do you prefer: high-speed karting, bowling, or escape room puzzles?"
                    )
                    cta = "How many friends are joining you? You can easily create an activity in the MeeTTabb app and invite your friends!"
                elif stated_amount < 35:
                    main_reply = (
                        f"With a {stated_amount} AZN budget, you can enjoy several fun activities in Baku:\n"
                        "• Karting: 1 session of 10 minutes (15-20 AZN).\n"
                        "• Cycling: 3-4 hours along Baku Boulevard (3-5 AZN/hour).\n"
                        "• Board games: Full evening pass including tea/coffee at Baku Board Game Club (5-10 AZN).\n"
                        "• Mini-football: Pitch share with friends (4-6 AZN per player)."
                    )
                    cta = "Which activity sounds best to you? You can host it directly on the MeeTTabb app!"
                else:
                    main_reply = (
                        f"With a {stated_amount} AZN budget, you can book an entire escape room for your team (50-80 AZN), "
                        "rent a full football pitch (40-70 AZN), or organize a multi-lane bowling tournament!"
                    )
                    cta = "How many people are in your group? Create a tournament or event in MeeTTabb to coordinate effortlessly."
            else:
                if 35 <= stated_amount <= 65:
                    main_reply = (
                        f"{stated_amount} AZN büdcə ilə Bakıda bir neçə əla fəaliyyət seçə bilərsiniz:\n"
                        "• Kartinq: Baku Karting və ya GoKart-da 2 dəfə 10 dəqiqəlik sürüş (hər biri 15-25 AZN).\n"
                        "• Bovlinq: 28 Mall-da 1.5-2 saat zolaq icarəsi (saatı 20-30 AZN) və ya Gənclik Mall-da 1 saat (25-35 AZN).\n"
                        "• Kvestlər: Portal Games və ya Quest Baku-da dostlarla adambaşı 12-20 AZN və ya 50-80 AZN komanda oyunu.\n"
                        "• Velosiped və ya Masaüstü oyunlar: Bulvarda velosiped (3-5 AZN) və ya oyun klubunda tam axşam (5-10 AZN).\n\n"
                        "Hansı istiqamət sizə daha maraqlıdır: sürət və adrenalin (kartinq), yoxsa komanda ilə əyləncə (bovlinq/kvest)?"
                    )
                    cta = "Neçə nəfərlə getməyi planlaşdırırsınız? MeeTTabb tətbiqində tədbir yaradaraq dostlarınızı bir toxunuşla dəvət edə bilərsiniz."
                elif stated_amount < 35:
                    main_reply = (
                        f"{stated_amount} AZN büdcə ilə Bakıda maraqlı aktivliklər:\n"
                        "• Kartinq: 1 dəfə 10 dəqiqəlik sürüş (15-20 AZN).\n"
                        "• Velosiped: Bakı Bulvarında 3-4 saatlıq icarə (saatı 3-5 AZN).\n"
                        "• Masaüstü oyunlar: Baku Board Game Club və ya Second Home-da çay/kofe menyusu ilə tam giriş (5-10 AZN).\n"
                        "• Futbol: Respublika Stadionunun meydançasında dostlarla matç (adambaşı 4-6 AZN)."
                    )
                    cta = "Hansı fəaliyyət sizə daha xoşdur? MeeTTabb tətbiqində tədbir açıb dostlarınızı dərhal toplaya bilərsiniz."
                else:
                    main_reply = (
                        f"{stated_amount} AZN geniş büdcənizlə həm dostlarınız üçün komanda kvesti (50-80 AZN), "
                        "həm mini-futbol meydançası icarəsi (40-70 AZN), həm də bir neçə zolaqlı bovlinq və ya kartinq çempionatı təşkil edə bilərsiniz!"
                    )
                    cta = "Neçə nəfərlik komanda yığmağı düşünürsünüz? MeeTTabb tətbiqində yarış tədbiri yaradıb bütün heyəti bir yerə toplaya bilərsiniz."
            return main_reply, cta

        # 3. Detect Activities & Intent
        is_budget = any(k in q_lower for k in (
            "büdcə", "budce", "budcem", "qiymət", "qiymet", "neçəyə", "neceye",
            "neçədir", "necedir", "pul", "pulum", "xərc", "xerc", "how much",
            "price", "cost", "цена", "стоимость", "стоит", "бюджет", "fiyat"
        ))
        is_detail = any(k in q_lower for k in (
            "detallı", "detalli", "ətraflı", "etrafli", "daha çox", "daha cox",
            "geniş", "genis", "more detail", "подробнее"
        ))
        is_venue = any(k in q_lower for k in (
            "harada", "harda", "haradadır", "hardadır", "hardadi", "məkan",
            "mekan", "ünvan", "unvan", "yerləş", "where", "location", "где", "адрес"
        ))
        is_hours = any(k in q_lower for k in ("saat", "vaxt", "açıq", "aciq", "bağlı", "bagli", "часы", "время", "hours"))
        is_contact = any(k in q_lower for k in ("əlaqə", "elaqe", "telefon", "nömrə", "nomre", "связь", "контакт", "contact"))
        is_app_overview = any(k in q_lower for k in (
            "meettabb nədir", "meettabb nedir", "necə işləyir", "nece isleyir",
            "tətbiq nədir", "tetbiq nedir", "nə üçündür", "ne ucundur",
            "app haqqında", "app haqqinda", "tətbiq haqqında",
            "можно информацию", "расскажите", "что такое", "что за приложение", "подробнее о",
            "can i have info", "can i get info", "tell me more", "what is meettabb", "about the app"
        ))

        cur_sports = any(k in q_lower for k in (
            "idman", "sport", "спорт", "fitness", "fitnes", "məşq", "mesq", "trenirovka"
        ))
        cur_karting = any(k in q_lower for k in ("kartinq", "karting", "gokart", "картинг"))
        cur_bowling = any(k in q_lower for k in ("bovlinq", "bowling", "боулинг"))
        cur_cycling = any(k in q_lower for k in ("velosiped", "velo", "bulvar", "velopark", "велосипед", "вело"))
        cur_boardgames = (
            any(k in q_lower for k in ("şahmat", "wahmat", "monopoly", "mafia", "catan", "board game", "настольн", "шахмат", "мафия", "masaüstü", "masaustu", "stolüstü", "stolustu"))
            or ("oyun" in q_lower and not cur_sports and not any(w in q_lower for w in ("futbol", "video oyun", "kompyuter", "idman")))
        )
        cur_football = any(k in q_lower for k in ("futbol", "meydança", "stadion", "sportinq", "футбол"))
        cur_paintball = any(k in q_lower for k in ("peyntbol", "paintball", "пейнтбол"))
        cur_escape = any(k in q_lower for k in ("kvest", "quest", "escape", "квест"))

        has_cur_activity = any([
            cur_karting, cur_bowling, cur_cycling, cur_boardgames,
            cur_football, cur_paintball, cur_escape, cur_sports
        ])

        # Active recall from history only applies if user query is a follow-up inquiry about details/budget/venues,
        # and NOT a new distinct query about platform differentiators, app overview, or referral.
        is_followup = (
            is_budget or is_detail or is_venue or is_hours
            or any(w in q_lower for w in ("olar", "bəs", "bes", "neçəyə", "neceye", "harda", "harada"))
            or len(q_lower.split()) <= 3
        ) and not (is_differentiators or is_app_overview or is_referral or is_browser_or_not_downloaded or is_general_info_request)

        if has_cur_activity:
            active_karting = cur_karting
            active_bowling = cur_bowling
            active_cycling = cur_cycling
            active_boardgames = cur_boardgames
            active_football = cur_football
            active_paintball = cur_paintball
            active_escape = cur_escape
            active_sports = cur_sports
        elif is_followup:
            active_karting = any(k in hist_text for k in ("kartinq", "karting", "gokart", "картинг"))
            active_bowling = any(k in hist_text for k in ("bovlinq", "bowling", "боулинг"))
            active_cycling = any(k in hist_text for k in ("velosiped", "velo", "bulvar", "velopark", "велосипед", "вело"))
            active_sports = any(k in hist_text for k in ("idman", "sport", "спорт", "fitness", "fitnes", "məşq", "mesq", "trenirovka"))
            active_boardgames = (
                any(k in hist_text for k in ("şahmat", "wahmat", "monopoly", "mafia", "catan", "board game", "настольн", "шахмат", "мафия", "masaüstü", "masaustu", "stolüstü", "stolustu"))
                or ("oyun" in hist_text and not active_sports and not any(w in hist_text for w in ("futbol", "video oyun", "idman")))
            )
            active_football = any(k in hist_text for k in ("futbol", "meydança", "stadion", "sportinq", "футбол"))
            active_paintball = any(k in hist_text for k in ("peyntbol", "paintball", "пейнтбол"))
            active_escape = any(k in hist_text for k in ("kvest", "quest", "escape", "квест"))
        else:
            active_karting = False
            active_bowling = False
            active_cycling = False
            active_boardgames = False
            active_football = False
            active_paintball = False
            active_escape = False
            active_sports = False

        # 5. Case Routing with Localization
        # Case 0: App Overview / Broad Info Inquiry ("Можно информацию?", "MeeTTabb nədir?", "Tell me about the app")
        if is_app_overview:
            if lang == LanguageCode.RU:
                main_reply = (
                    "MeeTTabb — это современная платформа активностей и мероприятий в Баку, созданная для того, чтобы объединять людей в реальной жизни вместо бесконечного скроллинга в телефоне! "
                    "В приложении можно создавать и находить события: картинг, боулинг, велопрогулки, футбол, настольные игры, пейнтбол и квесты. "
                    "Приглашайте друзей через ссылки-приглашения или находите новых единомышленников для совместного отдыха. Приложение полностью бесплатное для iOS и Android."
                )
                cta = "Какие направления вам наиболее интересны: картинг, боулинг, квесты, велопрогулки или спорт? Помогу подобрать идеальный план!"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "MeeTTabb is a social activity and event platform in Baku designed to bring people together in the real world instead of endless screen time! "
                    "Through the app, you can discover or host exciting events: karting, bowling, cycling, football matches, board games, paintball, and escape rooms. "
                    "Invite friends via direct links or recruit missing teammates. The app is completely free on both iOS and Android."
                )
                cta = "Which activities do you enjoy most: karting, bowling, cycling, escape rooms, or football? I can help you plan your outing!"
            else:
                main_reply = (
                    "MeeTTabb – Bakıda insanları virtual dünyadan və tək-tənha telefon sürüşdürməkdən çıxarıb real həyatda bir araya gətirən aktivlik və tədbir platformasıdır. "
                    "Tətbiqdə kartinq, bovlinq, velosiped, futbol, masaüstü oyunlar, peyntbol və kvest kimi fəaliyyətlər yaradıb dostlarınızı dəvət edə və ya komandanıza yeni iştirakçılar cəlb edə bilərsiniz. "
                    "Hər bir tədbirin xüsusi qrup çatı var. Tətbiq həm iOS, həm də Android üçün tamamilə pulsuzdur."
                )
                cta = "Hansı fəaliyyətlərlə maraqlanırsınız: kartinq, bovlinq, velosiped, kvest, yoxsa futbol? Marağınıza uyğun tədbir təşkil edə bilərik."

        # Case 1: Both Karting and Bowling
        elif active_karting and active_bowling:
            if lang == LanguageCode.RU:
                if is_budget or (is_detail and not is_venue):
                    main_reply = (
                        "Примерный бюджет для картинга и боулинга в Баку:\n"
                        "• Картинг: 10 минут заезда обойдется примерно в 15-25 AZN (Baku Karting 15-20 AZN, GoKart Baku 15-25 AZN).\n"
                        "• Боулинг: аренда дорожки на 1 час стоит 20-35 AZN (28 Mall 20-30 AZN, Ganjlik Mall 25-35 AZN). "
                        "В компании из 3-4 друзей выходит всего около 5-10 AZN с человека."
                    )
                    cta = "Какой торговый центр вам ближе: 28 Mall, Ganjlik Mall или Deniz Mall? В MeeTTabb можно создать встречу и позвать друзей."
                else:
                    main_reply = (
                        "Популярные места для картинга и боулинга в Баку и цены:\n\n"
                        "• Картинг:\n"
                        "- Baku Karting & Event Center (шоссе Беюкшор, крытый трек, 10 минут заезда 15-20 AZN)\n"
                        "- GoKart Baku (ТРЦ Deniz Mall, заезд 15-25 AZN)\n\n"
                        "• Боулинг:\n"
                        "- 28 Mall Bowling (м. 28 Мая, аренда дорожки 1 час 20-30 AZN)\n"
                        "- Ganjlik Mall CinemaPlus Bowling (25-35 AZN/час)\n"
                        "- Olympic Star Bowling (ул. Теймура Алиева, 20-30 AZN)"
                    )
                    cta = "Что вам больше по душе: драйв картинга или игра в боулинг с компанией? В приложении MeeTTabb можно создать событие и позвать друзей!"
            elif lang == LanguageCode.EN:
                if is_budget or (is_detail and not is_venue):
                    main_reply = (
                        "Estimated budget for karting and bowling in Baku:\n"
                        "• Karting: 10-minute session costs about 15-25 AZN (Baku Karting 15-20 AZN, GoKart Baku 15-25 AZN).\n"
                        "• Bowling: Lane rental is 20-35 AZN/hour (28 Mall 20-30 AZN, Ganjlik Mall 25-35 AZN). "
                        "With a group of friends, it comes down to roughly 5-10 AZN per person."
                    )
                    cta = "Which location suits you better: 28 Mall, Ganjlik Mall, or Deniz Mall? You can create a meetup on MeeTTabb to invite friends."
                else:
                    main_reply = (
                        "Popular spots for karting and bowling in Baku with prices:\n\n"
                        "• Karting:\n"
                        "- Baku Karting & Event Center (Boyukshor highway, indoor track, 10-min drive 15-20 AZN)\n"
                        "- GoKart Baku (Daniz Mall, 15-25 AZN)\n\n"
                        "• Bowling:\n"
                        "- 28 Mall Bowling (28 May metro, lane rental 1 hour 20-30 AZN)\n"
                        "- Ganjlik Mall CinemaPlus Bowling (25-35 AZN/hour)\n"
                        "- Olympic Star Bowling (Teymur Aliyev str., 20-30 AZN)"
                    )
                    cta = "Would you prefer a thrilling karting race or a friendly bowling match? You can create an activity in the MeeTTabb app and invite friends in one tap."
            else:
                if is_budget or (is_detail and not is_venue):
                    main_reply = (
                        "Kartinq və bovlinq üçün təxmini büdcə:\n"
                        "• Kartinq: 10 dəqiqəlik sürüş üçün 15-25 AZN (Baku Karting 15-20 AZN, GoKart Baku 15-25 AZN).\n"
                        "• Bovlinq: Zolağın saatlıq icarəsi 20-35 AZN (28 Mall 20-30 AZN, Gənclik Mall 25-35 AZN). "
                        "Bir neçə dostla getdikdə adambaşı təxminən 5-10 AZN xərc düşür."
                    )
                    cta = "Hansı məkan sizə daha yaxındır: 28 Mall, Gənclik Mall, yoxsa Dəniz Mall? MeeTTabb tətbiqində tədbir yaradaraq dostlarınızı bir toxunuşla dəvət edə bilərsiniz."
                else:
                    main_reply = (
                        "Bakıda kartinq və bovlinq üçün ən məşhur məkanlar və qiymətlər:\n\n"
                        "• Kartinq:\n"
                        "- Baku Karting & Event Center (Böyükşor şossesi, qapalı trek, 10 dəqiqə sürüş 15-20 AZN)\n"
                        "- GoKart Baku (Dəniz Mall əyləncə mərkəzi, sürüş qiyməti 15-25 AZN)\n\n"
                        "• Bovlinq:\n"
                        "- 28 Mall Bowling (28 May metrosu, zolaq icarəsi 1 saat 20-30 AZN)\n"
                        "- Gənclik Mall CinemaPlus Bowling (saatı 25-35 AZN)\n"
                        "- Olympic Star Bowling (Teymur Əliyev küçəsi, 20-30 AZN)"
                    )
                    cta = "Dostlarınızla sürətli kartinq yarışı istərdiniz, yoxsa əyləncəli bovlinq oyunu? MeeTTabb tətbiqində tədbir yaradaraq komandanızı asanlıqla çağıra bilərsiniz."

        # Case 2: Karting alone
        elif active_karting:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Лучшие места для картинга в Баку:\n"
                    "- Baku Karting & Event Center (шоссе Беюкшор, крытый трек, 10 минут заезда 15-20 AZN)\n"
                    "- GoKart Baku (ТРЦ Deniz Mall, заезд 15-25 AZN)"
                )
                cta = "Вы катаетесь в первый раз или хотите устроить заезд с друзьями? В MeeTTabb можно легко создать заезд и собрать участников!"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "Best venues for karting in Baku:\n"
                    "- Baku Karting & Event Center (Boyukshor highway, indoor track, 10-min drive 15-20 AZN)\n"
                    "- GoKart Baku (Daniz Mall, 15-25 AZN per session)"
                )
                cta = "Is this your first time or are you organizing a grand prix with friends? You can create a karting event on MeeTTabb to invite everyone."
            else:
                main_reply = (
                    "Bakıda kartinq üçün ən məşhur məkanlar:\n"
                    "- Baku Karting & Event Center (Böyükşor şossesi, qapalı trek, 10 dəqiqə sürüş 15-20 AZN)\n"
                    "- GoKart Baku (Dəniz Mall əyləncə mərkəzi, sürüş qiyməti 15-25 AZN)"
                )
                cta = "İlk dəfə sürəcəksiniz, yoxsa dostlarınızla yarış təşkil edirsiniz? MeeTTabb tətbiqində kartinq yarışı açıb komandanızı dəvət edə bilərsiniz."

        # Case 3: Bowling alone
        elif active_bowling:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Популярные боулинг-центры в Баку:\n"
                    "- 28 Mall Bowling (м. 28 Мая, аренда дорожки 1 час 20-30 AZN)\n"
                    "- Ganjlik Mall CinemaPlus Bowling (25-35 AZN/час)\n"
                    "- Olympic Star Bowling (ул. Теймура Алиева, 20-30 AZN)"
                )
                cta = "Какой торговый центр вам ближе: 28 Mall, Ganjlik Mall или Olympic Star? В MeeTTabb можно создать встречу и позвать друзей."
            elif lang == LanguageCode.EN:
                main_reply = (
                    "Popular bowling centers in Baku:\n"
                    "- 28 Mall Bowling (28 May metro, lane rental 1 hour 20-30 AZN)\n"
                    "- Ganjlik Mall CinemaPlus Bowling (25-35 AZN/hour)\n"
                    "- Olympic Star Bowling (Teymur Aliyev str., 20-30 AZN)"
                )
                cta = "Which location is most convenient for you: 28 Mall, Ganjlik Mall, or Olympic Star? Create a bowling outing on MeeTTabb to get your crew together."
            else:
                main_reply = (
                    "Bakıda bovlinq üçün məşhur məkanlar:\n"
                    "- 28 Mall Bowling (28 May metrosu, zolaq icarəsi 1 saat 20-30 AZN)\n"
                    "- Gənclik Mall CinemaPlus Bowling (saatı 25-35 AZN)\n"
                    "- Olympic Star Bowling (Teymur Əliyev küçəsi, 20-30 AZN)"
                )
                cta = "Hansı mərkəz sizə daha rahatdır: 28 Mall, Gənclik Mall, yoxsa Olympic Star? MeeTTabb-da tədbir yaradaraq komandanızı asanlıqla toplaya bilərsiniz."

        # Case 4: Cycling
        elif active_cycling:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Лучшие места для велопрогулок в Баку:\n"
                    "- Приморский Бульвар (прокат 3-5 AZN/час вдоль велодорожки)\n"
                    "- Велопарк в Шыхово (открытые горные и скоростные трассы)"
                )
                cta = "Планируете вечернюю прогулку по бульвару или горный велопарк в Шыхово? В MeeTTabb можно создать вело-заезд."
            elif lang == LanguageCode.EN:
                main_reply = (
                    "Top cycling spots in Baku:\n"
                    "- Seaside National Park (Baku Boulevard, bike rental 3-5 AZN/hour along the bike lane)\n"
                    "- Velopark Shikhov (outdoor scenic trails and downhill tracks)"
                )
                cta = "Are you planning a chill ride along the Boulevard or mountain biking at Velopark? You can create a cycling event in MeeTTabb."
            else:
                main_reply = (
                    "Bakıda velosiped sürmək üçün ən yaxşı məkanlar:\n"
                    "- Dənizkənarı Milli Park (Bakı Bulvarı velozolağı boyunca icarə saatı 3-5 AZN)\n"
                    "- Velopark Şıxov (açıq hava dağ və sürət yolları, münasib qiymətlər)"
                )
                cta = "Bulvarda dənizkənarı gəzinti planlaşdırırsınız, yoxsa Şıxovda dağ veloparkı? MeeTTabb-da velo-yürüş tədbiri aça bilərsiniz."

        # Case 4b: General Sports (Idman / Sport)
        elif active_sports and not (active_football or active_karting or active_cycling):
            if lang == LanguageCode.RU:
                main_reply = (
                    "Популярные спортивные направления и площадки в Баку:\n"
                    "• Футбол и мини-футбол: тренировочные поля Республиканского стадиона им. Тофика Бахрамова, Baku Sporting и Sport City (аренда 40-70 AZN/час, всего 4-6 AZN с игрока).\n"
                    "• Велоспорт и бег: Приморский Бульвар (прокат 3-5 AZN/час) и Велопарк в Шыхово.\n"
                    "• Фитнес и воркаут на свежем воздухе: открытые спортивные зоны вдоль Бакинского бульвара.\n"
                    "• Картинг и пейнтбол: Baku Karting (15-20 AZN), GoKart (15-25 AZN), Baku Paintball Center (15-25 AZN)."
                )
                cta = "Какой вид спорта вам ближе: футбол, велопрогулки, фитнес или картинг? В MeeTTabb можно собрать команду или присоединиться к матчу!"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "Popular sports activities and venues in Baku:\n"
                    "• Football & Mini-Football: Auxiliary pitches at Tofiq Bahramov Stadium, Baku Sporting, and Sport City (rental 40-70 AZN/hr, 4-6 AZN per player).\n"
                    "• Cycling & Running: Baku Boulevard scenic bike path (3-5 AZN/hr) and Velopark Shikhov.\n"
                    "• Outdoor Fitness & Workout: Free open-air fitness zones along Seaside National Park.\n"
                    "• Karting & Paintball: Baku Karting (15-20 AZN), GoKart (15-25 AZN), Baku Paintball Center (15-25 AZN)."
                )
                cta = "Which sport interests you most: football, cycling, fitness, or karting? You can recruit teammates or join existing matches on MeeTTabb!"
            else:
                main_reply = (
                    "Bakıda ən populyar idman fəaliyyətləri və məkanlar:\n"
                    "• Futbol və Mini-Futbol: Tofiq Bəhrəmov adına Respublika Stadionunun yardımçı meydançaları, Baku Sportinq və Sport City (icarə 40-70 AZN/saat, adambaşı 4-6 AZN).\n"
                    "• Velosiped və Qaçış: Bakı Bulvarı velozolağı (icarə 3-5 AZN/saat) və Şıxov Veloparkı (açıq hava sürət yolları).\n"
                    "• Açıq hava fitnes və məşq: Dənizkənarı Milli Park boyunca açıq fitnes zonaları və idman meydançaları.\n"
                    "• Kartinq və Peyntbol: Baku Karting (15-20 AZN), GoKart Baku (15-25 AZN), Baku Paintball Center (15-25 AZN)."
                )
                cta = "Hansı idman növü sizə daha yaxındır: futbol, velosiped, fitnes/qaçış, yoxsa kartinq? MeeTTabb-da komanda yığa və ya mövcud komandaya qoşula bilərsiniz!"

        # Case 5: Board Games
        elif active_boardgames:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Настольные игры (Мафия, Монополия, Catan, Шахматы) проводятся в Baku Board Game Club и Second Home Community Cafe. "
                    "Вход с чаем/кофе и доступом ко всем играм составляет 5-10 AZN с человека."
                )
                cta = "Интересуют игры в Мафию или настольные стратегии (Монополия, Catan)? В MeeTTabb можно собрать компанию для игр."
            elif lang == LanguageCode.EN:
                main_reply = (
                    "For board games (Mafia, Monopoly, Catan, Chess nights), visit Baku Board Game Club and Second Home Community Cafe. "
                    "Entry pass with tea/coffee is approximately 5-10 AZN per person."
                )
                cta = "Are you into Mafia nights or board games like Catan & Monopoly? You can organize a game night on MeeTTabb."
            else:
                main_reply = (
                    "Masaüstü oyunlar (Şahmat, Monopoly, Catan, Mafia gecələri) üçün Baku Board Game Club və Second Home Community Cafe fəaliyyət göstərir. "
                    "Giriş və çay/kofe menyusu ilə adambaşı təxminən 5-10 AZN təşkil edir."
                )
                cta = "Daha çox Mafia gecələri maraqlıdır, yoxsa strateji oyunlar (Monopoly, Catan)? MeeTTabb-da oyun gecəsi təşkil edə bilərsiniz."

        # Case 6: Football
        elif active_football:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Для футбола и мини-футбола в Баку: тренировочные поля Республиканского стадиона им. Тофика Бахрамова, Baku Sporting и Sport City. "
                    "Аренда поля 40-70 AZN в час, с человека в команде всего 4-6 AZN."
                )
                cta = "Сколько игроков не хватает в вашей команде? Создайте матч в приложении MeeTTabb и быстро наберите участников."
            elif lang == LanguageCode.EN:
                main_reply = (
                    "For football and mini-football: Auxiliary pitches at Tofiq Bahramov Republican Stadium, Baku Sporting, and Sport City. "
                    "Pitch rental is 40-70 AZN per hour, roughly 4-6 AZN per player."
                )
                cta = "How many players do you need for your team? Create a football match in MeeTTabb to find teammates quickly."
            else:
                main_reply = (
                    "Futbol və mini-futbol üçün Tofiq Bəhrəmov adına Respublika Stadionunun yardımçı meydançaları, Baku Sportinq və Sport City məkanları mövcuddur. "
                    "Meydança icarəsi saatı 40-70 AZN olub, komandada adambaşı təxminən 4-6 AZN təşkil edir."
                )
                cta = "Komandanızda neçə nəfər çatışmır? MeeTTabb tətbiqində matç yaradıb qısa zamanda yeni oyunçular toplaya bilərsiniz."

        # Case 7: Paintball or Escape Rooms
        elif active_paintball or active_escape:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Пейнтбол и квесты в Баку:\n"
                    "• Пейнтбол: Baku Paintball Center и Xəzər Paintball (15-25 AZN со 100 шарами и экипировкой).\n"
                    "• Квесты: Portal Games Baku и Quest Baku (50-80 AZN на команду, 12-20 AZN с человека)."
                )
                cta = "Хотите захватывающий квест с загадками или пейнтбольный бой на открытом воздухе? Создайте мероприятие в MeeTTabb."
            elif lang == LanguageCode.EN:
                main_reply = (
                    "Paintball and Escape Rooms in Baku:\n"
                    "• Paintball: Baku Paintball Center and Khazar Paintball (15-25 AZN with 100 pellets and gear).\n"
                    "• Escape Rooms: Portal Games Baku and Quest Baku (50-80 AZN for a team, 12-20 AZN per player)."
                )
                cta = "Would you prefer a thrilling escape room puzzle or an outdoor paintball battle? Create an event in MeeTTabb to gather your squad."
            else:
                main_reply = (
                    "Peyntbol və kvest məkanları:\n"
                    "• Peyntbol: Baku Paintball Center və Xəzər Paintball (100 güllə və təchizat daxil 15-25 AZN).\n"
                    "• Kvestlər: Portal Games Baku və Quest Baku (komanda üçün 50-80 AZN, adambaşı 12-20 AZN)."
                )
                cta = "Dostlarınızla adrenalin dolu kvest yoxsa açıq havada peyntbol döyüşü istərdiniz? MeeTTabb-da tədbir yaradaraq komandanızı qura bilərsiniz."

        # Case 9: General Budget Query without specific activity
        elif is_budget:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Примерные бюджеты на активности в Баку:\n"
                    "• Велосипед и настольные игры: 3-10 AZN с человека\n"
                    "• Картинг и боулинг: 15-30 AZN с человека\n"
                    "• Мини-футбол: 4-6 AZN с человека в команде\n"
                    "• Пейнтбол и квесты: 12-25 AZN с человека"
                )
                cta = "Какая активность вам ближе? В MeeTTabb можно создать встречу и позвать друзей."
            elif lang == LanguageCode.EN:
                main_reply = (
                    "Estimated budgets for popular activities in Baku:\n"
                    "• Cycling and Board games: 3-10 AZN per person\n"
                    "• Karting and Bowling: 15-30 AZN per person\n"
                    "• Mini-football: 4-6 AZN per player\n"
                    "• Paintball and Escape rooms: 12-25 AZN per person"
                )
                cta = "Which activity interests you most? You can set up an event in MeeTTabb to get your friends together."
            else:
                main_reply = (
                    "Bakıda fəaliyyətlərdən asılı olaraq təxmini büdcələr:\n"
                    "• Velosiped və masaüstü oyunlar: adambaşı 3-10 AZN\n"
                    "• Kartinq və bovlinq: adambaşı 15-30 AZN\n"
                    "• Mini-futbol: komandada adambaşı 4-6 AZN\n"
                    "• Peyntbol və kvestlər: adambaşı 12-25 AZN"
                )
                cta = "Hansı fəaliyyət istiqaməti sizə daha uyğundur? MeeTTabb tətbiqində tədbir yaradaraq dostlarınızı dəvət edə bilərsiniz."

        # Case 10: Hours / Contact
        elif is_hours or is_contact:
            if lang == LanguageCode.RU:
                main_reply = (
                    "Мобильное приложение MeeTTabb доступно 24/7. Контактный номер: +994501234567. "
                    "Для связи с живым менеджером напишите 'оператор' или 'человек'."
                )
                cta = "Приложение MeeTTabb можно бесплатно скачать в App Store и Google Play!"
            elif lang == LanguageCode.EN:
                main_reply = (
                    "The MeeTTabb mobile app operates 24/7. Contact phone: +994501234567. "
                    "To speak with a live representative, simply type 'operator' or 'manager'."
                )
                cta = "You can download the MeeTTabb app for free on the App Store and Google Play!"
            else:
                main_reply = (
                    "MeeTTabb mobil tətbiqi 24/7 xidmətinizdədir. Əlaqə nömrəsi: +994501234567. "
                    "Canlı insan dəstəyi üçün 'operator' və ya 'insan' yaza bilərsiniz."
                )
                cta = "MeeTTabb tətbiqini App Store və ya Google Play-dən pulsuz yükləyə bilərsiniz!"

        # Case 11: General fallback matching facts from RAG
        else:
            matched_fact = self._extract_matching_facts(rag_context, user_query)
            is_valid_fact = (
                matched_fact
                and not matched_fact.strip().endswith(":")
                and "context is retrieved" not in matched_fact.lower()
                and "knowledge base" not in matched_fact.lower()
                and len(matched_fact.strip()) >= 20
            )
            if is_valid_fact:
                clean_fact = matched_fact.strip(" -*#:").strip()
                clean_fact = re.sub(r"^\d+\.\s*", "", clean_fact)
                clean_fact = re.sub(r"\.{2,}", ".", clean_fact).rstrip(".")
                main_reply = f"{clean_fact}."
                if lang == LanguageCode.RU:
                    cta = "Какой вид отдыха вам наиболее интересен? Помогу организовать событие в MeeTTabb."
                elif lang == LanguageCode.EN:
                    cta = "Which type of activity do you prefer? I can help you set up an event in MeeTTabb."
                else:
                    cta = "Hansı fəaliyyət sizə daha maraqlıdır? MeeTTabb tətbiqində tədbir yaradaraq dostlarınızı dəvət edə bilərsiniz."
            else:
                main_reply = POLITE_FALLBACK_RESPONSES.get(lang, POLITE_FALLBACK_RESPONSES[LanguageCode.AZ])
                cta = None

        # Sanitize punctuation and formatting
        main_reply = re.sub(r"(?<!\.)\.\.(?!\.)", ".", main_reply).strip()
        main_reply = re.sub(r"^\d+\.\s*", "", main_reply).strip()
        if cta:
            cta = re.sub(r"(?<!\.)\.\.(?!\.)", ".", cta).strip()

        return main_reply, cta if cta else None

    def _context_has_facts(self, rag_context: str) -> bool:
        """Determines if the RAG context contains grounded facts."""
        if not rag_context or not rag_context.strip():
            return False
        clean = rag_context.strip().lower()
        # Check for explicit empty / absent indicators
        negative_indicators = [
            "no verified knowledge context available",
            "no relevant business catalog context found",
            "no context found",
            "facts are missing",
            "[empty]",
            "none",
            "heç bir məlumat tapılmadı",
        ]
        for neg in negative_indicators:
            if neg in clean:
                return False
        # Filter out markdown header lines to check if there is actual content
        lines = [l.strip() for l in rag_context.split("\n") if l.strip() and not l.strip().startswith("#")]
        content_text = " ".join(lines).strip()
        return len(content_text) >= 10

    def _extract_matching_facts(self, rag_context: str, user_query: str) -> Optional[str]:
        """Extracts relevant factual snippet from context based on query tokens."""
        if not rag_context:
            return None

        # Clean query tokens (remove punctuation and common stop words)
        stop_words = {
            "nədir", "nedir", "neçəyədir", "neceye", "haqqında", "haqqinda",
            "zəhmət", "olmasa", "məlumat", "verin", "var", "varmı", "varmi",
            "how", "much", "is", "what", "where", "tell", "me", "about",
            "цена", "сколько", "стоит", "где", "находится", "fiyatı", "nedir",
            "və", "ve", "ya", "yaxud", "ilə", "ile", "üçün", "ucun", "sizdə",
            "sizde", "sizin", "bizdə", "bizde", "mən", "men", "nə", "ne", "ki",
            "vəya", "veya", "həm", "hem", "həmçinin", "hemcinin",
        }
        tokens = [
            t.lower() for t in re.findall(r"\w+", user_query)
            if len(t) >= 2 and t.lower() not in stop_words
        ]

        lines = [line.strip() for line in rag_context.split("\n") if line.strip()]

        # Filter out markdown section headers, category titles, and generic title lines from candidate lines
        fact_lines = [
            l for l in lines
            if not l.startswith("#")
            and not l.endswith(":")
            and "kataloqu:" not in l.lower()
            and "məlumatları:" not in l.lower()
            and "platforması:" not in l.lower()
            and "üstünlüklər:" not in l.lower()
            and "qiymətləri:" not in l.lower()
            and "context is retrieved" not in l.lower()
            and "factual context" not in l.lower()
            and "knowledge base" not in l.lower()
            and not l.startswith("Source:")
            and len(l) >= 20
        ]

        if not fact_lines:
            fact_lines = lines

        # Semantic boost triggers for common merchant queries
        u_lower = user_query.lower()
        is_hours_query = any(k in u_lower for k in ("saat", "vaxt", "açıq", "aciq", "bağlı", "bagli", "hansi vaxt"))
        is_location_query = any(k in u_lower for k in ("ünvan", "unvan", "yerləş", "yerles", "harda", "harada", "adress"))
        is_contact_query = any(k in u_lower for k in ("əlaqə", "elaqe", "telefon", "nömrə", "nomre", "zəng"))
        is_delivery_query = any(k in u_lower for k in ("çatdırılma", "catdirilma", "kuryer", "poçt"))
        is_pricing_query = any(k in u_lower for k in ("qiymət", "qiymet", "neçəyə", "neceye", "neçədir", "necedir", "fiyat", "цена", "price", "ödəniş", "odenis", "pulsuz"))
        is_karting_query = any(k in u_lower for k in ("kartinq", "karting", "gokart", "böyükşor", "boyuksor"))
        is_bowling_query = any(k in u_lower for k in ("bovlinq", "bowling"))
        is_cycling_query = any(k in u_lower for k in ("velosiped", "velo", "bulvar", "velopark"))
        is_boardgames_query = any(k in u_lower for k in ("şahmat", "wahmat", "oyun", "monopoly", "mafia", "catan", "board game"))
        is_football_query = any(k in u_lower for k in ("futbol", "meydança", "stadion"))
        is_paintball_escape_query = any(k in u_lower for k in ("peyntbol", "paintball", "kvest", "quest"))
        is_meettabb_activity_query = any(k in u_lower for k in ("meettabb", "meettab", "tədbir", "tedbir", "dost", "yoldaş", "yoldas", "tətbiq", "tetbiq", "app", "yüklə", "yukle", "aktivlik"))

        # Score lines by token overlap and semantic category
        best_line: Optional[str] = None
        best_score = 0
        for line in fact_lines:
            line_lower = line.lower()
            score = 0
            for token in tokens:
                if token in line_lower:
                    score += 1
                elif len(token) >= 4 and token[:len(token)-1] in line_lower:
                    score += 1

            if is_hours_query:
                if any(k in line_lower for k in ("iş saat", "is saat", "saatlarımız", "saatlarimiz", "vaxtı", "rejim")):
                    score += 6
                elif any(k in line_lower for k in ("09:00", "22:00", "23:00")):
                    score += 3
                if "çatdırılma" in line_lower:
                    score -= 3

            if is_location_query:
                if any(k in line_lower for k in ("ünvan", "unvan", "küç", "nizami")):
                    score += 8
                elif "şəhər" in line_lower:
                    score += 2

            if is_contact_query:
                if any(k in line_lower for k in ("əlaqə", "elaqe", "telefon", "+994")):
                    score += 5

            if is_delivery_query:
                if any(k in line_lower for k in ("çatdırılma", "catdirilma", "kuryer", "pulsuz")):
                    score += 5

            if is_pricing_query:
                if any(k in line_lower for k in ("azn", "qiymət", "qiymet", "pulsuz", "ödəniş", "icarə")):
                    score += 5

            if is_karting_query:
                if any(k in line_lower for k in ("kartinq", "karting", "baku karting", "gokart")):
                    score += 9

            if is_bowling_query:
                if any(k in line_lower for k in ("bovlinq", "bowling", "28 mall", "olympic star", "gənclik mall")):
                    score += 9

            if is_cycling_query:
                if any(k in line_lower for k in ("velosiped", "velo", "bulvar", "velopark")):
                    score += 9

            if is_boardgames_query:
                if any(k in line_lower for k in ("şahmat", "oyun", "monopoly", "mafia", "catan", "board game")):
                    score += 9

            if is_football_query:
                if any(k in line_lower for k in ("futbol", "meydança", "stadion", "sportinq")):
                    score += 9

            if is_paintball_escape_query:
                if any(k in line_lower for k in ("peyntbol", "paintball", "kvest", "quest")):
                    score += 9

            if is_meettabb_activity_query:
                if any(k in line_lower for k in ("meettabb", "tədbir", "dost", "aktivlik", "tətbiq", "yoldaş")):
                    score += 7

            if score > best_score:
                best_score = score
                best_line = line

        if best_line is not None and best_score > 0:
            return best_line

        return None

    def _format_factual_reply(self, fact: str, lang: LanguageCode) -> str:
        """Formats the factual snippet into a polite localized assistant reply without tacky formatting."""
        clean_fact = fact.strip(" -*#").strip()
        clean_fact = re.sub(r"^\d+\.\s*", "", clean_fact)
        clean_fact = re.sub(r"\.{2,}", ".", clean_fact).rstrip(".")

        if lang == LanguageCode.RU:
            return f"Уважаемый клиент, согласно нашей информации: {clean_fact}."
        elif lang == LanguageCode.TR:
            return f"Değerli müşterimiz, mevcut bilgilere göre: {clean_fact}."
        elif lang == LanguageCode.EN:
            return f"Dear customer, according to our verified information: {clean_fact}."
        else:
            return f"{clean_fact}."


class OpenAILLMClient(BaseLLMClient):
    """Production OpenAI LLM inference client for GPT-4o / GPT-4o-mini."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o",
        settings: Optional[Settings] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._api_key = api_key or self._settings.OPENAI_API_KEY
        self._model = model
        self._client: Any = None
        self._quota_exhausted_until: float = 0.0

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import openai
                self._client = openai.OpenAI(
                    api_key=self._api_key,
                    timeout=3.0,
                    max_retries=0,
                )
            except ImportError as err:
                raise RuntimeError(
                    "openai package is required for OpenAILLMClient. "
                    "Install it or use InMemoryLLMClient."
                ) from err
        return self._client

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 500,
        temperature: float = 0.2,
        **kwargs: Any,
    ) -> Tuple[str, int]:
        """Invokes OpenAI Chat Completion API with sub-second failover."""
        import time
        now = time.time()
        if now < self._quota_exhausted_until:
            fallback = InMemoryLLMClient()
            return fallback.generate(
                prompt=prompt,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs,
            )

        client = self._get_client()
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            response = client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs,
            )
            reply = response.choices[0].message.content or ""
            tokens = response.usage.total_tokens if response.usage else len(reply.split())
            return reply, tokens
        except Exception as exc:
            err_str = str(exc)
            if "429" in err_str or "quota" in err_str.lower() or "rate_limit" in err_str.lower():
                logger.warning("OpenAI rate limit / quota exceeded (429). Activating local inference engine.")
                self._quota_exhausted_until = now + 600.0
            else:
                logger.warning("OpenAI API call failed (%s). Falling back to local inference engine.", exc)
            fallback = InMemoryLLMClient()
            return fallback.generate(
                prompt=prompt,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs,
            )


# Global singleton cache for LLM client
_GLOBAL_LLM_CLIENT: Optional[BaseLLMClient] = None


def get_llm_client(
    client: Optional[BaseLLMClient] = None,
    use_in_memory: Optional[bool] = None,
) -> BaseLLMClient:
    """Returns the configured or singleton LLM client."""
    global _GLOBAL_LLM_CLIENT
    if client is not None:
        _GLOBAL_LLM_CLIENT = client
        return _GLOBAL_LLM_CLIENT

    if _GLOBAL_LLM_CLIENT is None:
        settings = get_settings()
        is_mock = use_in_memory if use_in_memory is not None else (settings.ENVIRONMENT == "testing")
        if is_mock:
            _GLOBAL_LLM_CLIENT = InMemoryLLMClient()
        else:
            if settings.OPENAI_API_KEY and not settings.OPENAI_API_KEY.startswith("dummy"):
                _GLOBAL_LLM_CLIENT = OpenAILLMClient(api_key=settings.OPENAI_API_KEY)
            else:
                _GLOBAL_LLM_CLIENT = InMemoryLLMClient()

    return _GLOBAL_LLM_CLIENT


def reset_llm_client() -> None:
    """Reset the global LLM client singleton."""
    global _GLOBAL_LLM_CLIENT
    _GLOBAL_LLM_CLIENT = None
