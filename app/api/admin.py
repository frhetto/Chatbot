"""Admin Dashboard API endpoints and boundary controller.

Implements Subsystem 8.0 (Processes 8.1, 8.2, 8.3) and Boundary Stereotype AdminDashboardAPI.
Provides secure REST endpoints for Azerbaijani local merchants to:
- POST /api/v1/admin/tenants: Register or update business profiles.
- GET /api/v1/admin/tenants/{tenant_id}: Retrieve business profile.
- POST /api/v1/admin/tenants/{tenant_id}/facebook-credentials: Configure Facebook Page ID & Access Token.
- POST /api/v1/admin/tenants/{tenant_id}/catalog/upload: Ingest and index product menus/catalogs.
- GET /api/v1/admin/sessions/{session_id}/transcript: Retrieve full conversation message transcripts.
- POST /api/v1/admin/tickets/{ticket_id}/resolve: Resolve human escalation tickets.
- GET /api/v1/admin/tenants/{tenant_id}/analytics: Retrieve multi-channel performance metrics.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field

from app.models.domain import BusinessProfile, ChatMessage, EscalationTicket
from app.services.tenant_service import (
    TenantAdminService,
    get_tenant_admin_service,
)

logger = logging.getLogger("app.api.admin")


# ============================================================================
# Request / Response Schemas for Admin REST Endpoints
# ============================================================================


class CreateTenantRequest(BaseModel):
    """Payload schema for business tenant profile registration."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    tenant_id: Optional[UUID] = Field(default=None, alias="tenantId")
    business_name: str = Field(alias="businessName")
    business_category: str = Field(alias="businessCategory")
    city: str = Field(default="Bakı")
    phone_contact: str = Field(alias="phoneContact")
    meta_waba_id: Optional[str] = Field(default=None, alias="metaWabaId")
    meta_phone_number_id: Optional[str] = Field(default=None, alias="metaPhoneNumberId")
    fb_page_id: Optional[str] = Field(default=None, alias="fbPageId")
    fb_page_access_token_encrypted: Optional[str] = Field(
        default=None,
        alias="fbPageAccessTokenEncrypted",
    )
    ig_business_id: Optional[str] = Field(default=None, alias="igBusinessId")
    working_hours_json: Optional[str] = Field(default=None, alias="workingHoursJson")
    is_active: bool = Field(default=True, alias="isActive")


class FacebookCredentialsRequest(BaseModel):
    """Payload schema for configuring Facebook Page credentials."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    page_id: Optional[str] = Field(default=None, alias="pageId")
    page_access_token: Optional[str] = Field(default=None, alias="pageAccessToken")
    fb_page_id: Optional[str] = Field(default=None, alias="fbPageId")
    fb_page_access_token: Optional[str] = Field(default=None, alias="fbPageAccessToken")

    def get_page_id(self) -> str:
        val = self.page_id or self.fb_page_id
        if not val or not str(val).strip():
            raise ValueError("Facebook Page ID ('page_id' or 'pageId') is required.")
        return str(val).strip()

    def get_page_access_token(self) -> str:
        val = self.page_access_token or self.fb_page_access_token
        if not val or not str(val).strip():
            raise ValueError("Facebook Page Access Token ('page_access_token') is required.")
        return str(val).strip()


class ResolveTicketRequest(BaseModel):
    """Payload schema for resolving an escalation ticket."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    notes: Optional[str] = None
    manager_name: Optional[str] = Field(default=None, alias="managerName")


# ============================================================================
# FastAPI APIRouter Definition
# ============================================================================

def get_admin_service() -> TenantAdminService:
    """FastAPI dependency provider returning the TenantAdminService singleton."""
    return get_tenant_admin_service()


router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
admin_root_router = APIRouter(prefix="/admin", tags=["admin"])


@router.post(
    "/tenants",
    response_model=BusinessProfile,
    status_code=status.HTTP_201_CREATED,
    summary="Create or update business tenant profile",
)
def create_tenant_route(
    payload: CreateTenantRequest,
    service: TenantAdminService = Depends(get_admin_service),
) -> BusinessProfile:
    """Register or update a merchant business profile in Store D1."""
    return service.create_or_update_business_profile(
        tenant_id=payload.tenant_id,
        data=payload.model_dump(by_alias=False),
    )


@router.get(
    "/tenants/{tenant_id}",
    response_model=BusinessProfile,
    status_code=status.HTTP_200_OK,
    summary="Retrieve business tenant profile",
)
def get_tenant_route(
    tenant_id: UUID,
    service: TenantAdminService = Depends(get_admin_service),
) -> BusinessProfile:
    """Retrieve merchant profile details by tenant UUID."""
    profile = service.get_business_profile(tenant_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Business tenant '{tenant_id}' not found.",
        )
    return profile


