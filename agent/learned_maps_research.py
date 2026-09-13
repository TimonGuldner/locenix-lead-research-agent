from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from agent import deterministic_research

ROOT = Path(__file__).resolve().parents[1]
TASK = ROOT / "tasks" / "current_task.json"
LEARNING = ROOT / "results" / "maps_learning_config.json"
STATE = ROOT / "results" / "deterministic_state.json"
EXECUTION = ROOT / "results" / "maps_learning_execution.json"


def load(path: Path, default: Any):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ordered(options: list[str], weights: dict[str, float], recommended: list[str]) -> list[str]:
    seen = set()
    result: list[str] = []
    for item in recommended:
        if item in options and item not in seen:
            result.append(item)
            seen.add(item)
    for item in sorted(options, key=lambda x: (-float(weights.get(x, 1.0)), x)):
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result


def rotate(items: list[str], start: int, count: int) -> list[str]:
    if not items:
        return []
    count = min(max(1, count), len(items))
    return [items[(start + i) % len(items)] for i in range(count)]


def choose_plan(task: dict[str, Any], learning: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    cities = [str(x) for x in task.get("cities") or []]
    industries = [str(x) for x in task.get("categories") or []]
    qpr = max(1, int(task.get("queries_per_run", 3)))
    run_no = int(state.get("runs") or 0)
    learning_run = int(learning.get("learning_run_count") or 0)
    exploration_share = float(learning.get("exploration_share", 0.15))

    ranked_cities = ordered(cities, learning.get("city_weights") or {}, learning.get("recommended_cities") or [])
    ranked_industries = ordered(industries, learning.get("industry_weights") or {}, learning.get("recommended_industries") or [])

    # Deterministic exploration: roughly one out of every 7 cycles for a 15% target.
    explore_every = max(4, round(1.0 / max(0.05, min(0.5, exploration_share))))
    exploration = learning_run > 0 and learning_run % explore_every == 0

    if exploration:
        city_start = (run_no * qpr) % max(1, len(cities))
        industry_start = ((run_no + 3) * qpr) % max(1, len(industries))
        selected_cities = rotate(cities, city_start, min(qpr, len(cities)))
        selected_industries = rotate(industries, industry_start, min(qpr, len(industries)))
        focus = "exploration"
    elif run_no % 2 == 0:
        # Exploit best industry across several cities.
        selected_industries = ranked_industries[:1] or industries[:1]
        selected_cities = rotate(ranked_cities, run_no % max(1, len(ranked_cities)), min(qpr, len(ranked_cities)))
        focus = "industry_exploitation"
    else:
        # Exploit best city across several industries.
        selected_cities = ranked_cities[:1] or cities[:1]
        selected_industries = rotate(ranked_industries, run_no % max(1, len(ranked_industries)), min(qpr, len(ranked_industries)))
        focus = "city_exploitation"

    # Keep the Cartesian plan at qpr or fewer combinations so the underlying deterministic
    # agent executes exactly the learning-selected slice rather than a huge reordered matrix.
    if len(selected_cities) > 1 and len(selected_industries) > 1:
        if focus == "exploration":
            selected_industries = selected_industries[:1]
        elif focus == "industry_exploitation":
            selected_industries = selected_industries[:1]
        else:
            selected_cities = selected_cities[:1]

    combinations = [(city, industry) for city in selected_cities for industry in selected_industries]
    combinations = combinations[:qpr]
    return {
        "focus": focus,
        "exploration": exploration,
        "exploration_share": exploration_share,
        "selected_cities": selected_cities,
        "selected_industries": selected_industries,
        "selected_pairs": [{"city": c, "industry": i, "query": f"{i} {c}"} for c, i in combinations],
        "queries_per_run": min(qpr, max(1, len(combinations))),
    }


async def main() -> None:
    original = load(TASK, {})
    if not original.get("enabled"):
        print(json.dumps({"status": "disabled"}))
        return
    learning = load(LEARNING, {})
    state = load(STATE, {})
    plan = choose_plan(original, learning, state)

    temporary = dict(original)
    temporary["cities"] = plan["selected_cities"] or original.get("cities", [])
    temporary["categories"] = plan["selected_industries"] or original.get("categories", [])
    temporary["queries_per_run"] = plan["queries_per_run"]

    save(EXECUTION, {
        "agent": "AGENT_4L_MAPS_RESEARCH_LEARNING_OPTIMIZER",
        "status": "PLANNED",
        "learning_run_count": learning.get("learning_run_count", 0),
        **plan,
    })

    TASK.write_text(json.dumps(temporary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        await deterministic_research.main()
        save(EXECUTION, {
            "agent": "AGENT_4L_MAPS_RESEARCH_LEARNING_OPTIMIZER",
            "status": "EXECUTED",
            "learning_run_count": learning.get("learning_run_count", 0),
            **plan,
        })
    finally:
        TASK.write_text(json.dumps(original, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
