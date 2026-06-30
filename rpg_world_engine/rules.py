from __future__ import annotations


DEFAULT_DICE_RULES = {
    "version": 1,
    "dm_force_patterns": [
        "一定",
        "必须",
        "强制",
        "必定",
        "不能",
        "不要让",
        "不允许",
        "确保",
    ],
    "roll_triggers": {
        "minimum_score": 2,
        "pace_score": {"beat": 0, "scene": 1, "sequence": 2, "downtime": 2},
        "divergence_score": {"low": 0, "medium": 1, "high": 2},
        "risk_words": ["战", "潜入", "偷", "追", "骗", "冲突", "打", "危险", "谈判", "调查", "逃", "杀"],
        "risk_word_score": 2,
    },
    "dice": {
        "type": "d20",
        "min_roll": 1,
        "max_roll": 20,
        "bonus_choices": [0, 1, 2],
        "difficulty_min": 5,
        "difficulty_max": 20,
        "pace_base": {"beat": 10, "scene": 12, "sequence": 14, "downtime": 13},
        "divergence_modifier": {"low": -1, "medium": 0, "high": 2},
        "random_difficulty_jitter": [-2, 2],
        "partial_success_margin": 3,
    },
}
