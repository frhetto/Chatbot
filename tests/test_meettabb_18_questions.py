import pytest
from app.clients.llm_client import InMemoryLLMClient
from app.models.enums import LanguageCode


@pytest.fixture
def llm():
    return InMemoryLLMClient()


def test_18_questions_english(llm):
    questions_en = [
        ("How do I find specific activities like sports, gaming, or outdoor adventures in Baku?", "Discover"),
        ("Can I filter the map to only show events happening this weekend?", "Soon"),
        ("Do I have to pay to use MeeTTabb or join events?", "FREE"),
        ("How can I see who the host is and who else is participating before I join?", "Host"),
        ("What happens if I tap to join an event but later need to cancel?", "Leave"),
        ("How do I create and host a new meetup on the platform?", "Host"),
        ("What does the 'Show in Discovery' toggle do when I create an activity?", "Show in Discovery"),
        ("Can I make my event private or invite-only for my existing friends?", "private"),
        ("How do I change the location, time, or category of an event I've already published?", "Edit Event"),
        ("Is there a limit to how many people can join the activity I host?", "Participant Limit"),
        ("How do I access the group chat for an activity I just joined?", "Joined"),
        ("Can I send friend requests to people I meet at an event?", "Add Friend"),
        ("Where can I find my friend list and direct messages?", "Friends"),
        ("How do I block or report a user who is acting inappropriately in the chat?", "Report User"),
        ("How do I edit my profile details, picture, and interests?", "Edit Profile"),
        ("I'm experiencing an issue with the map loading; how do I report a bug?", "Report a Bug"),
        ("Is my exact location shared with other users on the platform?", "NOT"),
        ("How can I permanently delete my account and personal data?", "Delete Account"),
    ]

    for q, expected_snippet in questions_en:
        prompt = (
            f"### SYSTEM PERSONA & MERCHANT PROFILE:\nYou are the assistant for 'MeeTTabb'.\n"
            f"### TARGET LANGUAGE CODE: EN\n"
            f"### VERIFIED RAG CONTEXT BLOCK START ###\nMeeTTabb Tədbir və Aktivlik Platforması\n### VERIFIED RAG CONTEXT BLOCK END ###\n"
            f"### CURRENT USER QUERY:\n{q}\n"
        )
        reply, tokens = llm.generate(prompt=prompt, system_prompt="")
        assert expected_snippet.lower() in reply.lower(), f"Query: {q}\nReply: {reply}"
        assert "### FOLLOW_UP_MESSAGE ###" in reply, f"Missing follow-up CTA in query: {q}"


def test_18_questions_azerbaijani(llm):
    questions_az = [
        ("İdman və oyun fəaliyyətlərini necə tapa bilərəm?", "Discover"),
        ("Bu həftəsonu olan tədbirləri xəritədə necə filtrləyim?", "Soon"),
        ("Tətbiqdən istifadə etmək və qoşulmaq ödənişlidir?", "PULSUZDUR"),
        ("Təşkilatçının kim olduğunu və kimlərin qoşulduğunu necə görə bilərəm?", "Host"),
        ("Tədbirə qoşulsam sonra imtina edə bilərəm?", "Leave"),
        ("Necə yeni tədbir yarada bilərəm?", "Host"),
        ("Show in Discovery düyməsi nə edir?", "Show in Discovery"),
        ("Tədbiri yalnız dostlarım üçün özəl və qapalı edə bilərəm?", "Show in Discovery"),
        ("Dərc etdiyim tədbirin vaxtını və ya məkanını necə dəyişim?", "Edit Event"),
        ("İştirakçı limiti qoymaq olar?", "Participant Limit"),
        ("Qrup çatına necə daxil olum?", "Joined"),
        ("Tədbirdə tanış olduğum insanlara dostluq göndərə bilərəm?", "Add Friend"),
        ("Dostlar siyahısı və birbaşa mesajlar haradadır?", "Friends"),
        ("Narahat edən istifadəçini necə bloklayım və ya şikayət edim?", "Report User"),
        ("Profil məlumatlarımı, şəklimi və maraqlarımı necə redaktə edim?", "Edit Profile"),
        ("Xəritə açılmır, bu xətanı necə bildirim?", "Report a Bug"),
        ("Dəqiq canlı məkanım digər istifadəçilərlə paylaşılır?", "XEYR"),
        ("Hesabımı və məlumatlarımı birdəfəlik necə silim?", "Delete Account"),
    ]

    for q, expected_snippet in questions_az:
        prompt = (
            f"### SYSTEM PERSONA & MERCHANT PROFILE:\nYou are the assistant for 'MeeTTabb'.\n"
            f"### TARGET LANGUAGE CODE: AZ\n"
            f"### VERIFIED RAG CONTEXT BLOCK START ###\nMeeTTabb Tədbir və Aktivlik Platforması\n### VERIFIED RAG CONTEXT BLOCK END ###\n"
            f"### CURRENT USER QUERY:\n{q}\n"
        )
        reply, tokens = llm.generate(prompt=prompt, system_prompt="")
        assert expected_snippet.lower() in reply.lower(), f"Query: {q}\nReply: {reply}"
        assert "### FOLLOW_UP_MESSAGE ###" in reply, f"Missing follow-up CTA in query: {q}"