@router.post(
    "/tenants/{tenant_id}/facebook-credentials",
    status_code=status.HTTP_200_OK,
    summary="Configure Facebook Page ID & Access Token",
)
def configure_facebook_credentials_route(
    tenant_id: UUID,
    payload: FacebookCredentialsRequest,
    service: TenantAdminService = Depends(get_admin_service),
) -> Dict[str, Any]:
    """Store and encrypt Facebook Page credentials for Messenger messaging."""
    try:
        page_id = payload.get_page_id()
        page_access_token = payload.get_page_access_token()
    except ValueError as val_err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(val_err),
        )

    try:
        service.configure_facebook_page_credentials(
            tenant_id=tenant_id,
            page_id=page_id,
            page_access_token=page_access_token,
        )
        return {
            "status": "success",
            "tenant_id": str(tenant_id),
            "fb_page_id": page_id,
        }
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )


@router.post(
    "/tenants/{tenant_id}/catalog/upload",
    status_code=status.HTTP_200_OK,
    summary="Upload product catalog or menu file",
)
async def upload_catalog_route(
    tenant_id: UUID,
    request: Request,
    service: TenantAdminService = Depends(get_admin_service),
) -> Dict[str, Any]:
    """Ingest menu, price list, or FAQ file (.txt, .md, .json, .csv, .pdf) and index embeddings in Pinecone."""
    content_type = request.headers.get("content-type", "")
    filename = request.query_params.get("filename", "catalog.txt")
    file_type: Optional[str] = request.query_params.get("file_type")
    raw_bytes = b""

    body = await request.body()

    if "multipart/form-data" in content_type:
        from email.parser import BytesParser
        from email.policy import default

        header_prefix = f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
        msg = BytesParser(policy=default).parsebytes(header_prefix + body)
        if msg.is_multipart():
            for part in msg.iter_parts():
                part_name = part.get_param("name", header="content-disposition")
                part_filename = part.get_filename()
                if part_filename or part_name == "file":
                    if part_filename:
                        filename = part_filename
                    payload_data = part.get_payload(decode=True)
                    raw_bytes = bytes(payload_data) if payload_data else b""
                    break
        else:
            payload_data = msg.get_payload(decode=True)
            raw_bytes = bytes(payload_data) if payload_data else b""
            if msg.get_filename():
                filename = msg.get_filename()
        if not file_type and "." in filename:
            file_type = Path(filename).suffix.lstrip(".")
    elif "application/json" in content_type:
        try:
            import json
            body_json = json.loads(body.decode("utf-8", errors="replace")) if body else {}
        except Exception:
            body_json = {}
        filename = body_json.get("filename", filename)
        file_type = body_json.get("file_type") or body_json.get("fileType") or file_type
        content_val = body_json.get("content", "")
        if isinstance(content_val, str):
            try:
                raw_bytes = base64.b64decode(content_val, validate=True)
            except Exception:
                raw_bytes = content_val.encode("utf-8")
        elif isinstance(content_val, bytes):
            raw_bytes = content_val
        else:
            raw_bytes = str(content_val).encode("utf-8")
    else:
        # Plain text, octet-stream, or direct byte stream
        raw_bytes = body
        if not file_type and "." in filename:
            file_type = Path(filename).suffix.lstrip(".")

    try:
        return service.upload_catalog_document(
            tenant_id=tenant_id,
            filename=filename,
            content=raw_bytes,
            file_type=file_type,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@admin_root_router.post(
    "/tenants/{tenant_id}/catalog/upload",
    status_code=status.HTTP_200_OK,
    summary="Upload product catalog or menu file (root admin alias)",
)
async def upload_catalog_root_route(
    tenant_id: UUID,
    request: Request,
    service: TenantAdminService = Depends(get_admin_service),
) -> Dict[str, Any]:
    """Ingest menu, price list, or FAQ file (.txt, .md, .json, .csv, .pdf) and index embeddings in Pinecone."""
    return await upload_catalog_route(tenant_id=tenant_id, request=request, service=service)


@router.get(
    "/sessions/{session_id}/transcript",
    response_model=List[ChatMessage],
    status_code=status.HTTP_200_OK,
    summary="Retrieve full conversation message transcript",
)
def get_transcript_route(
    session_id: UUID,
    service: TenantAdminService = Depends(get_admin_service),
) -> List[ChatMessage]:
    """Fetch complete immutable conversation history from Store D4 in chronological order."""
    return service.get_conversation_transcript(session_id)


@router.post(
    "/tickets/{ticket_id}/resolve",
    response_model=EscalationTicket,
    status_code=status.HTTP_200_OK,
    summary="Mark human escalation ticket resolved",
)
def resolve_ticket_route(
    ticket_id: UUID,
    payload: Optional[ResolveTicketRequest] = None,
    service: TenantAdminService = Depends(get_admin_service),
) -> EscalationTicket:
    """Transition escalation ticket to RESOLVED, recording resolution timestamp and manager notes."""
    notes = payload.notes if payload else None
    manager_name = payload.manager_name if payload else None
    try:
        return service.resolve_escalation_ticket(
            ticket_id=ticket_id,
            notes=notes,
            manager_name=manager_name,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )


@router.get(
    "/tenants/{tenant_id}/analytics",
    status_code=status.HTTP_200_OK,
    summary="Retrieve aggregated multi-channel performance KPIs",
)
def get_analytics_route(
    tenant_id: UUID,
    service: TenantAdminService = Depends(get_admin_service),
) -> Dict[str, Any]:
    """Retrieve message volume partitioned by channel, escalation rate, and token usage."""
    try:
        return service.get_tenant_analytics(tenant_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )


# ============================================================================
# Boundary Class: AdminDashboardAPI (Stereotype Boundary)
# ============================================================================


class AdminDashboardAPI:
    """Boundary Stereotype representing the Merchant Admin Dashboard API (Subsystem 8.0).

    Exposes direct programmatic methods conforming to UML 2.5 class diagrams
    and provides access to underlying TenantAdminService controller.
    """

    def __init__(self, tenant_service: Optional[TenantAdminService] = None) -> None:
        self.tenant_service: TenantAdminService = tenant_service or get_tenant_admin_service()

    @property
    def tenantService(self) -> TenantAdminService:
        return self.tenant_service

    def create_or_update_business_profile(
        self,
        tenant_id: Optional[Union[UUID, str]] = None,
        data: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> BusinessProfile:
        return self.tenant_service.create_or_update_business_profile(tenant_id, data, **kwargs)

    def get_business_profile(self, tenant_id: Union[UUID, str]) -> Optional[BusinessProfile]:
        return self.tenant_service.get_business_profile(tenant_id)

    def configure_facebook_page_credentials(
        self,
        tenant_id: Union[UUID, str],
        page_id: str,
        page_access_token: str,
    ) -> bool:
        return self.tenant_service.configure_facebook_page_credentials(tenant_id, page_id, page_access_token)

    def upload_catalog_document(
        self,
        tenant_id: Union[UUID, str],
        filename: str,
        content: bytes,
        file_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.tenant_service.upload_catalog_document(tenant_id, filename, content, file_type)

    def get_conversation_transcript(self, session_id: Union[UUID, str]) -> List[ChatMessage]:
        return self.tenant_service.get_conversation_transcript(session_id)

    def resolve_escalation_ticket(
        self,
        ticket_id: Union[UUID, str],
        notes: Optional[str] = None,
        manager_name: Optional[str] = None,
    ) -> EscalationTicket:
        return self.tenant_service.resolve_escalation_ticket(ticket_id, notes, manager_name)

    def get_tenant_analytics(self, tenant_id: Union[UUID, str]) -> Dict[str, Any]:
        return self.tenant_service.get_tenant_analytics(tenant_id)

    # UML 2.5 CamelCase Aliases
    createOrUpdateBusinessProfile = create_or_update_business_profile
    getBusinessProfile = get_business_profile
    configureFacebookPageCredentials = configure_facebook_page_credentials
    uploadCatalogDocument = upload_catalog_document
    getConversationTranscript = get_conversation_transcript
    resolveEscalationTicket = resolve_escalation_ticket
    getTenantAnalytics = get_tenant_analytics


_GLOBAL_ADMIN_DASHBOARD_API: Optional[AdminDashboardAPI] = None


def get_admin_dashboard_api(
    tenant_service: Optional[TenantAdminService] = None,
) -> AdminDashboardAPI:
    """Return configured instance of AdminDashboardAPI."""
    global _GLOBAL_ADMIN_DASHBOARD_API
    if tenant_service is not None:
        return AdminDashboardAPI(tenant_service=tenant_service)
    if _GLOBAL_ADMIN_DASHBOARD_API is None:
        _GLOBAL_ADMIN_DASHBOARD_API = AdminDashboardAPI()
    return _GLOBAL_ADMIN_DASHBOARD_API


def reset_admin_dashboard_api() -> None:
    """Reset the global AdminDashboardAPI singleton."""
    global _GLOBAL_ADMIN_DASHBOARD_API
    _GLOBAL_ADMIN_DASHBOARD_API = None
