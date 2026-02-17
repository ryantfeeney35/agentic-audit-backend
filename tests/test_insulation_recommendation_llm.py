"""
Live LLM tests for insulation recommendation generation.

These tests interact with the actual OpenAI API to validate that:
1. Good insulation images do NOT produce recommendations
2. Missing/poor insulation images DO produce recommendations aligned with service_catalog

Requires OPENAI_API_KEY environment variable to be set.

Run with: pytest backend/tests/test_insulation_recommendation_llm.py -v -s
"""
import os
import base64
import pytest
from pathlib import Path

# Check for API key before attempting imports that initialize the LLM
_has_api_key = bool(os.environ.get("OPENAI_API_KEY"))

# Skip all tests if OPENAI_API_KEY is not set
pytestmark = [
    pytest.mark.skipif(
        not _has_api_key,
        reason="OPENAI_API_KEY not set - skipping live LLM tests"
    ),
    pytest.mark.recommendation,  # Run with: pytest -m recommendation
]

# Only import the agent module if we have an API key (to avoid initialization errors)
if _has_api_key:
    from agents.base_agent import run_agent
else:
    run_agent = None  # Will be skipped anyway


# Path to test images
IMAGES_DIR = Path(__file__).parent / "images"


def load_image_as_b64(filename: str) -> str:
    """Load an image file and return base64-encoded string."""
    image_path = IMAGES_DIR / filename
    if not image_path.exists():
        raise FileNotFoundError(f"Test image not found: {image_path}")
    
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


class TestInsulationRecommendationGeneration:
    """Tests for insulation recommendation generation using live LLM calls."""

    def test_good_insulation_no_recommendation(self):
        """
        When good quality insulation is visible, the LLM should NOT generate
        any upgrade recommendations since there's no deficiency.
        """
        # Load the good insulation image
        b64_image = load_image_as_b64("good_insulation.jpeg")
        
        # Run the insulation agent in media mode
        context = {
            "type": "image",
            "b64": b64_image,
        }
        
        result = run_agent(
            domain="insulation",
            context=context,
            mode="media",
        )
        
        # Verify parsing succeeded (no error key or error is None)
        assert "error" not in result or result.get("error") is None, (
            f"Agent returned an error: {result.get('error')}"
        )
        
        # The InsulationSchema should be returned
        assert "insulation_type" in result, "Expected insulation_type in response"
        assert "condition" in result, "Expected condition in response"
        assert "summary" in result, "Expected summary in response"
        
        # Key assertion: recommended_upgrades should be empty for good insulation
        recommended_upgrades = result.get("recommended_upgrades", [])
        assert isinstance(recommended_upgrades, list), (
            "recommended_upgrades should be a list"
        )
        assert len(recommended_upgrades) == 0, (
            f"Expected no recommendations for good insulation, but got: {recommended_upgrades}"
        )
        
        # Condition should indicate good quality (not poor/damaged/missing)
        condition = result.get("condition", "").lower()
        assert any(word in condition for word in ["good", "adequate", "well", "sufficient", "intact"]) or \
               not any(word in condition for word in ["poor", "damaged", "missing", "deteriorat", "inadequate"]), (
            f"Expected good condition assessment, got: {result.get('condition')}"
        )
        
        print(f"\n✓ Good insulation test passed")
        print(f"  Insulation type: {result.get('insulation_type')}")
        print(f"  Thickness: {result.get('thickness_inches')}")
        print(f"  Condition: {result.get('condition')}")
        print(f"  Summary: {result.get('summary')[:200]}...")

    def test_no_insulation_generates_recommendation(self):
        """
        When no insulation (or poor/missing insulation) is visible, the LLM
        should generate a recommendation that aligns with 'home-efficiency-insulation-attic-install'.
        """
        # Load the no insulation image
        b64_image = load_image_as_b64("no_insulation.jpeg")
        
        # Run the insulation agent in media mode
        context = {
            "type": "image",
            "b64": b64_image,
        }
        
        result = run_agent(
            domain="insulation",
            context=context,
            mode="media",
        )
        
        # Verify parsing succeeded
        assert "error" not in result or result.get("error") is None, (
            f"Agent returned an error: {result.get('error')}"
        )
        
        # The InsulationSchema should be returned
        assert "insulation_type" in result, "Expected insulation_type in response"
        assert "condition" in result, "Expected condition in response"
        assert "summary" in result, "Expected summary in response"
        
        # Key assertion: recommended_upgrades should NOT be empty
        recommended_upgrades = result.get("recommended_upgrades", [])
        assert isinstance(recommended_upgrades, list), (
            "recommended_upgrades should be a list"
        )
        assert len(recommended_upgrades) > 0, (
            "Expected at least one recommendation for missing/no insulation"
        )
        
        # Validate the recommendation aligns with service catalog
        # The service 'home-efficiency-insulation-attic-install' has keywords:
        # ["attic insulation", "install attic", "add attic insulation", "attic insulation installation"]
        service_keywords = [
            "attic insulation",
            "install",
            "add insulation",
            "insulation installation",
            "insulate",
            "blown-in",
            "fiberglass",
            "cellulose",
        ]
        
        # At least one recommendation should contain relevant keywords
        recommendation_text = " ".join(recommended_upgrades).lower()
        has_relevant_keyword = any(
            keyword.lower() in recommendation_text 
            for keyword in service_keywords
        )
        
        assert has_relevant_keyword, (
            f"Expected recommendation to align with insulation installation service, "
            f"got: {recommended_upgrades}"
        )
        
        # Condition or issues should indicate deficiency
        condition = result.get("condition", "").lower()
        issues = result.get("issues", [])
        issues_text = " ".join(issues).lower() if issues else ""
        
        deficiency_indicators = [
            "missing", "none", "no insulation", "bare", "uninsulated",
            "poor", "inadequate", "insufficient", "minimal", "deteriorat"
        ]
        
        has_deficiency_indicator = (
            any(ind in condition for ind in deficiency_indicators) or
            any(ind in issues_text for ind in deficiency_indicators) or
            any(ind in result.get("summary", "").lower() for ind in deficiency_indicators)
        )
        
        assert has_deficiency_indicator, (
            f"Expected deficiency to be noted in condition, issues, or summary. "
            f"Condition: {result.get('condition')}, Issues: {issues}, "
            f"Summary: {result.get('summary')[:200]}"
        )
        
        print(f"\n✓ No insulation test passed")
        print(f"  Insulation type: {result.get('insulation_type')}")
        print(f"  Condition: {result.get('condition')}")
        print(f"  Issues: {result.get('issues')}")
        print(f"  Recommendations: {recommended_upgrades}")
        print(f"  Summary: {result.get('summary')[:200]}...")