def test_18_questions_russian(llm):
    questions_ru = [
        ("Как найти спортивные или игровые активности в Баку?", "Discover"),
        ("Можно ли отфильтровать карту на эти выходные?", "Soon"),
        ("Надо ли платить за использование MeeTTabb или участие?", "БЕСПЛАТН"),
        ("Как узнать, кто организатор и кто уже участвует?", "Host"),
        ("Что делать, если нажал присоединиться к событию, но нужно отменить участие?", "Leave"),
        ("Как создать и провести мероприятие?", "Host"),
        ("Что делает переключатель Show in Discovery?", "Show in Discovery"),
        ("Можно ли сделать мероприятие закрытым только для друзей?", "Show in Discovery"),
        ("Как изменить время или место уже опубликованного события?", "Edit Event"),
        ("Есть ли лимит участников?", "лимит"),
        ("Как войти в групповой чат мероприятия?", "Joined"),
        ("Можно ли отправить заявку в друзья участникам?", "Add Friend"),
        ("Где найти список друзей и личные сообщения?", "Friends"),
        ("Как заблокировать или пожаловаться на пользователя в чате?", "Report User"),
        ("Как изменить профиль, фото и интересы?", "Edit Profile"),
        ("Карта не загружается, как сообщить об ошибке?", "Report a Bug"),
        ("Делится ли приложение моим точным местоположением?", "НЕТ"),
        ("Как навсегда удалить аккаунт и личные данные?", "Delete Account"),
    ]

    for q, expected_snippet in questions_ru:
        prompt = (
            f"### SYSTEM PERSONA & MERCHANT PROFILE:\nYou are the assistant for 'MeeTTabb'.\n"
            f"### TARGET LANGUAGE CODE: RU\n"
            f"### VERIFIED RAG CONTEXT BLOCK START ###\nMeeTTabb Tədbir və Aktivlik Platforması\n### VERIFIED RAG CONTEXT BLOCK END ###\n"
            f"### CURRENT USER QUERY:\n{q}\n"
        )
        reply, tokens = llm.generate(prompt=prompt, system_prompt="")
        assert expected_snippet.lower() in reply.lower(), f"Query: {q}\nReply: {reply}"
        assert "### FOLLOW_UP_MESSAGE ###" in reply, f"Missing follow-up CTA in query: {q}"


def test_sports_vs_boardgames(llm):
    # Case: Idman and idman oyunlari must route to sports, NOT board games
    prompt_idman = (
        "### SYSTEM PERSONA & MERCHANT PROFILE:\nYou are the assistant for 'MeeTTabb'.\n"
        "### TARGET LANGUAGE CODE: AZ\n"
        "### VERIFIED RAG CONTEXT BLOCK START ###\nMeeTTabb Tədbir və Aktivlik Platforması\n### VERIFIED RAG CONTEXT BLOCK END ###\n"
        "### RECENT CONVERSATION HISTORY:\n[Customer]: Bakıda maraqlı oyun və fəaliyyət varmı?\n[Assistant]: Bəli! İdman, stolüstü oyunlar və s.\n"
        "### CURRENT USER QUERY:\nIdman\n"
    )
    reply_idman, _ = llm.generate(prompt=prompt_idman, system_prompt="")
    assert "futbol" in reply_idman.lower() or "idman" in reply_idman.lower()
    assert "masaüstü" not in reply_idman.lower()
    assert "şahmat" not in reply_idman.lower()

    prompt_idman_oyunlari = (
        "### SYSTEM PERSONA & MERCHANT PROFILE:\nYou are the assistant for 'MeeTTabb'.\n"
        "### TARGET LANGUAGE CODE: AZ\n"
        "### VERIFIED RAG CONTEXT BLOCK START ###\nMeeTTabb Tədbir və Aktivlik Platforması\n### VERIFIED RAG CONTEXT BLOCK END ###\n"
        "### CURRENT USER QUERY:\nidman oyunlari\n"
    )
    reply_io, _ = llm.generate(prompt=prompt_idman_oyunlari, system_prompt="")
    assert "futbol" in reply_io.lower() or "idman" in reply_io.lower()
    assert "şahmat" not in reply_io.lower()


