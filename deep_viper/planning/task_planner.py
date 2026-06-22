from dataclasses import dataclass
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from deep_viper.scene.state import SceneState
from deep_viper.scene.renderer import load_scene_image, image_to_base64
from deep_viper.vlm.client import extract_json
from deep_viper.vlm.prompts import task_planning_prompt
from deep_viper.planning.conflict import SimulatedScene


@dataclass
class SubTask:
    step: int
    op: str       # move_to | pick | place
    args: dict
    stack_onto: int | None = None  # if set, exclude this obj_id from obstacles for this move_to


def plan_tasks(goal: str, state: SceneState, llm: ChatOpenAI) -> tuple[list[SubTask], str]:
    objects = [
        {"id": o.id, "label": o.label, "center": o.center, "bbox": o.bbox}
        for o in state.objects
    ]

    # Build find_free_spot tool scoped to this scene
    sim = SimulatedScene(state.objects)
    image_size = state.image_size

    @tool
    def find_free_spot_near(object_id: int) -> list[int]:
        """
        Find a free pixel coordinate near the given object where another object
        can be placed without overlapping anything. Use this when the goal says
        'place near', 'next to', 'beside', or 'close to' an object.
        Returns [x, y] pixel coordinates of a free spot.
        """
        obj = sim.get_object(object_id)
        if obj is None:
            return [image_size["width"] // 2, image_size["height"] // 2]
        # Search from the object's location outward
        free = sim.find_free_spot_near(object_id, image_size)
        print(f"  [Tool] find_free_spot_near(obj_{object_id}) -> {free}")
        return free

    tools = [find_free_spot_near]
    llm_with_tools = llm.bind_tools(tools)

    prompt = task_planning_prompt(goal, objects)
    scene_img = load_scene_image(state)
    img_b64 = image_to_base64(scene_img)

    print(f"\n[TaskPlanner] Sending goal + scene image to VLM: '{goal}'")

    # Build initial message with image
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
    ]
    messages = [HumanMessage(content=content)]

    # Agentic loop: handle tool calls until VLM produces final JSON
    tool_map = {"find_free_spot_near": find_free_spot_near}
    max_rounds = 5
    for round_i in range(max_rounds):
        response = llm_with_tools.invoke(messages)
        messages.append(response)

        # Check for tool calls
        tool_calls = getattr(response, "tool_calls", None)
        if tool_calls:
            for tc in tool_calls:
                fn_name = tc["name"]
                fn_args = tc["args"]
                print(f"  [TaskPlanner] Tool call: {fn_name}({fn_args})")
                if fn_name in tool_map:
                    result = tool_map[fn_name].invoke(fn_args)
                else:
                    result = f"Unknown tool: {fn_name}"
                messages.append(ToolMessage(
                    content=str(result),
                    tool_call_id=tc["id"],
                ))
        else:
            # No tool calls — this is the final plan response
            raw = response.content if hasattr(response, "content") else str(response)
            break
    else:
        raw = response.content if hasattr(response, "content") else ""

    data = extract_json(raw, llm)
    subtasks = [SubTask(**s) for s in data.get("subtasks", [])]
    reason = (data.get("reason") or "").strip()
    print(f"[TaskPlanner] Decomposed into {len(subtasks)} sub-tasks. Reason: {reason or '(none given)'}")
    for s in subtasks:
        print(f"  Step {s.step}: {s.op}({s.args})")
    return subtasks, reason
