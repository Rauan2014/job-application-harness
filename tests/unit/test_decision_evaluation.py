"""Testes unitários para as métricas puras de avaliação (decision/evaluation)."""

from config.settings import Settings
from core.decision.evaluation import (
    accuracy,
    coverage,
    expected_calibration_error,
    majority_baseline,
    passes,
    selective_accuracy,
)


def test_accuracy_and_majority_baseline() -> None:
    preds = ["yes", "yes", "no", "yes"]
    labels = ["yes", "no", "no", "yes"]
    # 3 acertos em 4 -> 0.75
    assert accuracy(preds, labels) == 0.75

    # Maioria em labels: 2 "yes" e 2 "no" -> 0.50
    assert majority_baseline(labels) == 0.50

    labels_imbalanced = ["yes", "yes", "yes", "no"]
    assert majority_baseline(labels_imbalanced) == 0.75


def test_selective_accuracy() -> None:
    preds = ["yes", "abstain", "no", "yes"]
    labels = ["yes", "yes", "no", "no"]
    # 2 acertos em 3 avaliados (1 abstenção ignorada) -> 2/3
    assert abs(selective_accuracy(preds, labels) - 2 / 3) < 1e-6
    assert selective_accuracy([], []) == 0.0
    assert selective_accuracy(["abstain"], ["yes"]) == 0.0


def test_ece_known_cases() -> None:
    # Caso perfeitamente calibrado: probabilidade 1.0 e 100% de acertos
    probs = [1.0, 1.0, 1.0, 1.0]
    correct = [True, True, True, True]
    assert expected_calibration_error(probs, correct) == 0.0

    # Caso com 100% de erro e 1.0 de probabilidade -> ECE = 1.0
    wrong = [False, False, False, False]
    assert expected_calibration_error(probs, wrong) == 1.0

    # Caso vazio
    assert expected_calibration_error([], []) == 0.0


def test_coverage() -> None:
    bands = ["yes", "uncertain", "no", "yes"]
    assert coverage(bands) == 0.75
    assert coverage([]) == 0.0


def test_passes_criteria(test_settings: Settings) -> None:
    # settings default: EVAL_MIN_EXAMPLES=30, EVAL_MARGIN=0.10, EVAL_MIN_COVERAGE=0.5
    valid_stats = {
        "n": 40,
        "accuracy": 0.85,
        "majority_baseline": 0.60,  # 0.85 >= 0.60 + 0.10
        "synthetic_count": 15,       # 25 não-sintéticos de 40 (62.5% >= 50%)
        "coverage": 0.80,           # 80% >= 50%
    }
    assert passes(valid_stats, test_settings) is True

    # 1. Falha por n insuficiente
    stats_small_n = dict(valid_stats, n=20)
    assert passes(stats_small_n, test_settings) is False

    # 2. Falha por margem insuficiente
    stats_low_acc = dict(valid_stats, accuracy=0.68)  # 0.68 < 0.60 + 0.10
    assert passes(stats_low_acc, test_settings) is False

    # 3. Falha por excesso de sintéticos (>50%)
    stats_mostly_synthetic = dict(valid_stats, synthetic_count=30)  # 10 não-sintéticos (25% < 50%)
    assert passes(stats_mostly_synthetic, test_settings) is False

    # 4. Falha por cobertura insuficiente (<50%)
    stats_low_coverage = dict(valid_stats, coverage=0.40)
    assert passes(stats_low_coverage, test_settings) is False