def test_cancellation_informal_vs_formal(llm):
    # Both informal and formal Azerbaijani must yield identical cancellation responses
    q_informal = "Tedbire qoshulmaq ucun toxunsam amma sonra legv etsem ne olar?"
    q_formal = "Tədbirə qoşulmaq üçün toxunsam, lakin sonradan ləğv etməli olsam, nə baş verir?"

    prompt_inf = (
        "### SYSTEM PERSONA & MERCHANT PROFILE:\nYou are the assistant for 'MeeTTabb'.\n"
        "### TARGET LANGUAGE CODE: AZ\n"
        "### VERIFIED RAG CONTEXT BLOCK START ###\nMeeTTabb Tədbir və Aktivlik Platforması\n### VERIFIED RAG CONTEXT BLOCK END ###\n"
        f"### CURRENT USER QUERY:\n{q_informal}\n"
    )
    prompt_for = (
        "### SYSTEM PERSONA & MERCHANT PROFILE:\nYou are the assistant for 'MeeTTabb'.\n"
        "### TARGET LANGUAGE CODE: AZ\n"
        "### VERIFIED RAG CONTEXT BLOCK START ###\nMeeTTabb Tədbir və Aktivlik Platforması\n### VERIFIED RAG CONTEXT BLOCK END ###\n"
        f"### CURRENT USER QUERY:\n{q_formal}\n"
    )

    r_inf, _ = llm.generate(prompt=prompt_inf, system_prompt="")
    r_for, _ = llm.generate(prompt=prompt_for, system_prompt="")

    assert "tərk et" in r_inf.lower() or "leave" in r_inf.lower()
    assert r_inf == r_for


def test_terminology_delete_account(llm):
    # Query asking what delete account means in Azerbaijani
    prompt = (
        "### SYSTEM PERSONA & MERCHANT PROFILE:\nYou are the assistant for 'MeeTTabb'.\n"
        "### TARGET LANGUAGE CODE: AZ\n"
        "### VERIFIED RAG CONTEXT BLOCK START ###\nMeeTTabb Tədbir və Aktivlik Platforması\n### VERIFIED RAG CONTEXT BLOCK END ###\n"
        "### CURRENT USER QUERY:\nAzerbaycan dilinde delete account nedir?\n"
    )
    reply, _ = llm.generate(prompt=prompt, system_prompt="")
    assert "hesabı sil" in reply.lower()
    assert "deməkdir" in reply.lower()


def test_no_false_escalation_on_inquiry():
    from app.services.language_engine import get_language_engine
    from app.models.domain import ChatMessage
    from app.models.enums import MessageDirection, PlatformChannel

    le = get_language_engine()
    esc_msg = ChatMessage(
        tenant_id="444fc7ec-266e-4658-9ee2-6e5d5b8c0881",
        session_id="11111111-1111-1111-1111-111111111111",
        sender_id="bot",
        recipient_id="user",
        platform=PlatformChannel.INSTAGRAM,
        direction=MessageDirection.OUTBOUND,
        message_body="Hörmətli müştəri, sizi menecerimizə yönləndirirəm.",
    )

    # Substantive price inquiry with "isteyirem" must NOT be treated as escalation
    assert not le.is_escalation_intent("Salam, qiymətləri öyrənmək isteyirem", history=[esc_msg])
    assert not le.is_escalation_intent("Menecer istemirem, qiymetleri deyin", history=[esc_msg])
