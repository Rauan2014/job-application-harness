import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from automation.browser import BrowserManager
from config.settings import load_settings
from core.decision.layer import DecisionLayer
from core.matcher import Matcher
from core.models import Job, Profile
from core.pipeline import Pipeline
from core.resume_resolver import ResumeResolver

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

async def main():
    settings = load_settings()
    browser_manager = BrowserManager(settings=settings)
    await browser_manager.start()

    try:
        resolver = ResumeResolver(cache_dir=settings.CACHE_DIR)
        resolver.load_cache()
        print(f"Resumos carregados: {len(resolver.get_resumes())}")

        profile_text = await asyncio.to_thread(Path("data/profile.json").read_text, encoding="utf-8")
        profile = Profile.model_validate_json(profile_text)
        decision_layer = DecisionLayer(model=None, settings=settings)
        matcher = Matcher(settings=settings)

        pipeline = Pipeline(
            settings=settings,
            browser_manager=browser_manager,
            decision_layer=decision_layer,
            matcher=matcher,
            resume_resolver=resolver,
        )

        job = Job(
            id="audax-solucoes-ai-developer-saas",
            row_index=6,
            company="Audax Soluções",
            title="AI Developer – SaaS",
            url="https://br.indeed.com/jobs?q=audax",
            resume_label="Currículo Otimizado - Audax Soluções.pdf",
        )

        print(f"\n--- Iniciando teste da vaga: {job.company} - {job.title} ---")
        result = await pipeline.process_job(job=job, profile=profile)
        print("\n=== RESULTADO ===")
        print(f"Status: {result.status}")
        print(f"Dry Run: {result.dry_run}")
        print(f"Submitted: {result.submitted}")
        print(f"Resume Usado: {result.resume_used}")
        print(f"Motivo / Detalhes: {result.reason}")

    finally:
        await browser_manager.stop()

if __name__ == "__main__":
    asyncio.run(main())
