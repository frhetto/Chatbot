"""Meta AI Chatbot ASGI Application Factory & Main Entrypoint.

Implements Subsystem 1.0 through 8.0 assembly, application lifespan lifecycle
management (@asynccontextmanager), system health monitoring (GET /health),
and mounting boundary controllers:
- Meta Webhook Controller (/webhook)
- Merchant Admin Dashboard API (/api/v1/admin)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

from app.api.admin import admin_root_router, router as admin_router
from app.api.webhook import get_webhook_controller, router as webhook_router
from app.clients.pinecone_client import get_pinecone_client
from app.core.config import get_settings, settings
from app.core.database import close_db, get_engine, init_db
from app.core.redis import close_redis_client, get_redis_client
from app.services.queue import QueueProducer
from app.workers.queue_worker import get_queue_worker

logger = logging.getLogger("app.main")


class HealthCheckResponse(BaseModel):
    """Schema for system health probe responses."""

    status: str
    database: str
    redis: str
    pinecone: str


def _seed_default_tenant_and_catalog(pinecone_client: Any) -> None:
    """Ensure production MeeTTabb tenant and knowledge catalog are registered and indexed."""
    from uuid import UUID, uuid4
    from datetime import datetime, timezone
    from app.core.database import get_db_session
    from app.models.domain import BusinessProfileORM, KnowledgeChunkORM, ChatSessionORM
    from app.models.enums import SessionState
    from app.services.tenant_service import _derive_fernet_key
    from cryptography.fernet import Fernet
    from app.clients.embedding_client import get_embedding_client

    cfg = get_settings()
    tenant_id = UUID("444fc7ec-266e-4658-9ee2-6e5d5b8c0881")
    ig_id = "17841480465696096"
    raw_token = cfg.META_PAGE_ACCESS_TOKEN or ""

    encrypted_token = None
    if raw_token:
        try:
            cipher = Fernet(_derive_fernet_key(cfg.META_APP_SECRET or "default_secret"))
            encrypted_token = cipher.encrypt(raw_token.encode("utf-8")).decode("utf-8")
        except Exception:
            encrypted_token = raw_token

    catalog_text = (
        "MeeTTabb Tədbir və Aktivlik Platforması (MeeTTabb Event & Activity Companion Platform):\n\n"
        "1. MeeTTabb Tətbiqi haqqında (About MeeTTabb):\n"
        "MeeTTabb insanları virtual dünyadan və tək-tənha telefon sürüşdürməkdən çıxarıb, real həyatda bir araya gətirən müasir fəaliyyət və tədbir platformasıdır (Activity Companion App). "
        "Tətbiq vasitəsilə Bakıda 46-dan çox maraqlı tədbir və aktivlik kəşf edin, yeni dostlar tapın və ya öz tədbirlərinizi təşkil edin.\n\n"
        "2. Tətbiqin 5 Əsas Naviqasiya Bölməsi (5 Main Navigation Tabs):\n"
        "- 'Joined' (Qoşulduğum tədbirlər / Ürək ikonu): Qoşulduğunuz bütün aktivliklərin siyahısı. Tədbirin üzərinə toxunaraq birbaşa onun canlı qrup çatına daxil ola bilərsiniz.\n"
        "- 'Discover' (Kəşf et / Kompas ikonu): Bakıda 46+ aktivliyi canlı interaktiv Xəritə (Map) və ya Siyahı (List) görünüşündə araşdırın. 'For You' (maraqlarınıza uyğun), 'Soon' (tezliklə / bu həftəsonu) və 'New' (yeni) filtrləri mövcuddur.\n"
        "- 'Host' (Tədbir yarat / '+' ikonu): Bir neçə toxunuşla yeni tədbir təşkil edin (başlıq, 10 maraq kateqoriyası, xəritədə məkan seçimi, tarix/saat, iştirakçı limiti və 'Show in Discovery' görünürlük düyməsi).\n"
        "- 'Friends' (Dostlar / İnsanlar ikonu): Dostlar siyahısı, dostların hansı tədbirlərə qoşulduğunu görmək və dostlarla təkbətək birbaşa mesajlaşma (Direct Messages / DM).\n"
        "- 'Profile' (Profil / İstifadəçi ikonu): Profil şəkli, ad, soyad, yaş, cins, bio (maksimum 300 simvol), 10 Maraq kateqoriyası seçimi, şəxsi referral kodu və Tabb Coin Marketplace (150 ₼ uduş fondu olan giveaway biletləri və 10 ₼ nağd mükafatlar), Parametrlər (Bildirişlər, Şifrəni dəyiş, Bloklanmış istifadəçilər, Xəta bildirişi / Report a Bug və Hesabı silmə / Account Deletion).\n\n"
        "3. 10 Rəsmi Maraq Kateqoriyası (10 Official Interest Tags):\n"
        "Fitness, Outdoor (Açıq hava), Sports (İdman), Action (Adrenalin/Aksiya), Social (Sosial görüşlər), "
        "Tabletop Games (Masaüstü oyunlar), Adventure (Macəra), Video Games (Video oyunlar), Fun (Əyləncə), Wellness (Sağlamlıq).\n"
        "Bu maraqlar profilinizdə seçildikdə, Discover bölməsindəki 'For You' lenti sizə tam fərdiləşdirilmiş tövsiyələr təqdim edir.\n\n"
        "4. Tədbirləri Kəşf Etmək və Qoşulmaq (Discovering & Joining Activities):\n"
        "- İdman, oyun və açıq hava fəaliyyətlərini necə tapmaq olar: 'Discover' bölməsində Xəritə və ya Siyahı görünüşünə keçin, kateqoriyalardan və ya 'For You' lentindən istifadə edin.\n"
        "- Həftəsonu tədbirlərini xəritədə filtrləmək: Discover bölməsində 'Soon' filtr düyməsini sıxdıqda ən yaxın günlərdə və bu həftəsonu baş tutacaq bütün tədbirlər xəritədə göstərilir.\n"
        "- Ödəniş və Qiymət siyasəti: MeeTTabb tətbiqindən istifadə, qeydiyyat, tədbir yaratmaq və tədbirlərə qoşulmaq 100% PULSUZDUR! Platforma heç bir komissiya və ya abunə haqqı tələb etmir. Məkan xərcləri (kartinq, bovlinq, kafe, meydança icarəsi və s.) tədbir iştirakçıları tərəfindən birbaşa həmin məkana ödənilir.\n"
        "- Təşkilatçı və iştirakçıları öncədən görmək: Discover bölməsində istənilən tədbirə və ya xəritə pininə toxunduqda açılan təfərrüat səhifəsində təşkilatçının (Host) adı, profil şəkli və qoşulmuş bütün iştirakçıların siyahısı və sayı aydın görünür.\n"
        "- Tədbirə qoşulmaq və imtina etmək (Join & Cancel): Tədbirə qoşulduqda o, dərhal 'Joined' bölmənizə əlavə olunur və qrup çatına daxil olursunuz. Əgər planınız dəyişərsə, 'Joined' bölməsindən tədbirə daxil olub 'Tədbiri tərk et' (Leave / Cancel) düyməsinə toxunaraq heç bir cərimə olmadan çıxa bilərsiniz.\n\n"
        "5. Tədbir Yaratmaq və İdarə Etmək (Hosting & Managing Events):\n"
        "- Yeni tədbir yaratmaq: Aşağı menyudan 'Host' ('+') bölməsinə keçin, tədbirin adını yazın, 10 maraq kateqoriyasından uyğun olanları seçin, interaktiv xəritədə görüş məkanını qeyd edin, tarix və saatı təyin edin, iştirakçı limiti qoyun və 'Show in Discovery' seçimini edin.\n"
        "- 'Show in Discovery' düyməsinin funksiyası: Bu seçim aktiv olduqda (açıq), tədbir bütün Bakı istifadəçiləri üçün ümumi Xəritədə və Discover siyahısında görünür. Seçim qeyri-aktiv olduqda (bağlı), tədbir gizli/şəxsi olur və yalnız dəvət linki göndərdiyiniz şəxslər və ya dostlarınız baxa bilər.\n"
        "- Tədbiri yalnız dostlar üçün özəl (private) etmək: 'Show in Discovery' düyməsini söndürmək kifayətdir. Beləliklə, tədbir ictimai xəritədə çıxmır və ancaq dostlarınız üçün qapalı qalır.\n"
        "- Dərc edilmiş tədbirin məkanını, vaxtını və ya kateqoriyasını dəyişmək: Tədbir təşkilatçısı kimi 'Joined' və ya 'Profile' bölməsindən tədbirinizə daxil olub 'Redaktə et' (Edit Event / Qələm ikonu) seçərək məkan, vaxt və detalları istənilən vaxt yeniləyə bilərsiniz.\n"
        "- İştirakçı sayı limiti: Təşkilatçı istədiyi sayda limit qoya bilər (məsələn: mini-futbol üçün 10 nəfər, kartinq və ya masaüstü oyun üçün 4 nəfər) və ya limiti limitsiz saxlaya bilər. Limit dolduqda qeydiyyat avtomatik bağlanır.\n\n"
        "6. Əlaqə, Çat və Dostluq (Chatting & Connecting):\n"
        "- Tədbir qrup çatına necə daxil olmaq olar: Aşağı menyudan 'Joined' (Ürək ikonu) bölməsinə keçin və qoşulduğunuz tədbirin üzərinə toxunun. Dərhal həmin tədbirin canlı qrup çatı açılacaq.\n"
        "- Tədbirdə tanış olduğunuz insanlara dostluq göndərmək: Tədbir səhifəsində və ya qrup çatında istənilən iştirakçının profil şəklinə/adına toxunub 'Dostluq göndər' (Add Friend) düyməsini sıxmaq kifayətdir.\n"
        "- Dostlar siyahısı və birbaşa şəxsi mesajlar (Direct Messages): Aşağı menyudan 'Friends' bölməsinə daxil olun. Burada dostlarınızın siyahısını görə, onların hansı tədbirlərə qoşulduğunu izləyə və birbaşa 1-on-1 şəxsi mesajlaşma apara bilərsiniz.\n"
        "- Qayda pozuntusu edən və ya narahat edən istifadəçini şikayət etmək və bloklamaq: Çatda və ya iştirakçılar siyahısında istifadəçinin profilinə toxunub üç nöqtə menyusundan 'Şikayət et' (Report User) və ya 'Blokla' (Block User) seçin. Bloklanmış istifadəçilərə Profile > Parametrlər > Blocked Users bölməsindən baxa və bloku aça bilərsiniz.\n\n"
        "7. Hesab Parametrləri və Texniki Dəstək (Account Settings & Technical Support):\n"
        "- Profil məlumatlarını, şəkli və maraqları redaktə etmək: 'Profile' bölməsinə keçib 'Edit Profile' düyməsinə toxunun. Şəkil yükləyə, ad, soyad, yaş, cins, 300 simvolluq bio mətninizi yaza və 10 maraq kateqoriyası arasından seçimlərinizi yeniləyə bilərsiniz.\n"
        "- Xəritə yüklənməsi və ya texniki xətanı (Bug) bildirmək: 'Profile' bölməsində Parametrlər (dişli çarx ikonu) > 'Report a Bug' (Xəta bildir) bölməsindən və ya birbaşa bu çatbot vasitəsilə rəy göndərə bilərsiniz, komandamız dərhal qeydə alacaq.\n"
        "- Dəqiq canlı məkanın (GPS) paylaşılması: XEYR! MeeTTabb istifadəçilərin şəxsi dəqiq GPS yerləşməsini heç vaxt digər istifadəçilərlə paylaşmır və canlı izləmir. Xəritədə yalnız təşkilatçının tədbir üçün seçdiyi görüş nöqtəsinin pini görünür.\n"
        "- Hesabı və şəxsi məlumatları birdəfəlik silmək: 'Profile' > Parametrlər > 'Account Deletion' bölməsinə keçin və qırmızı 'Delete Account' düyməsini sıxın. Bu zaman hesabınız və bütün şəxsi məlumatlarınız sistemdən birdəfəlik və bərpa olunmaz şəkildə silinir.\n\n"
        "8. Referral Sistemi və Tabb Coin (Referral Program & Rewards):\n"
        "MeeTTabb-da dostlarınızı dəvət edərək mükafatlar qazanmaq mümkündür! Şəxsi dəvət linkinizlə (referral link) qeydiyyatdan keçən hər dostunuza görə 1 Tabb Coin qazanırsınız. "
        "Tabb Coin-lərlə Marketplace bölməsində 150 ₼ uduş fonduna malik 1.000 istifadəçi yubileyi müsabiqəsinə bilet (1 coin) və ya 10 ₼ nağd mükafat (5 coins) əldə edə bilərsiniz.\n\n"
        "9. Mobil Tətbiq və Veb-Brauzer Dəstəyi (Mobile App & Web Browser Access):\n"
        "MeeTTabb platformasını birbaşa veb-brauzerdən (sayt üzərindən) də istifadə etmək mümkündür. Lakin canlı interaktiv xəritə, bildirişlər və daha rahat istifadəçi təcrübəsi üçün mobil tətbiqi yükləmək tövsiyə olunur. "
        "MeeTTabb mobil tətbiqi həm iOS (Apple App Store), həm də Android (Google Play Store) cihazları üçün tamamilə pulsuzdur.\n\n"
        "10. Bakı Məşhur Aktivlik Məkanları və Qiymətləri:\n"
        "- Kartinq (Karting): Baku Karting & Event Center (Böyükşor şossesi, qapalı trek, 10 dəqiqə sürüş 15-20 AZN), GoKart Baku (Dəniz Mall əyləncə mərkəzi, sürüş qiyməti 15-25 AZN).\n"
        "- Bovlinq (Bowling): 28 Mall Bowling (28 May metrosu, zolaq icarəsi 1 saat 20-30 AZN), Gənclik Mall CinemaPlus Bowling (saatı 25-35 AZN), Olympic Star Bowling (Teymur Əliyev küçəsi, 20-30 AZN).\n"
        "- Velosiped (Cycling & Velo): Dənizkənarı Milli Park (Bakı Bulvarı velozolağı boyunca icarə saatı 3-5 AZN), Velopark Şıxov (açıq hava dağ və sürət yolları, münasib qiymətlər).\n"
        "- Masaüstü Oyunlar (Board Games): Baku Board Game Club və Second Home Community Cafe (Şahmat, Monopoly, Catan, Mafia gecələri, giriş və çay/kofe menyusu ilə adambaşı 5-10 AZN).\n"
        "- Futbol və Mini-Futbol: Tofiq Bəhrəmov adına Respublika Stadionunun yardımçı meydançaları, Baku Sportinq, Sport City (meydança icarəsi saatı 40-70 AZN, komandada adambaşı 4-6 AZN).\n"
        "- Peyntbol (Paintball): Baku Paintball Center (Koroğlu yaxınlığı) və Xəzər Paintball (100 güllə və təchizat daxil 15-25 AZN).\n"
        "- Kvestlər (Escape Rooms): Portal Games Baku və Quest Baku (komanda üçün 50-80 AZN, adambaşı 12-20 AZN).\n\n"
        "11. Əlaqə və Dəstək:\n"
        "MeeTTabb mobil tətbiqi 24/7 xidmətinizdədir. Əlaqə nömrəsi: +994501234567. Canlı insan dəstəyi üçün 'operator' və ya 'insan' tələb edə bilərsiniz."
    )

    try:
        with get_db_session() as db:
            tenant = db.get(BusinessProfileORM, tenant_id)
            if tenant is None:
                tenant = BusinessProfileORM(
                    tenant_id=tenant_id,
                    business_name="MeeTTabb",
                    business_category="Tədbir və Aktivlik Platforması / Activity Companion App",
                    city="Bakı",
                    phone_contact="+994501234567",
                    ig_business_id=ig_id,
                    fb_page_id=ig_id,
                    fb_page_access_token_encrypted=encrypted_token,
                    working_hours_json='{"mon-sun": "24/7 (Mobil Tətbiq)"}',
                    is_active=True,
                )
                db.add(tenant)
                db.commit()
            else:
                tenant.business_name = "MeeTTabb"
                tenant.business_category = "Tədbir və Aktivlik Platforması / Activity Companion App"
                tenant.city = "Bakı"
                tenant.phone_contact = "+994501234567"
                tenant.ig_business_id = ig_id
                tenant.fb_page_id = ig_id
                if encrypted_token:
                    tenant.fb_page_access_token_encrypted = encrypted_token
                tenant.working_hours_json = '{"mon-sun": "24/7 (Mobil Tətbiq)"}'
                db.commit()

            # Ensure clean catalog chunk exists in SQLite
            from sqlalchemy import select
            existing_chunks = list(
                db.scalars(
                    select(KnowledgeChunkORM).where(KnowledgeChunkORM.tenant_id == tenant_id)
                ).all()
            )
            for old_c in existing_chunks:
                db.delete(old_c)
            db.commit()

            chunk_orm = KnowledgeChunkORM(
                chunk_id=uuid4(),
                tenant_id=tenant_id,
                document_title="meettabb_knowledge.txt",
                content_snippet=catalog_text,
                similarity_score=1.0,
            )
            db.add(chunk_orm)
            db.commit()

            # Resume any paused sessions so live testing is active
            paused_sessions = list(
                db.scalars(
                    select(ChatSessionORM).where(ChatSessionORM.tenant_id == tenant_id)
                ).all()
            )
            for s in paused_sessions:
                s.status = SessionState.ACTIVE.value
            db.commit()

        # Clean old vectors and index fresh catalog into Pinecone
        try:
            pinecone_client.delete(delete_all=True)
        except Exception as pe:
            logger.debug("Pinecone delete_all note: %s", pe)

        emb_client = get_embedding_client()
        vector = emb_client.embed(catalog_text)
        pinecone_client.upsert(
            vectors=[{
                "id": "meettabb-catalog-1",
                "values": vector,
                "metadata": {
                    "tenant_id": str(tenant_id),
                    "document_title": "meettabb_knowledge.txt",
                    "chunk_text": catalog_text,
                    "content_snippet": catalog_text,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            }]
        )
        logger.info("Successfully verified and seeded MeeTTabb tenant profile and Pinecone catalog.")
    except Exception as exc:
        logger.warning("Default tenant seed warning: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and graceful shutdown lifecycles.

    Startup:
    1. Initialize configuration and settings.
    2. Initialize database connection pool and create schemas.
    3. Initialize Redis client (live Upstash or in-memory fallback).
    4. Initialize Pinecone vector client.
    5. Wire Webhook Controller with QueueProducer for rapid acknowledgment (< 500ms).
    6. Launch background QueueConsumerWorker event processing task loop.

    Shutdown:
    1. Gracefully stop QueueConsumerWorker task loop.
    2. Close Redis client connection pools.
    3. Dispose SQLAlchemy database engine connection pools.
    """
    cfg = get_settings()
    logger.info("Starting %s [%s]...", cfg.PROJECT_NAME, cfg.ENVIRONMENT)

    # 1. Database initialization
    init_db()

    # 2. Redis queue client initialization
    redis_client = await get_redis_client()

    # 3. Pinecone vector client initialization
    pinecone_client = get_pinecone_client()

    # 3b. Verify & seed AP Solutions business profile and catalog if in production/dev
    if cfg.ENVIRONMENT in ("production", "development"):
        _seed_default_tenant_and_catalog(pinecone_client)

    # 4. Wire webhook controller with queue producer for sub-500ms ack
    controller = get_webhook_controller()
    controller.queue_producer = QueueProducer(redis_client=redis_client)

    # 5. Start background queue consumer worker
    start_worker = getattr(app.state, "start_worker", True)
    worker = getattr(app.state, "queue_worker", None)
    if worker is None:
        worker = get_queue_worker(redis_client=redis_client)
        app.state.queue_worker = worker

    if start_worker:
        worker.start()
        logger.info("Background QueueConsumerWorker started.")

    app.state.redis_client = redis_client
    app.state.pinecone_client = pinecone_client

    yield

    # Shutdown sequence
    logger.info("Initiating graceful application shutdown...")
    if hasattr(app.state, "queue_worker") and app.state.queue_worker:
        await app.state.queue_worker.stop()
        logger.info("QueueConsumerWorker gracefully stopped.")

    controller = get_webhook_controller()
    controller.queue_producer = None

    await close_redis_client()
    close_db()
    logger.info("Application shutdown complete.")


