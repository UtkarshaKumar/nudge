"""
Evaluation suite for testing the LLM-based action item extraction.
Runs the `ActionExtractor` against a golden dataset in `dataset.json` and 
scores the output for precision, recall, and attribute accuracy.

Usage:
    python -m src.evals.run
"""
import json
import logging
import sys
from pathlib import Path

from ..config import load_config
from ..extraction.ollama_client import ActionExtractor
from ..extraction.dedup import normalize, similarity

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

EVALS_DIR = Path(__file__).parent
DATASET_FILE = EVALS_DIR / "dataset.json"


def _match_action(extracted: dict, expected: dict, threshold: float = 0.75) -> bool:
    """Check if an extracted action item matches an expected one based on task text similarity."""
    extr_task = extracted.get("task", "")
    exp_task = expected.get("task", "")
    return similarity(extr_task, exp_task) >= threshold


def _compare_attribute(extracted_val, expected_val) -> bool:
    """Compare assignee or deadline, handling None gracefully."""
    # If expected is None, we don't strictly penalize if LLM guesses something,
    # but ideally it should also be None or empty. Let's aim for exact match or close text.
    if not expected_val:
        return not extracted_val or str(extracted_val).lower() in ["none", "null", ""]
    
    if not extracted_val:
        return False
        
    # Simple similarity for attributes
    return similarity(str(extracted_val), str(expected_val)) >= 0.70


def run_evals():
    if not DATASET_FILE.exists():
        logger.error(f"Dataset not found at {DATASET_FILE}")
        sys.exit(1)

    logger.info("Loading Golden Dataset...")
    with open(DATASET_FILE, "r") as f:
        cases = json.load(f)

    logger.info(f"Loaded {len(cases)} cases. Initializing ActionExtractor...\n")
    config = load_config()
    
    # We force deterministic generation for testing
    config.ollama.temperature = 0.0 
    
    extractor = ActionExtractor(config.ollama)

    total_expected = 0
    total_extracted = 0
    true_positives = 0  # Extracted tasks that match expected tasks
    total_owner_score = 0
    total_deadline_score = 0
    evals_with_expected = 0

    for case in cases:
        logger.info(f"▶ Running case: {case['id']}")
        transcript = case["transcript"]
        expected_actions = case["expected_actions"]
        
        # 1. Extract
        extracted_actions = extractor.extract(transcript)
        
        # 2. Score
        total_expected += len(expected_actions)
        total_extracted += len(extracted_actions)
        
        case_matched_expected = 0
        
        matched_extracted_indices = set()
        
        for exp in expected_actions:
            # Find the best match among extracted
            best_match_idx = -1
            best_score = 0.0
            
            for i, extr in enumerate(extracted_actions):
                if i in matched_extracted_indices:
                    continue
                score = similarity(extr.get("task", ""), exp.get("task", ""))
                if score > best_score:
                    best_score = score
                    best_match_idx = i
                    
            if best_match_idx >= 0 and best_score >= 0.75:
                # We have a match
                matched_extracted_indices.add(best_match_idx)
                case_matched_expected += 1
                true_positives += 1
                
                # Check attributes
                matched_extr = extracted_actions[best_match_idx]
                if _compare_attribute(matched_extr.get("assignee"), exp.get("assignee")):
                    total_owner_score += 1
                if _compare_attribute(matched_extr.get("deadline_raw"), exp.get("deadline_raw")):
                    total_deadline_score += 1
                    
                evals_with_expected += 1
                
        # Logging case results
        missing = len(expected_actions) - case_matched_expected
        hallucinated = len(extracted_actions) - case_matched_expected
        logger.info(f"  Expected: {len(expected_actions)} | Extracted: {len(extracted_actions)}")
        if missing > 0:
            logger.info(f"  [!] Missing: {missing}")
        if hallucinated > 0:
            logger.info(f"  [?] Hallucinated: {hallucinated}")
        logger.info("-" * 40)

    # 3. Final Report
    logger.info("\n=== EVALUATION RESULTS ===")
    
    # Recall: Fraction of expected tasks that were successfully extracted
    recall = true_positives / total_expected if total_expected > 0 else 1.0
    
    # Precision: Fraction of extracted tasks that were actually expected
    precision = true_positives / total_extracted if total_extracted > 0 else 1.0
    
    # F1 Score
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    
    # Attribute accuracy (only measured on true positives)
    owner_acc = total_owner_score / evals_with_expected if evals_with_expected > 0 else 1.0
    deadline_acc = total_deadline_score / evals_with_expected if evals_with_expected > 0 else 1.0
    
    logger.info(f"Total transcripts tested: {len(cases)}")
    logger.info(f"Total expected actions:   {total_expected}")
    logger.info(f"Total extracted actions:  {total_extracted}")
    logger.info("")
    logger.info(f"Task Recall:            {recall:.1%}")
    logger.info(f"Task Precision:         {precision:.1%}")
    logger.info(f"Task F1 Score:          {f1:.1%}")
    logger.info("")
    if evals_with_expected > 0:
        logger.info(f"Owner accuracy:         {owner_acc:.1%} (on properly extracted tasks)")
        logger.info(f"Deadline accuracy:      {deadline_acc:.1%} (on properly extracted tasks)")
        
    if f1 < 0.8:
        logger.warning("\n[!] Pipeline performance is under 80% F1. Consider improving prompts or lowering deduplication threshold.")

if __name__ == "__main__":
    run_evals()
