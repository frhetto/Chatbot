"""Integration test suite module conforming to TEST_INFRA.md layout."""

from tests.test_e2e_pipeline import (
    TestAC1SystemHealthMonitoring,
    TestAC2InboundFacebookMessengerStandardMessageFlow,
    TestAC3InboundFacebookMessengerEscalationMessageFlow,
    TestAC4WhatsAppExpiredWindowTemplateRouting,
    TestAC5FacebookMessengerExpired24hInside7dWindowTagRouting,
    TestAdditionalE2EPipelineScenarios,
)

__all__ = [
    "TestAC1SystemHealthMonitoring",
    "TestAC2InboundFacebookMessengerStandardMessageFlow",
    "TestAC3InboundFacebookMessengerEscalationMessageFlow",
    "TestAC4WhatsAppExpiredWindowTemplateRouting",
    "TestAC5FacebookMessengerExpired24hInside7dWindowTagRouting",
    "TestAdditionalE2EPipelineScenarios",
]