class TestInsulationRecommendationModeGeneration:
    """
    Tests for insulation recommendation generation using the 'recommendations' mode
    which outputs the AgentOutput schema with service_id alignment.
    """

    def test_no_insulation_recommendation_mode_service_alignment(self):
        """
        Using recommendations mode with context about missing insulation
        should generate recommendations that can be mapped to service catalog IDs.
        """
        # First, get the media analysis
        b64_image = load_image_as_b64("no_insulation.jpeg")
        
        media_context = {
            "type": "image",
            "b64": b64_image,
        }
        
        media_result = run_agent(
            domain="insulation",
            context=media_context,
            mode="media",
        )
        
        # Now use the media result as context for recommendations mode
        # Build a context string from the media analysis
        context_summary = (
            f"Insulation assessment findings:\n"
            f"- Type: {media_result.get('insulation_type', 'unknown')}\n"
            f"- Condition: {media_result.get('condition', 'unknown')}\n"
            f"- Thickness: {media_result.get('thickness_inches', 'unknown')} inches\n"
            f"- Issues: {', '.join(media_result.get('issues', []))}\n"
            f"- Summary: {media_result.get('summary', '')}\n"
        )
        
        rec_result = run_agent(
            domain="insulation",
            context=context_summary,
            mode="recommendations",
        )
        
        # Verify parsing succeeded
        assert "error" not in rec_result or rec_result.get("error") is None, (
            f"Agent returned an error: {rec_result.get('error')}"
        )
        
        # Check recommendations exist
        recommendations = rec_result.get("recommendations", [])
        assert isinstance(recommendations, list), "recommendations should be a list"
        assert len(recommendations) > 0, (
            "Expected at least one recommendation for missing insulation context"
        )
        
        # Each recommendation should have proper structure
        for rec in recommendations:
            assert "summary" in rec, f"Recommendation missing summary: {rec}"
            assert "step_type" in rec, f"Recommendation missing step_type: {rec}"
            
            # step_type should be Insulation for this domain
            step_type = rec.get("step_type")
            assert step_type in ["Insulation", "INSULATION", "insulation"], (
                f"Expected step_type to be Insulation, got: {step_type}"
            )
        
        # Check that at least one recommendation relates to attic insulation install
        rec_summaries = " ".join(r.get("summary", "") for r in recommendations).lower()
        attic_install_indicators = [
            "attic", "install", "add insulation", "blow-in", "blown-in",
            "fiberglass", "cellulose", "insulate"
        ]
        
        has_attic_install = any(
            ind in rec_summaries for ind in attic_install_indicators
        )
        
        assert has_attic_install, (
            f"Expected recommendation related to attic insulation installation, "
            f"got summaries: {[r.get('summary') for r in recommendations]}"
        )
        
        print(f"\n✓ Recommendations mode test passed")
        print(f"  Number of recommendations: {len(recommendations)}")
        for i, rec in enumerate(recommendations, 1):
            print(f"  {i}. {rec.get('summary')[:100]}...")
            if rec.get("service_id"):
                print(f"     Service ID: {rec.get('service_id')}")
