"""Avaliação estatística do Laya contra exemplos padrão (decision_goldens.json).

Gera o relatório LAYA_EVAL_FILE com acurácia, baseline majoritário, ECE e aprovação.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Garante importação a partir da raiz do projeto
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import Settings, load_settings  # noqa: E402
from core.decision.evaluation import (  # noqa: E402
    coverage,
    expected_calibration_error,
    majority_baseline,
    passes,
    selective_accuracy,
)
from core.decision.laya_client import LayaSidecarClient  # noqa: E402
from core.models import DecisionUnavailable  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("eval_decisions")


async def evaluate(
    goldens_path: Path,
    out_path: Path,
    settings: Settings,
) -> int:
    if not await asyncio.to_thread(goldens_path.exists):
        print(f"Erro: Arquivo {goldens_path} não encontrado.", file=sys.stderr)
        return 1

    goldens_raw = await asyncio.to_thread(goldens_path.read_text, encoding="utf-8")
    goldens: list[dict[str, Any]] = json.loads(goldens_raw)
    client = LayaSidecarClient(settings)

    print("\n" + "=" * 70)
    print("AVALIAÇÃO ESTATÍSTICA DO SIDECAR LAYA")
    print("=" * 70)

    try:
        health = await client.health()
        print(f"Status do Sidecar: OK (degraded={health.demoted_to_cpu})")
    except DecisionUnavailable as exc:
        print(f"\n[ERRO CRÍTICO] Sidecar Laya não está respondendo em {settings.LAYA_URL}: {exc}", file=sys.stderr)
        print("Inicie o sidecar primeiro com: laya-mcp serve --model multilingual --port 8787", file=sys.stderr)
        return 2

    # Agrupa por DP
    by_dp: dict[str, list[dict[str, Any]]] = {}
    for item in goldens:
        by_dp.setdefault(item["dp"], []).append(item)

    report: dict[str, Any] = {}
    now_iso = datetime.now(UTC).isoformat()

    print("\nExecutando inferências tipadas por Ponto de Decisão (DP)...")

    for dp in sorted(by_dp.keys()):
        examples = by_dp[dp]
        predictions: list[str] = []
        labels: list[str] = []
        probabilities: list[float] = []
        correct_list: list[bool] = []
        bands: list[str] = []
        synthetic_count = 0

        for ex in examples:
            if ex.get("synthetic", False):
                synthetic_count += 1
            expected = str(ex["label"])
            labels.append(expected)

            try:
                if "options" in ex:
                    # choice
                    verdict = await client.choice(
                        state=ex["state"],
                        instructions=ex["instructions"],
                        options=ex["options"],
                    )
                    qualifies = verdict.order_consistent and verdict.margin >= settings.LAYA_MIN_MARGIN
                    band = "yes" if qualifies else "uncertain"
                    pred = verdict.top if qualifies else "abstain"
                    prob = verdict.probs.get(verdict.top, 0.5)
                else:
                    # noul
                    noul_verdict = await client.noul(
                        state=ex["state"],
                        instructions=ex["instructions"],
                        criteria=ex.get("criteria"),
                    )
                    band = noul_verdict.band
                    pred = {"yes": "yes", "no": "no", "uncertain": "abstain"}[band]
                    prob = noul_verdict.p_true if pred == "yes" else 1.0 - noul_verdict.p_true

                predictions.append(pred)
                probabilities.append(prob)
                bands.append(band)
                correct_list.append(pred == expected)
            except DecisionUnavailable as exc:
                log.warning("Falha ao avaliar exemplo em %s: %s", dp, exc)
                predictions.append("abstain")
                probabilities.append(0.5)
                bands.append("uncertain")
                correct_list.append(False)

        n = len(examples)
        acc = selective_accuracy(predictions, labels)
        base = majority_baseline(labels)
        ece = expected_calibration_error(probabilities, correct_list, num_bins=10)
        cov = coverage(bands)

        dp_stats = {
            "n": n,
            "accuracy": round(acc, 4),
            "majority_baseline": round(base, 4),
            "ece": round(ece, 4),
            "coverage": round(cov, 4),
            "synthetic_count": synthetic_count,
            "non_synthetic": n - synthetic_count,
            "ts": now_iso,
        }
        dp_stats["passed"] = passes(dp_stats, settings)
        report[dp] = dp_stats

    # Grava relatório
    await asyncio.to_thread(out_path.parent.mkdir, parents=True, exist_ok=True)
    report_json = json.dumps(report, indent=2, ensure_ascii=False)
    await asyncio.to_thread(out_path.write_text, report_json, encoding="utf-8")
    print(f"\nRelatório de avaliação salvo em: {out_path}\n")

    # Imprime tabela
    print(f"{'DP':<6} | {'N':<4} | {'Acurácia':<9} | {'Baseline':<9} | {'ECE':<7} | {'Cobertura':<9} | {'Aprovado?'}")
    print("-" * 65)
    for dp, st in sorted(report.items()):
        status = "SIM [ATIVO]" if st["passed"] else "NÃO [SHADOW]"
        acc = f"{st['accuracy']:<9.2%}"
        base = f"{st['majority_baseline']:<9.2%}"
        ece = f"{st['ece']:<7.3f}"
        cov = f"{st['coverage']:<9.2%}"
        print(f"{dp:<6} | {st['n']:<4} | {acc} | {base} | {ece} | {cov} | {status}")

    print("-" * 65)
    print("\nAVISO IMPORTANTE:")
    print("O resultado em dados puramente sintéticos (synthetic=True) NÃO comprova desempenho")
    print("em vagas reais e reprova automaticamente os DPs que liberam ação (DP1, DP2 e DP5).")
    print("Para ativar DP1, DP2 ou DP5 em produção:")
    print("1. Adicione exemplos reais rotulados em data/decision_goldens.json com 'synthetic': false.")
    print("2. Garanta ao menos 30 exemplos por DP com pelo menos 50% de exemplos reais.")
    print("3. Execute novamente este script e confirme se a acurácia superou o baseline + 10%.\n")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Avaliação de decisões do Laya")
    parser.add_argument("--goldens", type=Path, default=Path("data/decision_goldens.json"), help="Caminho do dataset")
    parser.add_argument("--out", type=Path, default=None, help="Caminho de saída do JSON")
    args = parser.parse_args()

    fallback_kwargs = {
        "LLM_API_KEY": "eval_dummy_key",
        "LLM_BASE_URL": "http://localhost:8000/v1",
        "LLM_MODEL": "eval_dummy_model",
        "DRIVE_FOLDER_ID": "eval_dummy_folder",
    }
    kwargs = {}
    if args.out is not None:
        kwargs["LAYA_EVAL_FILE"] = args.out

    try:
        settings = load_settings(**kwargs)
    except Exception:
        settings = load_settings(**{**fallback_kwargs, **kwargs})

    out = args.out or settings.LAYA_EVAL_FILE
    exit_code = asyncio.run(evaluate(args.goldens, out, settings))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
