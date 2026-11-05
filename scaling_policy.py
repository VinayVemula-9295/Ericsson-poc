"""Small, deterministic policy helpers for EC2 instance type recommendations."""
from __future__ import annotations

_INSTANCE_SIZE_RANK = {
    "nano": 0, "micro": 1, "small": 2, "medium": 3, "large": 4,
    "xlarge": 5, "2xlarge": 6, "4xlarge": 7, "8xlarge": 8,
    "12xlarge": 9, "16xlarge": 10, "24xlarge": 11, "metal": 12,
}


def _instance_rank(instance_type: str) -> tuple[str, int] | None:
    if "." not in instance_type:
        return None
    family, size = instance_type.split(".", 1)
    rank = _INSTANCE_SIZE_RANK.get(size)
    return (family, rank) if rank is not None else None


def directional_instance_candidates(allowed: list[str], offered: set[str], current_type: str, signal: str | None) -> list[str]:
    """Return only offered, same-family targets in the requested scaling direction."""
    current_rank = _instance_rank(current_type)
    if current_rank is None or signal not in {"scale_up", "scale_down"}:
        return [item for item in allowed if item != current_type and item in offered]
    family, rank = current_rank
    candidates = []
    for candidate in allowed:
        candidate_rank = _instance_rank(candidate)
        if not candidate_rank or candidate_rank[0] != family or candidate not in offered:
            continue
        if signal == "scale_down" and candidate_rank[1] < rank:
            candidates.append(candidate)
        elif signal == "scale_up" and candidate_rank[1] > rank:
            candidates.append(candidate)
    candidates.sort(key=lambda item: abs(_instance_rank(item)[1] - rank))
    return candidates
