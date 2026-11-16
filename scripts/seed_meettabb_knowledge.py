"""Seed MeeTTabb knowledge chunks into Supabase PostgreSQL, SQLite, and Pinecone."""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4
import dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
dotenv.load_dotenv(PROJECT_ROOT / ".env")

from app.core.config import get_settings
from app.core.database import get_db_session, get_engine
from app.models.domain import Base, BusinessProfileORM, KnowledgeChunkORM, ChatSessionORM
from app.models.enums import SessionState
from app.clients.embedding_client import get_embedding_client
from app.clients.pinecone_client import get_pinecone_client
from sqlalchemy import select

TENANT_ID = UUID("444fc7ec-266e-4658-9ee2-6e5d5b8c0881")

MEETTABB_CHUNKS = [
    (
        "MeeTTabb Tətbiqi haqqında (About MeeTTabb)",
        "MeeTTabb insanları virtual dünyadan və tək-tənha telefon sürüşdürməkdən çıxarıb, real həyatda bir araya gətirən müasir fəaliyyət və tədbir platformasıdır (Activity Companion App). Tətbiq vasitəsilə Bakıda 46-dan çox maraqlı tədbir və aktivlik kəşf edin, yeni dostlar tapın və ya öz tədbirlərinizi təşkil edin. Platforma 4 əsas üstünlüyə malikdir: İnteraktiv Canlı Xəritə (Map), Referral sistemi və Tabb Coin, Birbaşa və Qrup Mesajlaşması, Dostlar Bölməsi və Komanda Yığma."
    ),
    (
        "Tətbiqin 5 Əsas Naviqasiya Bölməsi (5 Main Navigation Tabs)",
        "MeeTTabb tətbiqinin 5 əsas naviqasiya bölməsi vardır:\n- 'Joined' (Qoşulduğum tədbirlər / Ürək ikonu): Qoşulduğunuz bütün aktivliklərin siyahısı. Tədbirin üzərinə toxunaraq birbaşa onun canlı qrup çatına daxil ola bilərsiniz.\n- 'Discover' (Kəşf et / Kompas ikonu): Bakıda 46+ aktivliyi canlı interaktiv Xəritə (Map) və ya Siyahı (List) görünüşündə araşdırın. 'For You', 'Soon' və 'New' filtrləri mövcuddur.\n- 'Host' (Tədbir yarat / '+' ikonu): Bir neçə toxunuşla yeni tədbir təşkil edin.\n- 'Friends' (Dostlar / İnsanlar ikonu): Dostlar siyahısı, dostların aktivlikləri və təkbətək birbaşa mesajlaşma (DM).\n- 'Profile' (Profil / İstifadəçi ikonu): Profil şəkli, ad, soyad, yaş, cins, bio (maksimum 300 simvol), 10 maraq kateqoriyası, referral kodu, Tabb Coin Marketplace və Parametrlər."
    ),
    (
        "10 Rəsmi Maraq Kateqoriyası (10 Official Interest Categories)",
        "MeeTTabb-da 10 rəsmi maraq kateqoriyası mövcuddur:\n1. Fitness\n2. Outdoor (Açıq hava)\n3. Sports (İdman)\n4. Action (Adrenalin və aksiya)\n5. Social (Sosial görüşlər)\n6. Tabletop Games (Masaüstü oyunlar)\n7. Adventure (Macəra)\n8. Video Games (Video oyunlar)\n9. Fun (Əyləncə)\n10. Wellness (Sağlamlıq).\nBu maraqlar profilinizdə seçildikdə, Discover bölməsindəki 'For You' lenti sizə tam fərdiləşdirilmiş tövsiyələr təqdim edir."
    ),
    (
        "Tədbirləri Kəşf Etmək və Xəritə Filtrləri (Discovering Activities & Soon Filter)",
        "İdman, oyun və açıq hava fəaliyyətlərini tapmaq üçün 'Discover' (Kompas) bölməsinə keçin, interaktiv Xəritə və ya Siyahı görünüşündən istifadə edin. 'Soon' filtr düyməsini sıxdıqda ən yaxın günlərdə və bu həftəsonu baş tutacaq bütün tədbirlər xəritədə filtrlənir. Discover bölməsində istənilən tədbirə və ya xəritə pininə toxunduqda açılan təfərrüat səhifəsində təşkilatçının (Host) adı, profil şəkli və qoşulmuş bütün iştirakçıların siyahısı və sayı aydın görünür."
    ),
    (
        "Ödəniş və Qiymət Siyasəti (100% Free Platform)",
        "MeeTTabb tətbiqindən istifadə, qeydiyyat, tədbir yaratmaq və tədbirlərə qoşulmaq 100% PULSUZDUR! Platforma heç bir komissiya və ya abunə haqqı tələb etmir. Məkan xərcləri (kartinq, bovlinq, kafe, futbol meydançası icarəsi və s.) tədbir iştirakçıları tərəfindən birbaşa həmin məkana ödənilir."
    ),
    (
        "Tədbirə Qoşulmaq və İştirakı Ləğv Etmək (Join & Cancel Policy)",
        "Tədbirə qoşulduqda ('Join') o, dərhal 'Joined' (Ürək ikonu) bölmənizə əlavə olunur və qrup çatı açılır. Əgər planınız dəyişərsə və ya sonradan ləğv etməli olsanız, heç bir problem yoxdur: 'Joined' bölməsindən tədbirə daxil olub 'Leave' (Tədbiri tərk et / İştirakı ləğv et) düyməsinə toxunaraq heç bir cərimə və ya ödəniş olmadan çıxa bilərsiniz. Sizin yeriniz dərhal digər istifadəçilər üçün açılır."
    ),
    (
        "Yeni Tədbir Yaratmaq və İdarə Etmək (Hosting & Managing Events)",
        "Yeni tədbir yaratmaq üçün aşağı menyudan 'Host' ('+') bölməsinə keçin, tədbirin adını yazın, 10 maraq kateqoriyasından uyğun olanları seçin, xəritədə görüş məkanını qeyd edin, tarix və saatı təyin edin, iştirakçı limiti qoyun və 'Show in Discovery' seçimini edin. Tədbir təşkilatçısı istədiyi iştirakçı sayına limit qoya bilər və ya limiti limitsiz saxlaya bilər. Dərc edilmiş tədbirin məkanını, vaxtını və ya kateqoriyasını dəyişmək üçün 'Joined' və ya 'Profile' bölməsindən tədbirinizə daxil olub 'Redaktə et' (Edit Event / Qələm ikonu) seçərək istənilən vaxt yeniləyə bilərsiniz."
    ),
    (
        "'Show in Discovery' Düyməsi və Özəl Tədbirlər (Show in Discovery Toggle)",
        "'Show in Discovery' düyməsinin funksiyası: Bu seçim aktiv olduqda (açıq), tədbir bütün Bakı istifadəçiləri üçün ümumi Xəritədə və Discover siyahısında görünür. Seçim qeyri-aktiv olduqda (bağlı), tədbir gizli/şəxsi (private / invite-only) olur və yalnız xüsusi dəvət linki göndərdiyiniz şəxslər və ya dostlarınız baxa bilər."
    ),
    (
        "Çat, Mesajlaşma və Dostlar (Chatting, Group Chats & Direct Messages)",
        "Tədbir qrup çatına daxil olmaq üçün 'Joined' (Ürək ikonu) bölməsinə keçin və qoşulduğunuz tədbirin üzərinə toxunun - canlı qrup çatı dərhal açılacaq. Tədbirdə tanış olduğunuz insanlara dostluq göndərmək üçün tədbir səhifəsində və ya çatda iştirakçının profilinə toxunub 'Dostluq göndər' (Add Friend) sıxın. 'Friends' bölməsindən dostlar siyahısını görə və təkbətək şəxsi mesajlaşma (Direct Messages / DM) apara bilərsiniz. Qayda pozuntusu edən və ya narahat edən istifadəçiləri profillərindəki menyudan 'Şikayət et' (Report User) və ya 'Blokla' (Block User) edə bilərsiniz."
    ),
    (
        "Hesab Parametrləri, Texniki Dəstək və Hesabı Silmə (Profile, Support, Location & Account Deletion)",
        "Profil məlumatlarını (ad, soyad, yaş, cins, 300 simvolluq bio, şəkil, maraqlar) redaktə etmək üçün 'Profile' bölməsindən 'Edit Profile' seçin. Dəqiq canlı məkanın (GPS) paylaşılması: XEYR! MeeTTabb istifadəçilərin şəxsi GPS yerləşməsini heç vaxt paylaşmır, xəritədə yalnız tədbir məkanının pini görünür. Xəritə yüklənməsi xətalarını Profile > Parametrlər > 'Report a Bug' bölməsindən və ya çatbot vasitəsilə bildirə bilərsiniz. 'Delete Account' ifadəsi Azərbaycan dilində 'Hesabı silmək' deməkdir. Hesabı və şəxsi məlumatları həmişəlik silmək üçün: Profile > Parametrlər (dişli çarx ikonu) > 'Account Deletion' bölməsinə daxil olub qırmızı 'Delete Account' düyməsini sıxın. Bütün məlumatlarınız bərpa olunmaz şəkildə tamamilə silinir."
    ),
    (
        "Referral Proqramı və Tabb Coin (Referral Program & Rewards)",
        "MeeTTabb-da dostlarınızı dəvət edərək mükafatlar qazanmaq mümkündür! Şəxsi dəvət kodunuzla (referral link) qeydiyyatdan keçən hər dostunuza görə 1 Tabb Coin qazanırsınız. Tabb Coin-lərlə Marketplace bölməsində 150 ₼ uduş fonduna malik 1.000 istifadəçi yubileyi müsabiqəsinə bilet (1 coin) və ya 10 ₼ nağd mükafat (5 coins) əldə edə bilərsiniz."
    ),
    (
        "Veb-Brauzer İstifadəsi və Mobil Tətbiq Tövsiyəsi (Browser vs Mobile App)",
        "MeeTTabb platformasını birbaşa veb-brauzerdən (sayt üzərindən) də istifadə etmək mümkündür. İstifadəçilər yükləmə etmədən də fəaliyyətlərə baxa və qoşula bilərlər. Lakin canlı interaktiv xəritə, anlıq bildirişlər və ən yaxşı istifadəçi təcrübəsi üçün mobil tətbiqi yükləmək tövsiyə olunur. MeeTTabb mobil tətbiqi həm iOS (Apple App Store), həm də Android (Google Play Store) üçün tamamilə pulsuzdur."
    ),
    (
        "Bakı Populyar Aktivlik Məkanları və Qiymətləri (Venues & Prices)",
        "Bakıda ən populyar fəaliyyət məkanları və qiymətlər:\n- Kartinq: Baku Karting & Event Center (Böyükşor şossesi, 10 dəqiqə sürüş 15-20 AZN), GoKart Baku (Dəniz Mall, 15-25 AZN).\n- Bovlinq: 28 Mall Bowling (1 saat zolaq 20-30 AZN), Gənclik Mall CinemaPlus Bowling (saatı 25-35 AZN), Olympic Star (20-30 AZN).\n- Velosiped və Qaçış: Bakı Bulvarı velozolağı (icarə saatı 3-5 AZN), Şıxov Veloparkı.\n- İdman, Futbol və Fitnes: Tofiq Bəhrəmov adına Stadionun yardımçı meydançaları, Baku Sportinq, Sport City (meydança saatı 40-70 AZN, adambaşı 4-6 AZN), Dənizkənarı Bulvar boyunca açıq fitnes meydançaları.\n- Masaüstü Oyunlar: Baku Board Game Club və Second Home Community Cafe (Şahmat, Monopoly, Catan, Mafia gecələri, giriş 5-10 AZN).\n- Peyntbol: Baku Paintball və Xəzər Paintball (100 güllə ilə 15-25 AZN).\n- Kvestlər: Portal Games və Quest Baku (komanda üçün 50-80 AZN, adambaşı 12-20 AZN)."
    ),
]


