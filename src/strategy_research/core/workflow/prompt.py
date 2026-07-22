from __future__ import annotations

from pathlib import Path


class PromptBuilder:
    def __init__(self, prompts_dir: Path | str | None = None) -> None:
        if prompts_dir is None:
            prompts_dir = (
                Path(__file__).parent.parent.parent / "templates" / ".prompts"
            )
        self._prompts_dir = Path(prompts_dir)
        self._cache: dict[str, str] = {}

    def load_prompt(self, agent_name: str) -> str:
        if agent_name in self._cache:
            return self._cache[agent_name]

        prompt_file = self._prompts_dir / f"{agent_name}.md"
        if not prompt_file.exists():
            return ""

        content = prompt_file.read_text(encoding="utf-8")
        self._cache[agent_name] = content
        return content

    def build_prompt(
        self,
        agent_name: str,
        base_prompt: str = "",
        context: dict | None = None,
        upstream_outputs: dict[str, dict] | None = None,
    ) -> str:
        parts: list[str] = []

        template = self.load_prompt(agent_name)
        if template:
            parts.append(template)
            if base_prompt:
                parts.append(f"\n\n## Additional Instructions\n\n{base_prompt}")
        elif base_prompt:
            parts.append(base_prompt)

        if upstream_outputs:
            upstream_section = self._format_upstream(upstream_outputs)
            if upstream_section:
                parts.append(upstream_section)

        if context:
            context_section = self._format_context(context)
            if context_section:
                parts.append(context_section)

        return "\n".join(parts)

    def _format_upstream(self, outputs: dict[str, dict]) -> str:
        lines = ["\n\n## Upstream Agent Outputs\n"]
        for agent_name, output in outputs.items():
            lines.append(f"### {agent_name}")
            if isinstance(output, dict):
                for key, value in output.items():
                    lines.append(f"- **{key}**: {value}")
            else:
                lines.append(f"```json\n{output}\n```")
            lines.append("")
        return "\n".join(lines)

    def _format_context(self, context: dict) -> str:
        lines = ["\n\n## Current Context\n"]
        for key, value in context.items():
            if key == "input_from":
                continue
            if isinstance(value, dict):
                lines.append(f"- **{key}**: {value}")
            else:
                lines.append(f"- **{key}**: {value}")
        return "\n".join(lines)

    def clear_cache(self) -> None:
        self._cache.clear()
