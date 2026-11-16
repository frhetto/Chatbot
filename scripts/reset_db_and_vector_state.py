"""MeeTTabb Database and Vector State Reset Script.

Executes complete purge of live Pinecone index and resets SQLite relational database
tables without creating any backup files. Prepares schema and seeds MeeTTabb tenant
and knowledge base.
"""

import os
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4
from datetime import datetime, timezone
import dotenv
from sqlalchemy import create_engine

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env
dotenv.load_dotenv(PROJECT_ROOT / ".env")

from app.core.config import get_settings
from app.models.domain import Base, BusinessProfileORM, KnowledgeChunkORM, ChatSessionORM
from app.models.enums import SessionState
from app.clients.embedding_client import get_embedding_client


MEETTABB_TENANT_ID = UUID("444fc7ec-266e-4658-9ee2-6e5d5b8c0881")
MEETTABB_BUSINESS_NAME = "MeeTTabb"
MEETTABB_CATEGORY = "Tədbir və Aktivlik Platforması / Activity Companion App"
MEETTABB_CITY = "Bakı"
MEETTABB_PHONE = "+994501234567"
MEETTABB_IG_ID = "17841480465696096"
MEETTABB_HOURS = '{"mon-sun": "24/7 (Mobil Tətbiq)"}'

MEETTABB_CATALOG_CONTENT = (
    "MeeTTabb Tədbir və Aktivlik Platforması:\n"
    "1. MeeTTabb Tətbiqi haqqında - Bakıda insanları virtual dünyadan və tək-tənha telefon sürüşdürməkdən çıxarıb, "
    "real həyatda bir araya gətirən müasir fəaliyyət və tədbir platformasıdır (Activity Companion App). "
    "Tətbiq vasitəsilə maraqlı tədbirlər və aktivliklər kəşf edin, yeni dostlar və yoldaşlar tapın.\n"
    "2. Tədbir Yaratmaq və Dostları Dəvət Etmək - MeeTTabb-da bir neçə toxunuşla kartinq, bovlinq, velosiped gəzintisi, "
    "futbol matçı, masaüstü oyunlar (şahmat, monopoly, mafia), peyntbol və ya kvest tədbirləri yaradın. "
    "Dostlarınızı tətbiqdaxili dəvət linki ilə çağırın və ya komandanıza çatışmayan iştirakçıları cəlb edin.\n"
    "3. Tətbiqdaxili Çat - Hər bir tədbirin xüsusi qrup çatı var. İştirakçılar görüş yerini, vaxtı və detalları rahatlıqla müzakirə edə bilərlər.\n"
    "4. Mobil Tətbiq Yükləmə - MeeTTabb həm iOS (Apple App Store), həm də Android (Google Play Store) cihazları üçün tamamilə pulsuzdur. "
    "İndi tətbiqi yükləyin, ilk tədbirinizi yaradın və dostlarınızı dəvət edin!\n\n"
    "Bakı Məşhur Aktivlik Məkanları və Qiymətləri:\n"
    "- Kartinq (Karting): Baku Karting & Event Center (Böyükşor şossesi, qapalı trek, 10 dəqiqə sürüş 15-20 AZN), "
    "GoKart Baku (Dəniz Mall əyləncə mərkəzi, sürüş qiyməti 15-25 AZN).\n"
    "- Bovlinq (Bowling): 28 Mall Bowling (28 May metrosu, zolaq icarəsi 1 saat 20-30 AZN), "
    "Gənclik Mall CinemaPlus Bowling (saatı 25-35 AZN), Olympic Star Bowling (Teymur Əliyev küçəsi, 20-30 AZN).\n"
    "- Velosiped (Cycling & Velo): Dənizkənarı Milli Park (Bakı Bulvarı velozolağı boyunca icarə saatı 3-5 AZN), "
    "Velopark Şıxov (açıq hava dağ və sürət yolları, münasib qiymətlər).\n"
    "- Masaüstü Oyunlar (Board Games): Baku Board Game Club və Second Home Community Cafe (Şahmat, Monopoly, Catan, Mafia gecələri, "
    "giriş və çay/kofe menyusu ilə adambaşı 5-10 AZN).\n"
    "- Futbol və Mini-Futbol: Tofiq Bəhrəmov adına Respublika Stadionunun yardımçı meydançaları, Baku Sportinq, Sport City "
    "(meydança icarəsi saatı 40-70 AZN, komandada adambaşı 4-6 AZN). Çatışmayan oyunçuları MeeTTabb ilə yığın.\n"
    "- Peyntbol (Paintball): Baku Paintball Center (Koroğlu yaxınlığı) və Xəzər Paintball (100 güllə və təchizat daxil 15-25 AZN).\n"
    "- Kvestlər (Escape Rooms): Portal Games Baku və Quest Baku (komanda üçün 50-80 AZN, adambaşı 12-20 AZN).\n\n"
    "Əlaqə və Dəstək: MeeTTabb mobil tətbiqi 24/7 xidmətinizdədir. Əlaqə nömrəsi: +994501234567. "
    "Canlı insan dəstəyi üçün 'operator' və ya 'insan' tələb edə bilərsiniz."
)


