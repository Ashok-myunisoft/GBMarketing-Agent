from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


class PromptService:

    PROMPT_DIR = BASE_DIR / "app" / "prompts"

    @classmethod
    def load(cls, prompt_name: str) -> str:

        file_path = cls.PROMPT_DIR / f"{prompt_name}.md"

        if not file_path.exists():
            raise FileNotFoundError(f"{prompt_name}.md not found.")

        return file_path.read_text(encoding="utf-8")