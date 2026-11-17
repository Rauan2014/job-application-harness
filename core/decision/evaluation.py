"""Funções puras de avaliação e calibração estatística para decisões do Laya."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from config.settings import Settings


def accuracy(predictions: Sequence[Any], labels: Sequence[Any]) -> float:
    """Acurácia pura: fração de predições que coincidem com os rótulos."""
    if not predictions or len(predictions) != len(labels):
        return 0.0
    correct = sum(1 for p, y in zip(predictions, labels, strict=False) if p == y)
    return correct / len(predictions)


def selective_accuracy(
    predictions: Sequence[Any], labels: Sequence[Any], abstain: Any = "abstain"
) -> float:
    """Acurácia apenas sobre as predições não-abstidas."""
    if not predictions or len(predictions) != len(labels):
        return 0.0
    pairs = [(p, y) for p, y in zip(predictions, labels, strict=False) if p != abstain]
    if not pairs:
        return 0.0
    correct = sum(1 for p, y in pairs if p == y)
    return correct / len(pairs)


def majority_baseline(labels: Sequence[Any]) -> float:
    """Acurácia do classificador que sempre chuta a classe mais frequente."""
    if not labels:
        return 0.0
    counts = Counter(labels)
    most_common_count = counts.most_common(1)[0][1]
    return most_common_count / len(labels)


def expected_calibration_error(
    probabilities: Sequence[float], correct: Sequence[bool], num_bins: int = 10
) -> float:
    """Expected Calibration Error (ECE) com num_bins (padrão 10)."""
    n = len(probabilities)
    if n == 0 or len(correct) != n:
        return 0.0

    ece = 0.0
    bin_size = 1.0 / num_bins
    for b in range(num_bins):
        bin_min = b * bin_size
        bin_max = (b + 1) * bin_size
        # No último bin incluímos 1.0 no limite superior
        indices = [
            i
            for i, p in enumerate(probabilities)
            if bin_min <= p < bin_max or (b == num_bins - 1 and p == 1.0)
        ]
        if not indices:
            continue
        bin_weight = len(indices) / n
        bin_acc = sum(1 for i in indices if correct[i]) / len(indices)
        bin_conf = sum(probabilities[i] for i in indices) / len(indices)
        ece += bin_weight * abs(bin_acc - bin_conf)

    return ece


def coverage(bands: Sequence[str]) -> float:
    """Fração de decisões assertivas (não 'uncertain')."""
    if not bands:
        return 0.0
    decisive = sum(1 for b in bands if b != "uncertain")
    return decisive / len(bands)


def passes(dp_stats: dict[str, Any], settings: Settings) -> bool:
    """Regra de aprovação para ativar DP1, DP2 ou DP5.

    Critérios:
    - n >= EVAL_MIN_EXAMPLES
    - accuracy >= majority_baseline + EVAL_MARGIN
    - pelo menos 50% dos exemplos NÃO são sintéticos
    - coverage >= EVAL_MIN_COVERAGE
    """
    n = int(dp_stats.get("n", 0))
    if n < settings.EVAL_MIN_EXAMPLES:
        return False

    acc = float(dp_stats.get("accuracy", 0.0))
    base = float(dp_stats.get("majority_baseline", 0.0))
    if acc < base + settings.EVAL_MARGIN:
        return False

    synthetic_count = int(dp_stats.get("synthetic_count", 0))
    non_synthetic = n - synthetic_count
    if (non_synthetic / n) < 0.50:
        return False

    cov = float(dp_stats.get("coverage", 1.0))
    min_cov = getattr(settings, "EVAL_MIN_COVERAGE", 0.5)
    return not cov < min_cov