def create_app(
    start_worker: bool = True,
    lifespan_override: Optional[Any] = None,
) -> FastAPI:
    """FastAPI application factory configuring routers, middleware, and lifespan.

    Args:
        start_worker: Whether to start background QueueConsumerWorker during lifespan.
        lifespan_override: Optional custom lifespan context manager.

    Returns:
        Configured FastAPI application instance.
    """
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version="1.0.0",
        description="Meta AI Chatbot for Azerbaijani Local Businesses (WhatsApp, Instagram, Messenger)",
        lifespan=lifespan_override or lifespan,
    )

    app.state.start_worker = start_worker

    # Enable CORS for admin dashboard web clients
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount boundary controllers
    app.include_router(webhook_router)
    app.include_router(admin_router)
    app.include_router(admin_root_router)

    # Root API Welcome Endpoint
    @app.get(
        "/",
        status_code=status.HTTP_200_OK,
        tags=["Root"],
        summary="Root API Welcome",
    )
    async def root() -> Dict[str, Any]:
        """Root API welcome endpoint returning system status and links."""
        return {
            "message": "Meta AI Chatbot API is online and operational.",
            "status": "healthy",
            "health_check": "/health",
            "api_docs": "/docs",
        }

    # System Health Check Endpoint (AC-1)
    @app.get(
        "/health",
        response_model=HealthCheckResponse,
        status_code=status.HTTP_200_OK,
        tags=["Health"],
        summary="System and Subsystem Health Monitoring Probe",
    )
    async def health_check() -> JSONResponse:
        """Probe operational status of Database, Redis broker, and Pinecone vector index.

        Returns HTTP 200 OK with:
        {"status": "healthy", "database": "up", "redis": "up", "pinecone": "up"}
        """
        db_status = "up"
        redis_status = "up"
        pinecone_status = "up"

        # 1. Database probe
        try:
            engine = get_engine()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:
            logger.warning("Database health check probe failed: %s", exc)
            db_status = "down"

        # 2. Redis broker probe
        try:
            r_client = await get_redis_client()
            is_ok = await r_client.ping()
            if not is_ok:
                redis_status = "down"
        except Exception as exc:
            logger.warning("Redis health check probe failed: %s", exc)
            redis_status = "down"

        # 3. Pinecone vector index probe
        try:
            p_client = get_pinecone_client()
            stats = p_client.describe_index_stats()
            if not isinstance(stats, dict):
                pinecone_status = "down"
        except Exception as exc:
            logger.warning("Pinecone health check probe failed: %s", exc)
            pinecone_status = "down"

        all_up = (db_status == "up" and redis_status == "up" and pinecone_status == "up")
        overall = "healthy" if all_up else "degraded"
        status_code = status.HTTP_200_OK if all_up else status.HTTP_503_SERVICE_UNAVAILABLE

        return JSONResponse(
            content={
                "status": overall,
                "database": db_status,
                "redis": redis_status,
                "pinecone": pinecone_status,
            },
            status_code=status_code,
        )

    return app


# Root ASGI Application Instance
app: FastAPI = create_app()
