"""End-to-End System Integration Test Entrypoint.

Aliases and re-exports test cases from test_e2e_pipeline.py for full architectural compliance.
"""

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
