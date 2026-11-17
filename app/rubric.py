"""A consistent usefulness rubric; scores are judgments, not business-policy controls."""

CRITERIA = {
    "relevance": "Does the message address the selected, evidence-supported objective?",
    "clarity": "Is the wording understandable and free from ambiguity?",
    "personalization": "Does it use appropriate verified context without unnecessary personal data?",
    "call_to_action": "Is the next step clear and proportionate, including no required action for appreciation?",
    "tone": "Is it respectful and appropriate for the selected objective and reminder stage?",
}
SCALE = {1: "Unusable", 2: "Major revision needed", 3: "Acceptable with edits", 4: "Good", 5: "Excellent"}


def validate_scores(scores):
    if not isinstance(scores, dict):
        raise ValueError("Rubric scores must be an object.")
    if scores and (set(scores) != set(CRITERIA) or
                   any(type(value) is not int or not 1 <= value <= 5 for value in scores.values())):
        raise ValueError("Provide all five rubric scores as integers from 1 to 5, or leave scores empty.")
    return scores


def summarize_reviews(reviews):
    # Count only the latest judgment per revision and source, avoiding repeated-click weighting.
    latest = {}
    for review in sorted(reviews, key=lambda r: (r.created_at, r.id)):
        if review.action in {"approve", "reject"}:
            latest[(review.message_id, review.review_source)] = review
    result = {}
    for source in ("human", "automated", "unspecified"):
        group = [r for r in latest.values() if r.review_source == source]
        scored = [r for r in group if r.scores and set(r.scores) == set(CRITERIA)]
        result[source] = {"reviews": len(group), "scored_reviews": len(scored),
                          "mean_scores": {key: round(sum(r.scores[key] for r in scored) / len(scored), 2)
                                          for key in CRITERIA} if scored else {}}
    result["unscored_reviews"] = sum(not r.scores for r in latest.values())
    result["rubric_version"] = "usefulness-v1"
    return result
