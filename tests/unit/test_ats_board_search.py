"""Testes offline para boards públicos ATS (Greenhouse/Lever/Ashby) e fila local."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from core.job_search import (
    compute_dynamic_fit_score,
    fetch_ashby_boards,
    fetch_greenhouse_boards,
    fetch_lever_boards,
    load_discovered_jobs,
    merge_discovered_jobs,
    update_discovered_job,
)


@pytest.mark.asyncio
async def test_fetch_greenhouse_lever_ashby_boards_with_respx():
    with respx.mock(assert_all_called=False) as router:
        router.get("https://boards-api.greenhouse.io/v1/boards/nubank/jobs?content=true").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": 101,
                            "title": "Senior Python Backend Engineer",
                            "absolute_url": "https://boards.greenhouse.io/nubank/jobs/101",
                            "location": {"name": "Remote Brazil"},
                            "content": "<p>Python FastAPI PostgreSQL</p>",
                            "updated_at": "2026-11-10T12:00:00Z",
                        }
                    ]
                },
            )
        )
        router.get("https://api.lever.co/v0/postings/quintoandar?mode=json").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": "lev-1",
                        "text": "Python Tech Lead",
                        "hostedUrl": "https://jobs.lever.co/quintoandar/lev-1",
                        "categories": {"location": "São Paulo / Remote"},
                        "descriptionPlain": "Python microservices",
                        "createdAt": 1700000000000,
                    }
                ],
            )
        )
        router.get("https://api.ashbyhq.com/posting-api/job-board/vercel").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": "ash-1",
                            "title": "Full Stack Engineer (TypeScript)",
                            "jobUrl": "https://jobs.ashbyhq.com/vercel/ash-1",
                            "location": "Remote",
                            "isRemote": True,
                            "descriptionPlain": "TypeScript Next.js React",
                            "publishedAt": "2026-11-11T09:00:00Z",
                        }
                    ]
                },
            )
        )

        gh_jobs = await fetch_greenhouse_boards(boards=["nubank"], query="python", limit=10)
        lv_jobs = await fetch_lever_boards(boards=["quintoandar"], query="python", limit=10)
        as_jobs = await fetch_ashby_boards(boards=["vercel"], query="typescript", limit=10)

    assert len(gh_jobs) == 1
    assert gh_jobs[0]["ats"] == "Greenhouse"
    assert gh_jobs[0]["source_platform"] == "greenhouse_board"

    assert len(lv_jobs) == 1
    assert lv_jobs[0]["ats"] == "Lever"
    assert lv_jobs[0]["source_platform"] == "lever_board"

    assert len(as_jobs) == 1
    assert as_jobs[0]["ats"] == "Ashby"
    assert as_jobs[0]["source_platform"] == "ashby_board"


def test_discovered_jobs_local_queue_merge_and_update(tmp_path: Path):
    queue_file = tmp_path / "discovered_jobs.json"
    jobs_batch_1 = [
        {
            "title": "Dev Python",
            "company": "Nubank",
            "job_url": "https://boards.greenhouse.io/nubank/jobs/101/",
            "ats": "Greenhouse",
            "external_job_id": "gh-101",
        }
    ]
    all_jobs, added_1 = merge_discovered_jobs(jobs_batch_1, queue_path=queue_file)
    assert added_1 == 1
    assert len(all_jobs) == 1

    # Duplicata com barra final diferente não deve duplicar
    all_jobs_2, added_2 = merge_discovered_jobs(
        [
            {
                "title": "Dev Python",
                "company": "Nubank",
                "job_url": "https://boards.greenhouse.io/nubank/jobs/101",
                "ats": "Greenhouse",
            },
            {
                "title": "Backend Gupy",
                "company": "Empresa X",
                "job_url": "https://empresax.gupy.io/jobs/555",
                "ats": "Gupy",
                "external_job_id": "gupy-555",
            },
        ],
        queue_path=queue_file,
    )
    assert added_2 == 1
    assert len(all_jobs_2) == 2

    updated = update_discovered_job(
        "gh-101",
        {"sheet_status": "Pronto", "tailored_resume_path": "cache/generated_resumes/gh_101.pdf"},
        queue_path=queue_file,
    )
    assert updated is not None
    assert updated["sheet_status"] == "Pronto"

    reloaded = load_discovered_jobs(queue_file)
    by_id = {j.get("id"): j for j in reloaded}
    assert by_id["gh-101"]["tailored_resume_path"] == "cache/generated_resumes/gh_101.pdf"


def test_compute_dynamic_fit_score_rewards_skill_matches():
    high_match_job = {
        "title": "Python FastAPI Engineer",
        "description": "Experience with Python, FastAPI, PostgreSQL and Docker.",
        "stack": ["Python", "FastAPI", "PostgreSQL"],
        "modality": "100% Remoto",
    }
    low_match_job = {
        "title": "COBOL Mainframe Specialist",
        "description": "JCL, CICS, DB2 mainframe.",
        "stack": ["COBOL"],
        "modality": "Presencial",
    }
    skills = ["Python", "FastAPI", "PostgreSQL", "Docker"]

    high = compute_dynamic_fit_score(high_match_job, skills)
    low = compute_dynamic_fit_score(low_match_job, skills)
    assert high > low
    assert 50 <= low <= high <= 99
