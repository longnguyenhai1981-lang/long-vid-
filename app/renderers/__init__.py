"""Motily execution/rendering layer: turns a locked plan into real media.

Deliberately separate from app/engines/ (reasoning/planning against an LLM).
Nothing under app/renderers/ may import app.llm or generate a prompt --
renderers only execute what a planning engine already decided.
"""
