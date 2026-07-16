"""Model-facing phases of the experiment lifecycle."""
from __future__ import annotations

import json
from typing import Optional

from pydantic import ValidationError

from . import db, prompts
from .config import config
from .models import CheckinAction, Plan
from .ollama_client import extract_json_object, ollama
from .tool_loader import registry
from .ws import hub

MAX_JSON_RETRIES = 3


async def _trace(exp_id: str, type_: str, payload: dict) -> None:
    event = await db.add_event(exp_id, type_, payload)
    await hub.broadcast("trace", {"exp": exp_id, "event": event})


async def run_planning(exp_id: str, prompt: str, options: dict) -> Optional[Plan]:
    """Research (tool loop) + plan. Returns a validated Plan or None on failure."""
    phase = "plan"
    model = options.get("model") or config.model_for(phase)
    think = config.think_for(phase)
    max_iters = int(config.get("limits.max_plan_tool_iterations", 12))

    registry.maybe_reload()
    tools = registry.schemas()

    messages: list[dict] = [
        {"role": "system", "content": prompts.PLAN_SYSTEM},
        {"role": "user", "content": prompt},
    ]
    await db.add_message(exp_id, phase, "system", prompts.PLAN_SYSTEM, model=model, think=think)
    await db.add_message(exp_id, phase, "user", prompt, model=model, think=think)

    json_retries = 0
    for _ in range(max_iters):
        reply = await ollama.chat(model, messages, tools=tools or None, think=think)

        if reply["tool_calls"]:
            call = reply["tool_calls"][0]
            await db.add_message(exp_id, phase, "assistant", "",
                                 tool_calls=[call], model=model, think=think)
            await _trace(exp_id, "toolcall", {"name": call["name"], "arguments": call["arguments"]})
            result = await registry.run(call["name"], call["arguments"])
            await _trace(exp_id, "toolresult",
                         {"name": call["name"], "result": result[:2000]})
            messages.append({"role": "assistant",
                             "content": f"(calling tool {call['name']} with {json.dumps(call['arguments'])})"})
            messages.append(ollama.tool_result_message(call["name"], result))
            await db.add_message(exp_id, phase, "tool", result, model=model)
            continue

        content = reply["content"]
        await db.add_message(exp_id, phase, "assistant", content, model=model, think=think)

        obj = extract_json_object(content)
        plan_dict = obj.get("plan") if isinstance(obj, dict) else None
        if plan_dict is None and isinstance(obj, dict) and "files" in obj:
            plan_dict = obj  # model skipped the wrapper key
        if plan_dict is None:
            json_retries += 1
            if json_retries > MAX_JSON_RETRIES:
                break
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content":
                             "That was not a valid plan. Reply with ONLY the fenced "
                             '```json {"plan": {...}} ``` block exactly as specified.'})
            continue

        try:
            plan = Plan.model_validate(plan_dict)
        except ValidationError as e:
            json_retries += 1
            if json_retries > MAX_JSON_RETRIES:
                break
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content":
                             f"The plan failed validation:\n{e}\n"
                             "Fix these problems and resend ONLY the corrected "
                             '```json {"plan": {...}} ``` block.'})
            continue

        await _trace(exp_id, "plan", {"title": plan.title})
        return plan

    await _trace(exp_id, "error", {"message": "planning failed: no valid plan produced"})
    return None