def seed_knowledge():
    print("1. Connecting to database...")
    engine = get_engine()
    emb_client = get_embedding_client()
    pinecone_client = get_pinecone_client()

    with get_db_session(engine) as db:
        # Clear existing chunks for MeeTTabb
        existing_chunks = list(
            db.scalars(
                select(KnowledgeChunkORM).where(KnowledgeChunkORM.tenant_id == TENANT_ID)
            ).all()
        )
        print(f"Deleting {len(existing_chunks)} old chunks from SQL database...")
        for old_c in existing_chunks:
            db.delete(old_c)
        db.commit()

        # Add new chunks
        print(f"Adding {len(MEETTABB_CHUNKS)} semantic chunks to SQL database...")
        vector_records = []
        now_iso = datetime.now(timezone.utc).isoformat()

        for idx, (title, snippet) in enumerate(MEETTABB_CHUNKS):
            c_id = uuid4()
            vector = emb_client.embed(snippet)
            chunk_orm = KnowledgeChunkORM(
                chunk_id=c_id,
                tenant_id=TENANT_ID,
                document_title=title,
                content_snippet=snippet,
                vector_embedding=vector,
                similarity_score=1.0,
            )
            db.add(chunk_orm)

            vector_records.append({
                "id": str(c_id),
                "values": vector,
                "metadata": {
                    "tenant_id": str(TENANT_ID),
                    "document_title": title,
                    "chunk_text": snippet,
                    "content_snippet": snippet,
                    "chunk_index": idx,
                    "created_at": now_iso,
                },
            })
        db.commit()
        print("SQL knowledge chunks committed successfully.")

    # Upsert to Pinecone in tenant namespace
    print(f"Upserting {len(vector_records)} vectors to Pinecone namespace '{TENANT_ID}'...")
    try:
        pinecone_client.upsert(vectors=vector_records, namespace=str(TENANT_ID))
        print("Pinecone namespace successfully populated!")
    except Exception as e:
        print(f"Pinecone upsert error: {e}")

    # Also seed SQLite fallback chatbot.db if different engine
    try:
        import sqlite3
        con = sqlite3.connect("chatbot.db")
        cur = con.cursor()
        cur.execute("DELETE FROM knowledge_chunks WHERE tenant_id = ?", (str(TENANT_ID),))
        for idx, (title, snippet) in enumerate(MEETTABB_CHUNKS):
            c_id = str(uuid4())
            cur.execute(
                "INSERT INTO knowledge_chunks (chunk_id, tenant_id, document_title, content_snippet, similarity_score) VALUES (?, ?, ?, ?, ?)",
                (c_id, str(TENANT_ID), title, snippet, 1.0)
            )
        con.commit()
        con.close()
        print("SQLite chatbot.db populated successfully!")
    except Exception as sqle:
        print(f"SQLite local note: {sqle}")

    print("ALL KNOWLEDGE SEEDING COMPLETE!")


if __name__ == "__main__":
    seed_knowledge()
