import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.settings import load_settings
from core.matcher import Matcher
from core.models import FormQuestion, Profile


async def test():
    s = load_settings(LLM_MODEL="gemini-3.5-flash")
    matcher = Matcher(settings=s)
    profile_text = await asyncio.to_thread(Path("data/profile.json").read_text, encoding="utf-8")
    profile = Profile.model_validate_json(profile_text)

    questions = [
        FormQuestion(id="q1", label="How did you hear about this job?", kind="text", required=True),
        FormQuestion(id="q2", label="What is your salary expectation?", kind="text", required=True)
    ]

    res = await matcher.answer_questions(
        job="Engenheiro de IA",
        questions=questions,
        profile=profile,
        resume_text="Engenheiro de Inteligência Artificial com experiência em LLMs, Python, LangChain."
    )
    print("Sucesso! Respostas:", res.answers)
    await matcher.aclose()

if __name__ == "__main__":
    asyncio.run(test())
