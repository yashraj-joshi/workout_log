"""Prompt text. Kept apart from the call sites so wording changes are reviewable."""

# Verbatim from the brief. The model writes prose only; every number in the
# facts block was computed in Python.
SUMMARY_SYSTEM = (
    "Write the day summary in 2-3 short plain sentences (never more than 4, "
    "under 60 words; shorter is better). Say what I did and the totals, which "
    "muscles got the most sets, any main area I skipped, and what went up, down "
    "or stayed the same versus last time. No hype, no emoji, no advice, never "
    "mention volume."
)
