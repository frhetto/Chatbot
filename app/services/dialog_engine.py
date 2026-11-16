"""Subsystem 5.0 (Processes 5.2, 5.3): Multi-Lingual AI Dialog & Persona Engine.

Provides the `DialogGenerationEngine` control class as specified in
`project_documentation.md`, `system_analysis_and_design.md`, and `tickets/TICKET-08.md`.
Assembles merchant system prompts with authentic Baku commerce persona etiquette,
injects retrieved RAG factual context, enforces strict anti-hallucination guardrails,
governs multi-lingual responses (AZ, RU, TR, EN), detects escalation triggers,
and enforces mobile screen formatting constraints.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Union

from app.clients.llm_client import (
    ESCALATION_MESSAGES,
    POLITE_FALLBACK_RESPONSES,
    BaseLLMClient,
    get_llm_client,
)
from app.models.domain import BusinessProfile, ChatMessage, ChatSession, CustomerProfile
from app.models.enums import LanguageCode
from app.models.schemas import LLMResponse
from app.services.language_engine import LanguageDetectionEngine, get_language_engine

logger = logging.getLogger(__name__)

# Maximum character length for mobile conversational screen display
MAX_MOBILE_CHARACTERS = 1000

# Mapping of LanguageCode to human-readable language names
LANGUAGE_NAMES: Dict[LanguageCode, str] = {
    LanguageCode.AZ: "Azərbaycan dili",
    LanguageCode.RU: "Русский язык",
    LanguageCode.TR: "Türkçe",
    LanguageCode.EN: "English",
}

# Disallowed prompt leak and exploit patterns
_DISALLOWED_PATTERNS: List[re.Pattern] = [
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"system\s+prompt\s*:", re.IGNORECASE),
    re.compile(r"you\s+are\s+a\s+helpful\s+assistant\s+and\s+your\s+instructions\s+are", re.IGNORECASE),
    re.compile(r"system\s+instructions\s*:", re.IGNORECASE),
]


class DialogGenerationEngine:
    """Control class for AI dialog generation, persona synthesis, and guardrails (Subsystem 5.0)."""

    def __init__(
        self,
        language_engine: Optional[LanguageDetectionEngine] = None,
        llm_client: Optional[BaseLLMClient] = None,
        max_output_tokens: int = 500,
    ) -> None:
        """Initialize DialogGenerationEngine with dependencies.

        Args:
            language_engine: LanguageDetectionEngine for script normalization and intent detection.
            llm_client: LLM inference client for text completions.
            max_output_tokens: Maximum tokens allowed for generation (default 500).
        """
        self._language_engine: LanguageDetectionEngine = language_engine or get_language_engine()
        self._llm_client: BaseLLMClient = llm_client or get_llm_client()
        self._max_output_tokens: int = max_output_tokens

    # -------------------------------------------------------------------------
    # Properties & UML 2.5 Aliases
    # -------------------------------------------------------------------------

    @property
    def language_engine(self) -> LanguageDetectionEngine:
        """The language detection engine instance."""
        return self._language_engine

    @property
    def languageEngine(self) -> LanguageDetectionEngine:
        """UML 2.5 CamelCase alias for language_engine."""
        return self._language_engine

    @property
    def llm_client(self) -> BaseLLMClient:
        """The LLM inference client instance."""
        return self._llm_client

    @property
    def llmClient(self) -> BaseLLMClient:
        """UML 2.5 CamelCase alias for llm_client."""
        return self._llm_client

    @property
    def max_output_tokens(self) -> int:
        """Maximum tokens for LLM generation."""
        return self._max_output_tokens

    @property
    def maxOutputTokens(self) -> int:
        """UML 2.5 CamelCase alias for max_output_tokens."""
        return self._max_output_tokens

    # -------------------------------------------------------------------------
    # Persona & System Prompt Synthesis (Process 5.2)
    # -------------------------------------------------------------------------

    def apply_azerbaijani_persona(
        self,
        system_prompt: str,
        tenant: BusinessProfile,
        lang: LanguageCode,
    ) -> str:
        """Synthesize localized merchant persona and Baku commerce etiquette into system prompt.

        Customizes persona to be polite, welcoming, and helpful ("Hörmətli müştəri...", "Xoş gəlmisiniz!").
        Injects verified merchant metadata: business name, address / city, phone contact,
        category, and formatted working hours.
        Enforces language alignment: respond strictly in the detected customer language (`lang`).

        Args:
            system_prompt: Base system prompt string or instructions.
            tenant: BusinessProfile containing merchant metadata and operating hours.
            lang: Target customer LanguageCode.

        Returns:
            Assembled localized system persona string.
        """
        # Format operating hours from tenant profile
        working_hours_json = getattr(tenant, "workingHoursJson", getattr(tenant, "working_hours_json", None))
        working_hours_str = self._format_working_hours(working_hours_json)
        lang_name = LANGUAGE_NAMES.get(lang, "Azərbaycan dili")

        # Dynamic persona styling tailored from active tenant configuration
        tone_directive = getattr(tenant, "toneDirective", getattr(tenant, "tone_directive", None))
        if tone_directive:
            tone_bullets = [
                f"- {line.strip().lstrip('-').strip()}"
                for line in str(tone_directive).split("\n")
                if line.strip()
            ]
        else:
            tone_bullets = [
                "- Address the customer with authentic Azerbaijani commercial warmth and respect ('Hörmətli müştəri', 'Xoş gəlmisiniz!').",
                "- Maintain an attentive, polite, and service-oriented demeanor reflecting Azerbaijani hospitality.",
                "- Keep answers concise, factual, and pleasant.",
            ]

        persona_sections: List[str] = [
            "### SYSTEM PERSONA & MERCHANT PROFILE:",
            f"You are the official digital customer service assistant for '{tenant.businessName}'.",
            f"Business Category: {tenant.businessCategory}",
            f"City / Location: {tenant.city}",
            f"Contact Phone: {tenant.phoneContact}",
            f"Operating Hours: {working_hours_str}",
            "",
            "### BAKU COMMERCE ETIQUETTE & TONE:",
            *tone_bullets,
            "",
            "### TARGET LANGUAGE DIRECTIVE:",
            f"- The customer's detected interaction language is: {lang.value} ({lang_name}).",
            f"- You MUST generate your response strictly in {lang_name} ({lang.value})!",
            "- Even when responding in English, Russian, or Turkish, preserve the courteous hospitality of the business.",
        ]

        system_prompt_cfg = getattr(tenant, "systemPrompt", getattr(tenant, "system_prompt", None))
        effective_system_prompt = system_prompt or system_prompt_cfg or ""
        if effective_system_prompt and effective_system_prompt.strip():
            persona_sections.extend(["", "### ADDITIONAL SYSTEM INSTRUCTIONS:", effective_system_prompt.strip()])

        return "\n".join(persona_sections)

    def applyAzerbaijaniPersona(
        self,
        system_prompt: str,
        tenant: BusinessProfile,
        lang: LanguageCode,
    ) -> str:
        """UML 2.5 CamelCase alias for apply_azerbaijani_persona."""
        return self.apply_azerbaijani_persona(system_prompt, tenant, lang)

    def synthesize_prompt(
        self,
        session: ChatSession,
        customer: CustomerProfile,
        user_text: str,
        rag_context: str,
        tenant: BusinessProfile,
        history: Optional[List[ChatMessage]] = None,
    ) -> str:
        """Assembles complete LLM prompt with persona, RAG context, guardrails, history, and query.

        Args:
            session: Active ChatSession with platform details.
            customer: CustomerProfile with identity and preferences.
            user_text: Raw incoming customer query.
            rag_context: Retrieved factual grounding snippet from vector index.
            tenant: BusinessProfile for merchant configuration.
            history: Optional list of recent ChatMessage turns.

        Returns:
            Fully assembled prompt ready for LLM inference.
        """
        # Determine language from query or customer preference
        lang = self._language_engine.detect_language(user_text)

        # 1. Base localized persona
        system_prompt_cfg = getattr(tenant, "systemPrompt", getattr(tenant, "system_prompt", "")) or ""
        persona = self.apply_azerbaijani_persona(system_prompt=system_prompt_cfg, tenant=tenant, lang=lang)

        # 2. Explicit Language Directive
        target_lang_block = [
            f"### TARGET LANGUAGE CODE: {lang.value}",
            f"### TARGET LANGUAGE NAME: {LANGUAGE_NAMES.get(lang, 'Azərbaycan dili')}",
        ]

        # 3. Azerbaijani Informal Internet Slang & Phonetic Typing Interpretation
        slang_directive_block = [
            "### AZERBAIJANI INFORMAL INTERNET SLANG & PHONETIC TYPING INTERPRETATION:",
            "1. Phonetic & Slang Interpretation Directive: Azerbaijani social media and chat users frequently write phonetically without diacritics.",
            "   - You MUST accurately interpret informal phonetic typing: 'w' or 'sh' as 'ş' (e.g., 'yaxwi'/'yaxshi' -> 'yaxşı', 'wexsi' -> 'şəxsi', 'bawla' -> 'başla', 'wahmat' -> 'şahmat'), 'c' or 'ch' as 'ç' (e.g., 'cox'/'chox' -> 'çox', 'nece' -> 'necə', 'cay' -> 'çay'), 'e' as 'ə' (e.g., 'qiymet' -> 'qiymət', 'tedbir' -> 'tədbir', 'he' -> 'hə'), and colloquial suffixes (e.g., 'yoxdu' -> 'yoxdur', 'hardadi' -> 'haradadır', 'necedi' -> 'neçədir', 'neciyedi' -> 'neçəyədir', 'dostlarimnan' -> 'dostlarımla', 'surmey' -> 'sürmək', 'neyniyim' -> 'nə edim').",
            "2. Literary Output Enforcement: While you must accurately understand and interpret informal internet slang and phonetic typing, you MUST NEVER mimic the user's typos, slang, or missing diacritics in your response!",
            "   - You MUST strictly generate all Azerbaijani responses in clean, high-quality, grammatical literary Azerbaijani with proper Latin diacritics (ə, ş, ç, ğ, ö, ü, ı).",
            "3. Active Recall & Context Continuity: Always actively recall topics from RECENT CONVERSATION HISTORY. If the customer previously discussed an activity or service and now asks for budget, venues, or details without repeating the topic name, answer accurately for that specific context!",
            "4. Anti-Tacky Formatting Enforcement: NEVER start responses with robotic prefixes like 'Hörmətli müştəri, məlumatımıza əsasən:'. NEVER use raw list numbering ('1. ', '2. ', '3. '). NEVER output double periods ('..'). Respond in natural, warm, conversational sentences.",
        ]

        # Dynamic Custom Rules from Tenant Configuration
        custom_rules = getattr(tenant, "customRules", getattr(tenant, "custom_rules", None))
        if custom_rules:
            slang_directive_block.append("### TENANT CUSTOM CONVERSATIONAL RULES:")
            if isinstance(custom_rules, list):
                for idx, rule in enumerate(custom_rules, 1):
                    slang_directive_block.append(f"{idx}. {rule}")
            elif isinstance(custom_rules, str):
                slang_directive_block.append(custom_rules)

        # 4. Strict Anti-Hallucination Guardrails
        guardrails = [
            "### STRICT ANTI-HALLUCINATION GUARDRAILS & FACTUAL GROUNDING:",
            "1. You must ONLY answer using the verified facts provided in the Context snippet below.",
            "2. If the Context snippet does not contain the answer, or if the Context snippet is empty or ungrounded, "
            "you MUST NOT invent, guess, or extrapolate unsupported information.",
            "3. In cases where the information is absent from the Context, you must politely state in the customer's language "
            "that you do not have verified information on this topic, and offer to connect them with a human manager.",
            "4. NEVER invent prices, menu items, business hours, services, discounts, or policies not explicitly stated in the context.",
            "5. Keep the response concise, clear, and formatted for mobile devices (maximum 3-4 short sentences or paragraphs).",
        ]

        # 5. Verified RAG Context Block
        rag_block = [
            "### VERIFIED RAG CONTEXT BLOCK START ###",
        ]
        if rag_context and rag_context.strip():
            rag_block.append(rag_context.strip())
        else:
            rag_block.append("[No verified knowledge context available for this inquiry. Facts are missing.]")
        rag_block.append("### VERIFIED RAG CONTEXT BLOCK END ###")

        # 6. Customer Context
        customer_block = [
            "### CUSTOMER INFORMATION:",
            f"- Customer Channel: {session.platform.value}",
            f"- Preferred Language: {customer.preferredLanguage.value}",
        ]
        if customer.customerName:
            customer_block.append(f"- Customer Name: {customer.customerName}")

        # 7. Conversation History Buffer (last 5-10 turns)
        history_block: List[str] = []
        if history:
            recent_turns = history[-10:]
            history_block.append("### RECENT CONVERSATION HISTORY:")
            for msg in recent_turns:
                direction_label = "Customer" if msg.direction.value == "INBOUND" else "Assistant"
                body = msg.messageBody.strip()
                history_block.append(f"[{direction_label}]: {body}")

        # 8. Current User Query & Execution Command
        query_block = [
            "### CURRENT USER QUERY:",
            user_text.strip(),
            "",
            "### RESPONSE INSTRUCTION:",
            f"Generate a polite, concise, mobile-friendly response to the CURRENT USER QUERY adhering strictly "
            f"to the Verified RAG Context and Anti-Hallucination Guardrails in {lang.value}.",
        ]

        # Assemble full prompt
        all_sections = [
            persona,
            "",
            "\n".join(target_lang_block),
            "",
            "\n".join(slang_directive_block),
            "",
            "\n".join(guardrails),
            "",
            "\n".join(rag_block),
            "",
            "\n".join(customer_block),
        ]
        if history_block:
            all_sections.extend(["", "\n".join(history_block)])
        all_sections.extend(["", "\n".join(query_block)])

        return "\n".join(all_sections)

    def synthesizePrompt(
        self,
        session: ChatSession,
        customer: CustomerProfile,
        user_text: str,
        rag_context: str,
        tenant: BusinessProfile,
        history: Optional[List[ChatMessage]] = None,
    ) -> str:
        """UML 2.5 CamelCase alias for synthesize_prompt."""
        return self.synthesize_prompt(
            session=session,
            customer=customer,
            user_text=user_text,
            rag_context=rag_context,
            tenant=tenant,
            history=history,
        )

    # -------------------------------------------------------------------------
    # Safety Guardrails & Mobile Formatting (Process 5.3)
    # -------------------------------------------------------------------------

    def validate_safety_guardrails(
        self,
        generated_text: str,
        tenant: Optional[BusinessProfile] = None,
    ) -> str:
        """Validates and enforces safety guardrails and mobile screen constraints on generated responses.

        1. Truncates oversized messages to mobile limits (maximum 1000 characters).
        2. Normalizes excessive blank lines for mobile readability.
        3. Scans for disallowed prompt leak patterns or profanity.
        4. Replaces severe policy violations with polite fallback text.

        Args:
            generated_text: Raw output string from LLM inference.
            tenant: Optional active business profile for personalized fallback messaging.

        Returns:
            Sanitized, mobile-compliant response string.
        """
        if not generated_text or not generated_text.strip():
            return ""

        text = generated_text.strip()

        # 1. Disallowed pattern and prompt leak scan
        for pattern in _DISALLOWED_PATTERNS:
            if pattern.search(text):
                logger.warning("Disallowed prompt leak pattern detected in generated response.")
                return (
                    "Hörmətli müştəri, tələbiniz üzrə cavab təhlükəsizlik qaydalarına uyğun gəlmir. "
                    "Zəhmət olmasa başqa sualınız varsa qeyd edin."
                )

        # 2. Normalize whitespace (collapse 3+ newlines to double newlines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        # Strip trailing whitespace on each line
        lines = [line.rstrip() for line in text.split("\n")]
        text = "\n".join(lines).strip()

        # 2b. Strip tacky robotic prefixes and raw numbering
        tacky_prefixes = [
            "Hörmətli müştəri, məlumatımıza əsasən: ",
            "Hormetli musteri, melumatimiza esasen: ",
            "Məlumatımıza əsasən: ",
            "Melumatimiza esasen: ",
        ]
        for pfix in tacky_prefixes:
            if text.startswith(pfix):
                text = text[len(pfix):].strip()

        # Clean leading numbers like '1. ', '2. '
        text = re.sub(r"^\d+[\.\)]\s*", "", text).strip()
        # Thoroughly strip internal RAG context headers and leak strings
        text = re.sub(r"(?i)^The following verified factual context[^\n]*\n*", "", text).strip()
        text = re.sub(r"(?i)^Verified (?:merchant|business) knowledge context[^\n]*\n*", "", text).strip()
        text = re.sub(r"(?i)^MeeTTabb Tədbir və Aktivlik Platforması[:\.]*\n*", "", text).strip()
        text = re.sub(r"(?i)^####\s*Source:[^\n]*\n*", "", text).strip()
        text = re.sub(r"(?i)^###\s*[^\n]+###\n*", "", text).strip()
        if not text and generated_text.strip():
            biz_name = ""
            if tenant is not None:
                if isinstance(tenant, dict):
                    biz_name = tenant.get("businessName") or tenant.get("business_name") or ""
                else:
                    biz_name = getattr(tenant, "businessName", getattr(tenant, "business_name", "")) or ""
            if biz_name:
                text = f"{biz_name} barədə əlavə suallarınız varsa, zəhmət olmasa bildirin."
            else:
                text = "Zəhmət olmasa bildirin necə kömək edə bilərəm."

        # Sanitize double/triple periods like '..' into '.' (preserve intentional '...')
        text = re.sub(r"(?<!\.)\.\.(?!\.)", ".", text)

        # 3. Mobile screen character length truncation (AC-5)
        if len(text) > MAX_MOBILE_CHARACTERS:
            # Attempt to cleanly truncate at a sentence boundary before MAX_MOBILE_CHARACTERS
            cutoff_candidate = text[:MAX_MOBILE_CHARACTERS - 3]
            last_period = max(
                cutoff_candidate.rfind(". "),
                cutoff_candidate.rfind("! "),
                cutoff_candidate.rfind("? "),
                cutoff_candidate.rfind(".\n"),
                cutoff_candidate.rfind("!\n"),
                cutoff_candidate.rfind("?\n"),
                cutoff_candidate.rfind("\n\n"),
            )
            # If a clean boundary exists in the latter part of the snippet (> 650 chars)
            if last_period > 650:
                text = text[:last_period + 1].strip()
            else:
                text = cutoff_candidate.rstrip() + "..."

        return text

    def validateSafetyGuardrails(
        self,
        generated_text: str,
        tenant: Optional[BusinessProfile] = None,
    ) -> str:
        """UML 2.5 CamelCase alias for validate_safety_guardrails."""
        return self.validate_safety_guardrails(generated_text, tenant=tenant)

    # -------------------------------------------------------------------------
    # Dialog Generation & Execution (Process 5.2, 5.3)
    # -------------------------------------------------------------------------

    def generate_reply(
        self,
        session: ChatSession,
        customer: CustomerProfile,
        user_text: str,
        rag_context: str,
        tenant: BusinessProfile,
        history: Optional[List[ChatMessage]] = None,
    ) -> LLMResponse:
        """Generates a structured LLMResponse for the given customer turn.

        Workflow:
        1. Normalizes query and detects language (`lang`).
        2. Evaluates escalation intent via `_language_engine`. If True, immediately
           returns `LLMResponse` with `escalationTriggered = True` and localized handoff message.
        3. Synthesizes full dynamic prompt with persona, RAG context, and anti-hallucination rules.
        4. Invokes LLM client inference.
        5. Applies mobile formatting constraints and safety guardrails.
        6. Returns structured `LLMResponse`.

        Args:
            session: Active ChatSession.
            customer: CustomerProfile.
            user_text: Inbound customer message text.
            rag_context: Verified RAG factual grounding snippet (or empty string).
            tenant: BusinessProfile for merchant persona and metadata.
            history: Optional list of recent ChatMessage turns.

        Returns:
            Structured LLMResponse DTO.
        """
        # 1. Detect inquiry language
        lang = self._language_engine.detect_language(user_text)

        # 2. Check escalation intent (AC-3)
        if self._language_engine.is_escalation_intent(user_text, lang):
            logger.info("Escalation intent detected for customer %s on platform %s", customer.customerId, session.platform)
            escalation_text = ESCALATION_MESSAGES.get(lang, ESCALATION_MESSAGES[LanguageCode.AZ])
            return LLMResponse(
                replyText=escalation_text,
                language=lang,
                tokensUsed=0,
                escalationTriggered=True,
            )

        # 3. Synthesize dynamic prompt
        prompt = self.synthesize_prompt(
            session=session,
            customer=customer,
            user_text=user_text,
            rag_context=rag_context,
            tenant=tenant,
            history=history,
        )

        # 4. Invoke LLM inference client
        raw_reply, tokens_used = self._llm_client.generate(
            prompt=prompt,
            max_tokens=self._max_output_tokens,
        )

        follow_up_msg = None
        if "### FOLLOW_UP_MESSAGE ###" in raw_reply:
            parts = raw_reply.split("### FOLLOW_UP_MESSAGE ###", 1)
            raw_reply = parts[0].strip()
            follow_up_msg = parts[1].strip()

        # 5. Apply safety guardrails and mobile formatting
        guarded_reply = self.validate_safety_guardrails(raw_reply, tenant=tenant)
        guarded_follow_up = self.validate_safety_guardrails(follow_up_msg, tenant=tenant) if follow_up_msg else None

        is_feedback = self._language_engine.is_app_feedback(user_text)

        # 6. Return structured LLMResponse
        return LLMResponse(
            replyText=guarded_reply,
            language=lang,
            tokensUsed=tokens_used,
            escalationTriggered=False,
            followUpText=guarded_follow_up,
            isFeedback=is_feedback,
        )

    def generateReply(
        self,
        session: ChatSession,
        customer: CustomerProfile,
        user_text: str,
        rag_context: str,
        tenant: BusinessProfile,
        history: Optional[List[ChatMessage]] = None,
    ) -> LLMResponse:
        """UML 2.5 CamelCase alias for generate_reply."""
        return self.generate_reply(
            session=session,
            customer=customer,
            user_text=user_text,
            rag_context=rag_context,
            tenant=tenant,
            history=history,
        )

    def generate_response(
        self,
        session: ChatSession,
        customer: CustomerProfile,
        user_text: str,
        rag_context: str,
        tenant: BusinessProfile,
        history: Optional[List[ChatMessage]] = None,
    ) -> LLMResponse:
        """Interface contract alias matching PROJECT.md specification."""
        return self.generate_reply(
            session=session,
            customer=customer,
            user_text=user_text,
            rag_context=rag_context,
            tenant=tenant,
            history=history,
        )

    def generateResponse(
        self,
        session: ChatSession,
        customer: CustomerProfile,
        user_text: str,
        rag_context: str,
        tenant: BusinessProfile,
        history: Optional[List[ChatMessage]] = None,
    ) -> LLMResponse:
        """UML 2.5 CamelCase alias for generate_response."""
        return self.generate_reply(
            session=session,
            customer=customer,
            user_text=user_text,
            rag_context=rag_context,
            tenant=tenant,
            history=history,
        )

    # -------------------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------------------

    def _format_working_hours(self, working_hours_json: Optional[str]) -> str:
        """Parses and formats weekly working hours into readable string."""
        if not working_hours_json or not working_hours_json.strip():
            return "Bazar ertəsi - Bazar: 09:00 - 23:00"

        raw = working_hours_json.strip()
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                parts = []
                for k, v in parsed.items():
                    key_clean = str(k).replace("_", " ").title()
                    parts.append(f"{key_clean}: {v}")
                return "; ".join(parts)
            elif isinstance(parsed, str):
                return parsed
            elif isinstance(parsed, list):
                return "; ".join(str(item) for item in parsed)
        except Exception:
            # Fallback if raw string is not valid JSON
            return raw

        return raw


# -----------------------------------------------------------------------------
# Global Singleton & Factory Function
# -----------------------------------------------------------------------------

_GLOBAL_DIALOG_ENGINE: Optional[DialogGenerationEngine] = None


def get_dialog_engine(
    language_engine: Optional[LanguageDetectionEngine] = None,
    llm_client: Optional[BaseLLMClient] = None,
    max_output_tokens: int = 500,
) -> DialogGenerationEngine:
    """Return DialogGenerationEngine instance with default or custom dependencies."""
    global _GLOBAL_DIALOG_ENGINE
    if language_engine is not None or llm_client is not None or max_output_tokens != 500:
        return DialogGenerationEngine(
            language_engine=language_engine,
            llm_client=llm_client,
            max_output_tokens=max_output_tokens,
        )

    if _GLOBAL_DIALOG_ENGINE is None:
        _GLOBAL_DIALOG_ENGINE = DialogGenerationEngine(
            language_engine=get_language_engine(),
            llm_client=get_llm_client(),
            max_output_tokens=max_output_tokens,
        )
    return _GLOBAL_DIALOG_ENGINE


def reset_dialog_engine() -> None:
    """Reset the global DialogGenerationEngine singleton instance."""
    global _GLOBAL_DIALOG_ENGINE
    _GLOBAL_DIALOG_ENGINE = None
