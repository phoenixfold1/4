"""Scene Coder — reference image → final Three.js module.

`SceneCoderAgent` (in `agent.py`) takes the reference image directly and
emits the JS module in one multimodal pass. Prompts live in `prompts.py`;
the shared Three.js primitive reference is in `threejs_reference.py`.
"""
from modules.scene_coder.agent import SceneCoderAgent

__all__ = ["SceneCoderAgent"]