async def run_checkin(exp_id: str, *, objective: str, success_criteria: str,
                      elapsed_minutes: float, container_state: str,
                      exit_code: Optional[int], log_tail: str,
                      revision: int, max_revisions: int) -> CheckinAction:
    phase = "checkin"
    model = config.model_for(phase)
    think = config.think_for(phase)

    user = (
        f"Objective: {objective}\n"
        f"Success criteria: {success_criteria}\n"
        f"Elapsed: {elapsed_minutes:.0f} min. Revision {revision}/{max_revisions}.\n"
        f"Container state: {container_state}"
        + (f" (exit code {exit_code})" if exit_code is not None else "")
        + f"\n\nLog tail:\n{log_tail or '(no output yet)'}"
    )
    messages = [
        {"role": "system", "content": prompts.CHECKIN_SYSTEM},
        {"role": "user", "content": user},
    ]
    await db.add_message(exp_id, phase, "user", user, model=model, think=think)

    for attempt in range(MAX_JSON_RETRIES + 1):
        reply = await ollama.chat(model, messages, think=think)
        content = reply["content"]
        await db.add_message(exp_id, phase, "assistant", content, model=model, think=think)
        obj = extract_json_object(content)
        if isinstance(obj, dict):
            try:
                action = CheckinAction.model_validate(obj)
                await _trace(exp_id, "checkin", {
                    "action": action.action, "reasoning": action.reasoning[:2000],
                    "notes": action.notes_for_ui, "revision": revision,
                })
                return action
            except ValidationError as e:
                err = str(e)
        else:
            err = "no JSON object found"
        if attempt < MAX_JSON_RETRIES:
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content":
                             f"Invalid check-in response ({err}). Reply with ONLY the "
                             "fenced JSON block in the specified format."})

    # Malformed after retries: default to continue and flag it, never kill the run.
    await _trace(exp_id, "warning",
                 {"message": "check-in response malformed after retries; defaulting to continue"})
    return CheckinAction(action="continue", reasoning="malformed response; defaulted",
                         notes_for_ui="check-in response was malformed; continuing")


async def run_conclusion(exp_id: str, *, objective: str, success_criteria: str,
                         log_tail: str, artifacts: list[str],
                         outcome: str) -> str:
    phase = "conclude"
    model = config.model_for(phase)
    think = config.think_for(phase)
    user = (
        f"Objective: {objective}\n"
        f"Success criteria: {success_criteria}\n"
        f"Outcome: {outcome}\n"
        f"Artifacts produced: {', '.join(artifacts) if artifacts else '(none)'}\n\n"
        f"Final log tail:\n{log_tail or '(no output)'}"
    )
    messages = [
        {"role": "system", "content": prompts.CONCLUDE_SYSTEM},
        {"role": "user", "content": user},
    ]
    await db.add_message(exp_id, phase, "user", user, model=model, think=think)
    reply = await ollama.chat(model, messages, think=think)
    conclusion = reply["content"].strip()
    await db.add_message(exp_id, phase, "assistant", conclusion, model=model, think=think)

    embedding = await ollama.embed(f"{objective}\n{conclusion}")
    await db.set_conclusion(exp_id, conclusion, embedding)
    await _trace(exp_id, "conclusion", {"conclusion": conclusion[:2000]})
    return conclusion


async def run_chat(exp_id: str, user_message: str, log_tail: str) -> str:
    phase = "chat"
    model = config.model_for(phase)
    think = config.think_for(phase)
    exp = await db.get_experiment(exp_id)
    context = (
        f"Experiment {exp_id}: {exp.get('title') or ''}\n"
        f"Status: {exp.get('status')}\n"
        f"Plan: {json.dumps(exp.get('plan') or {}, indent=2)[:4000]}\n"
        f"Conclusion: {exp.get('conclusion') or '(none yet)'}\n"
        f"Recent logs:\n{log_tail[:4000]}"
    )
    history = await db.list_messages(exp_id)
    chat_history = [
        {"role": m["role"], "content": m["content"]}
        for m in history if m["phase"] == "chat" and m["role"] in ("user", "assistant")
    ][-10:]
    messages = ([{"role": "system", "content": prompts.CHAT_SYSTEM + "\n\n" + context}]
                + chat_history + [{"role": "user", "content": user_message}])
    await db.add_message(exp_id, phase, "user", user_message, model=model, think=think)
    reply = await ollama.chat(model, messages, think=think)
    answer = reply["content"].strip()
    await db.add_message(exp_id, phase, "assistant", answer, model=model, think=think)
    return answer
