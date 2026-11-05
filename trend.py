"""CPU-threshold state machine for sustained scale recommendations.

Only distinct, fresh CloudWatch CPU datapoints drive signals. The state machine
persists diagnostic counters so users can see why scale-down has or has not
triggered. It never changes AWS; execution still requires reviewer approval.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

HIGH_CPU_PERCENT = 70.0
LOW_CPU_PERCENT = 25.0
HIGH_STREAK_REQUIRED = 2
LOW_STREAK_REQUIRED = 3
# EC2 basic monitoring can publish every 5 minutes. The newest datapoint must be
# reasonably fresh; older distinct observations remain persisted between refreshes.
MAX_METRIC_AGE_MINUTES = 6
POST_CHANGE_COOLDOWN_MINUTES = 15


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _family_targets(instance_type: str) -> list[str]:
    if instance_type.startswith("t2."):
        return ["t2.micro", "t2.small", "t2.medium", "t2.large"]
    if instance_type.startswith("t3."):
        return ["t3.micro", "t3.small", "t3.medium", "t3.large"]
    if instance_type.startswith("t3a."):
        return ["t3a.micro", "t3a.small", "t3a.medium", "t3a.large"]
    if instance_type.startswith("t4g."):
        return ["t4g.micro", "t4g.small", "t4g.medium", "t4g.large"]
    return []


def _streak(history: list[dict[str, Any]], flag: str) -> int:
    count = 0
    for item in reversed(history):
        if item.get(flag):
            count += 1
        else:
            break
    return count


def _save_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2))


def start_post_change_cooldown(
    state_path: Path,
    instance_type: str,
    cooldown_minutes: int = POST_CHANGE_COOLDOWN_MINUTES,
) -> dict[str, Any]:
    """Reset trend history after an approved resize and suppress reversals during cooldown.

    Only datapoints timestamped after the cooldown boundary can count toward a new
    scale-up or scale-down event. This deliberately prevents a resize from being
    immediately followed by a reverse recommendation based on old/settling metrics.
    """
    now = datetime.now(timezone.utc)
    until = now + timedelta(minutes=max(1, int(cooldown_minutes)))
    try:
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
    except Exception:
        state = {}
    state.update({
        "observations": [],
        "last_emitted_signal": None,
        "last_emitted_metric_timestamp": None,
        "instance_type": instance_type,
        "metric_after_timestamp": now.isoformat(),
        "instance_type_changed_at": now.isoformat(),
        "cooldown_until": until.isoformat(),
        "diagnostics": {
            "instance_type": instance_type,
            "latest_cpu_percent": None,
            "latest_metric_timestamp": None,
            "data_fresh": False,
            "high_streak": 0,
            "high_required": HIGH_STREAK_REQUIRED,
            "low_streak": 0,
            "low_required": LOW_STREAK_REQUIRED,
            "scale_down_available": None,
            "cooldown_active": True,
            "cooldown_until": until.isoformat(),
            "message": f"Post-resize settling period active until {until.isoformat()}. The agent is idle and will evaluate only new datapoints after the cooldown.",
        },
    })
    _save_state(state_path, state)
    return state["diagnostics"]


def get_trend_diagnostics(state_path: Path) -> dict[str, Any]:
    """Return the latest persisted threshold progress for the UI."""
    try:
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
    except Exception:
        state = {}
    diagnostics = state.get("diagnostics")
    return diagnostics if isinstance(diagnostics, dict) else {
        "data_fresh": False,
        "message": "Waiting for a fresh CloudWatch CPU datapoint.",
        "high_streak": 0,
        "low_streak": 0,
        "scale_down_available": None,
    }


def evaluate_trend(metrics: dict[str, Any], instance: dict[str, Any], state_path: Path) -> dict[str, Any] | None:
    """Emit a signal only on sustained CPU threshold transitions.

    Scale-up: CPU >= 70% for two distinct fresh datapoints.
    Scale-down: CPU <= 25% for three distinct fresh datapoints, provided a
    smaller same-family type exists in the POC allowlist.

    If EC2's instance type changed since the last evaluation, old observations
    are discarded and the evaluator waits for datapoints newer than the latest
    previously observed timestamp. This prevents pre-resize samples from
    immediately triggering a reverse resize.
    """
    if not instance.get("available") or instance.get("state") != "running":
        return None
    instance_type = str(instance.get("instance_type", ""))
    targets = _family_targets(instance_type)
    if not targets or instance_type not in targets:
        return None

    now = datetime.now(timezone.utc)
    try:
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
    except Exception:
        state = {}

    previous_type = state.get("instance_type")
    old_history = state.get("observations", [])
    metric_after_timestamp = state.get("metric_after_timestamp")
    cooldown_until_value = state.get("cooldown_until")
    if previous_type and previous_type != instance_type:
        # Preserve post-resize cooldown and the timestamp boundary across an
        # observed instance-type change; never reuse the prior type's streak.
        prior_latest = old_history[-1].get("timestamp") if old_history else metric_after_timestamp
        state = {
            "observations": [],
            "last_emitted_signal": None,
            "last_emitted_metric_timestamp": None,
            "instance_type": instance_type,
            "metric_after_timestamp": prior_latest,
            "instance_type_changed_at": now.isoformat(),
            "cooldown_until": cooldown_until_value,
        }
        metric_after_timestamp = prior_latest
    else:
        state["instance_type"] = instance_type

    boundary = _parse_time(metric_after_timestamp)
    cooldown_dt = _parse_time(state.get("cooldown_until"))
    if cooldown_dt and (boundary is None or cooldown_dt > boundary):
        boundary = cooldown_dt
    cooldown_active = bool(cooldown_dt and now < cooldown_dt)
    cpu_points = sorted(
        metrics.get("metrics", {}).get("CPU", {}).get("points", []),
        key=lambda point: point.get("timestamp", ""),
    )
    fresh: list[dict[str, Any]] = []
    for point in cpu_points:
        ts = _parse_time(point.get("timestamp"))
        try:
            value = float(point.get("average"))
        except (TypeError, ValueError):
            continue
        if not ts or not (timedelta(0) <= now - ts <= timedelta(minutes=MAX_METRIC_AGE_MINUTES)):
            continue
        if boundary and ts <= boundary:
            continue
        fresh.append({
            "timestamp": ts.astimezone(timezone.utc).isoformat(),
            "cpu_percent": value,
            "high": value >= HIGH_CPU_PERCENT,
            "low": value <= LOW_CPU_PERCENT,
            "instance_type": instance_type,
        })

    prior_observations = state.get("observations", [])
    by_timestamp = {
        item.get("timestamp"): {
            **item,
            "instance_type": item.get("instance_type", instance_type),
        }
        for item in prior_observations
        if item.get("timestamp") and item.get("instance_type", instance_type) == instance_type
    }
    for item in fresh:
        by_timestamp[item["timestamp"]] = item
    history = sorted(by_timestamp.values(), key=lambda item: item["timestamp"])[-8:]

    current_index = targets.index(instance_type)
    scale_down_available = current_index > 0
    high_streak = _streak(history, "high")
    low_streak = _streak(history, "low")
    latest_time = history[-1]["timestamp"] if history else None
    latest_dt = _parse_time(latest_time)
    data_fresh = bool(latest_dt and timedelta(0) <= now - latest_dt <= timedelta(minutes=MAX_METRIC_AGE_MINUTES))

    diagnostic_message = ""
    if cooldown_active:
        diagnostic_message = f"Post-resize cooldown active until {cooldown_dt.isoformat()}. Agent is idle; datapoints from before the cooldown ends cannot trigger another resize."
    elif not data_fresh:
        diagnostic_message = "No fresh CPU datapoint is available yet. Wait for CloudWatch to publish a new distinct sample; dashboard refreshes do not create datapoints."
    elif low_streak >= LOW_STREAK_REQUIRED and not scale_down_available:
        diagnostic_message = f"CPU is low for {low_streak} consecutive datapoints, but {instance_type} is the smallest allowlisted type in this POC. Scale-down is not applicable until the workload has been scaled up to a larger type."
    elif low_streak > 0 and low_streak < LOW_STREAK_REQUIRED:
        diagnostic_message = f"Scale-down evaluation: {low_streak}/{LOW_STREAK_REQUIRED} distinct fresh datapoints at CPU ≤ {LOW_CPU_PERCENT:.0f}%."
    elif high_streak > 0 and high_streak < HIGH_STREAK_REQUIRED:
        diagnostic_message = f"Scale-up evaluation: {high_streak}/{HIGH_STREAK_REQUIRED} distinct fresh datapoints at CPU ≥ {HIGH_CPU_PERCENT:.0f}%."
    elif low_streak >= LOW_STREAK_REQUIRED and scale_down_available:
        diagnostic_message = f"Scale-down threshold met: {low_streak} distinct fresh low-CPU datapoints. A reviewer-approved resize recommendation can be prepared."
    elif high_streak >= HIGH_STREAK_REQUIRED and current_index < len(targets) - 1:
        diagnostic_message = f"Scale-up threshold met: {high_streak} distinct fresh high-CPU datapoints. A reviewer-approved resize recommendation can be prepared."
    else:
        diagnostic_message = "CPU is within the configured thresholds; agent is monitoring for a sustained event."

    diagnostics = {
        "instance_type": instance_type,
        "latest_cpu_percent": history[-1].get("cpu_percent") if history else None,
        "latest_metric_timestamp": latest_time,
        "data_fresh": data_fresh,
        "high_streak": high_streak,
        "high_required": HIGH_STREAK_REQUIRED,
        "low_streak": low_streak,
        "low_required": LOW_STREAK_REQUIRED,
        "scale_down_available": scale_down_available,
        "scale_down_target": targets[current_index - 1] if scale_down_available else None,
        "scale_up_available": current_index < len(targets) - 1,
        "scale_up_target": targets[current_index + 1] if current_index < len(targets) - 1 else None,
        "cooldown_active": cooldown_active,
        "cooldown_until": cooldown_dt.isoformat() if cooldown_dt else None,
        "message": diagnostic_message,
    }

    # Persist even when there is no event so Work/Agent can explain why a threshold
    # has not fired. If no new metric arrived, preserve the previous history/counters.
    state.update({
        "observations": history,
        "last_processed_at": now.isoformat(),
        "instance_type": instance_type,
        "diagnostics": diagnostics,
    })

    if not fresh or not data_fresh:
        _save_state(state_path, state)
        return None

    high_streak = _streak(history, "high")
    low_streak = _streak(history, "low")
    signal = None
    target_type = None
    if high_streak >= HIGH_STREAK_REQUIRED and diagnostics["scale_up_available"]:
        signal, target_type = "scale_up", diagnostics["scale_up_target"]
    elif low_streak >= LOW_STREAK_REQUIRED and scale_down_available:
        signal, target_type = "scale_down", diagnostics["scale_down_target"]

    previous_signal = state.get("last_emitted_signal")

    # Re-arm after two distinct samples outside the prior threshold condition.
    if previous_signal == "scale_up":
        below_high_streak = 0
        for item in reversed(history):
            if not item.get("high"):
                below_high_streak += 1
            else:
                break
        if below_high_streak >= 2:
            previous_signal = None
            state["last_emitted_signal"] = None
            state["last_emitted_metric_timestamp"] = None
    elif previous_signal == "scale_down":
        above_low_streak = 0
        for item in reversed(history):
            if not item.get("low"):
                above_low_streak += 1
            else:
                break
        if above_low_streak >= 2:
            previous_signal = None
            state["last_emitted_signal"] = None
            state["last_emitted_metric_timestamp"] = None

    if not signal or (signal is not None and signal == previous_signal):
        # Keep the latch but persist current streak progress.
        if signal is not None and signal == previous_signal:
            state["diagnostics"]["message"] = f"{signal.replace('_', ' ').title()} threshold remains true; already emitted for this episode. Waiting for the condition to clear before another event."
        _save_state(state_path, state)
        return None

    latest = history[-1]
    count = high_streak if signal == "scale_up" else low_streak
    event = {
        "event_id": f"cpu-trend-{signal}-{latest_time.replace(':', '').replace('-', '')}",
        "event_type": "sustained_cpu_trend",
        "trend_signal": signal,
        "trigger_metric": "CPUUtilization",
        "threshold_percent": HIGH_CPU_PERCENT if signal == "scale_up" else LOW_CPU_PERCENT,
        "required_consecutive_datapoints": HIGH_STREAK_REQUIRED if signal == "scale_up" else LOW_STREAK_REQUIRED,
        "started_at": history[-count]["timestamp"],
        "finished_at": now.isoformat(),
        "metric_timestamp": latest_time,
        "current_instance_type": instance_type,
        "suggested_instance_type": target_type,
        "cpu_percent": latest.get("cpu_percent"),
        "consecutive_datapoints": count,
        "evidence": history[-max(count, 4):],
        "recommendation_reason": (
            f"CPU averaged >= {HIGH_CPU_PERCENT:.0f}% for {high_streak} consecutive distinct CloudWatch datapoints; "
            f"review {instance_type} → {target_type}."
            if signal == "scale_up" else
            f"CPU averaged <= {LOW_CPU_PERCENT:.0f}% for {low_streak} consecutive distinct CloudWatch datapoints; "
            f"review {instance_type} → {target_type} to reduce compute cost."
        ),
        "note": "CPU-threshold signal from real CloudWatch datapoints. Recommendation only; no AWS changes have been made.",
    }
    state.update({
        "observations": history,
        "last_processed_at": now.isoformat(),
        "last_emitted_signal": signal,
        "last_emitted_metric_timestamp": latest_time,
        "diagnostics": {
            **diagnostics,
            "message": f"{signal.replace('_', ' ').title()} threshold met. A reviewer-approved recommendation was generated; no AWS change was applied automatically.",
        },
    })
    _save_state(state_path, state)
    return event