def purge_live_pinecone() -> None:
    """Flush the existing Pinecone vector namespace and verify."""
    cfg = get_settings()
    api_key = cfg.PINECONE_API_KEY or os.getenv("PINECONE_API_KEY")
    index_name = cfg.PINECONE_INDEX_NAME or os.getenv("PINECONE_INDEX_NAME", "quickstart")

    if not api_key or not api_key.startswith("pcsk_"):
        print("[Pinecone] No live Pinecone API key configured, skipping cloud purge.")
        return

    print(f"[Pinecone] Connecting to Pinecone index: '{index_name}'...")
    try:
        from pinecone import Pinecone
        pc = Pinecone(api_key=api_key)
        index = pc.Index(index_name)

        # Inspect initial stats
        initial_stats = index.describe_index_stats()
        print(f"[Pinecone] Initial vector stats: total_vector_count={initial_stats.total_vector_count}")

        # Purge default namespace
        print("[Pinecone] Purging default namespace ('')...")
        index.delete(delete_all=True, namespace="")

        # Check for any named namespaces and purge them as well
        if hasattr(initial_stats, "namespaces") and initial_stats.namespaces:
            for ns in initial_stats.namespaces.keys():
                if ns and ns != "__default__":
                    print(f"[Pinecone] Purging namespace: '{ns}'...")
                    index.delete(delete_all=True, namespace=ns)

        # Wait briefly for cloud propagation
        time.sleep(2)
        final_stats = index.describe_index_stats()
        print(f"[Pinecone] Post-purge vector count: {final_stats.total_vector_count}")
        print("[Pinecone] Vector index purge completed successfully.")
    except Exception as exc:
        print(f"[Pinecone] Warning/Error during Pinecone purge: {exc}")


def reset_relational_database() -> None:
    """Connect to SQLite chatbot.db directly and drop/recreate all tables without backups."""
    cfg = get_settings()
    db_url = cfg.DATABASE_URL
    clean_url = db_url.replace("+aiosqlite", "")
    print(f"[Database] Connecting to SQLite via engine: {clean_url}...")

    engine = create_engine(clean_url, connect_args={"check_same_thread": False})

    print("[Database] Dropping all tables directly without backup files...")
    Base.metadata.drop_all(engine)

    print("[Database] Re-creating all tables from schema...")
    Base.metadata.create_all(engine)

    print("[Database] Database reset complete.")


def seed_meettabb_state() -> None:
    """Seed MeeTTabb tenant profile and knowledge chunks into SQLite and Pinecone."""
    cfg = get_settings()
    db_url = cfg.DATABASE_URL.replace("+aiosqlite", "")
    engine = create_engine(db_url, connect_args={"check_same_thread": False})

    print(f"[Seeding] Seeding MeeTTabb tenant ({MEETTABB_TENANT_ID})...")
    from sqlalchemy.orm import Session
    with Session(engine) as session:
        tenant = BusinessProfileORM(
            tenant_id=MEETTABB_TENANT_ID,
            business_name=MEETTABB_BUSINESS_NAME,
            business_category=MEETTABB_CATEGORY,
            city=MEETTABB_CITY,
            phone_contact=MEETTABB_PHONE,
            ig_business_id=MEETTABB_IG_ID,
            fb_page_id=MEETTABB_IG_ID,
            working_hours_json=MEETTABB_HOURS,
            is_active=True,
        )
        session.add(tenant)

        chunk_orm = KnowledgeChunkORM(
            chunk_id=uuid4(),
            tenant_id=MEETTABB_TENANT_ID,
            document_title="meettabb_knowledge.txt",
            content_snippet=MEETTABB_CATALOG_CONTENT,
            similarity_score=1.0,
        )
        session.add(chunk_orm)
        session.commit()
        print("[Seeding] MeeTTabb tenant and knowledge chunk saved to SQLite.")

    # Upsert vector into live Pinecone if configured
    api_key = cfg.PINECONE_API_KEY or os.getenv("PINECONE_API_KEY")
    index_name = cfg.PINECONE_INDEX_NAME or os.getenv("PINECONE_INDEX_NAME", "quickstart")
    if api_key and api_key.startswith("pcsk_"):
        try:
            print("[Seeding] Upserting MeeTTabb embedding to live Pinecone index...")
            from pinecone import Pinecone
            pc = Pinecone(api_key=api_key)
            index = pc.Index(index_name)

            emb_client = get_embedding_client()
            vector = emb_client.embed(MEETTABB_CATALOG_CONTENT)
            chunk_id = str(uuid4())
            index.upsert(
                vectors=[{
                    "id": chunk_id,
                    "values": vector,
                    "metadata": {
                        "tenant_id": str(MEETTABB_TENANT_ID),
                        "document_title": "meettabb_knowledge.txt",
                        "chunk_text": MEETTABB_CATALOG_CONTENT,
                        "content_snippet": MEETTABB_CATALOG_CONTENT,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                }],
                namespace="",
            )
            print("[Seeding] MeeTTabb knowledge embedding upserted to Pinecone.")
        except Exception as exc:
            print(f"[Seeding] Pinecone upsert note: {exc}")


def main() -> None:
    print("=" * 60)
    print("MeeTTabb Database & Vector State Reset Execution")
    print("=" * 60)
    purge_live_pinecone()
    reset_relational_database()
    seed_meettabb_state()
    print("=" * 60)
    print("Reset and MeeTTabb Seeding Completed Successfully.")
    print("=" * 60)


if __name__ == "__main__":
    main()
