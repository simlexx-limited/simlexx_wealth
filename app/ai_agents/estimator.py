import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING

from app.model.mod_enum import IncomeFrequency, SavingFrequency

PERIOD_DAYS = {
    IncomeFrequency.DAILY: 1,
    IncomeFrequency.WEEKLY: 7,
    IncomeFrequency.MONTHLY: 30.44,
    IncomeFrequency.VARIES: 30.44,
}
SAVING_FREQUENCY = {
    IncomeFrequency.DAILY: SavingFrequency.DAILY,
    IncomeFrequency.WEEKLY: SavingFrequency.WEEKLY,
    IncomeFrequency.MONTHLY: SavingFrequency.MONTHLY,
    IncomeFrequency.VARIES: SavingFrequency.MONTHLY,
}
SUGGESTED_PACE_SHARE = Decimal("0.5")
ROUNDING_STEP = Decimal("1000")


@dataclass(frozen=True)
class Estimate:
    frequency: SavingFrequency
    remaining: Decimal
    periods_until_target: int
    required: Decimal
    suggested_pace: Decimal
    periods_at_suggested_pace: int
    suggested_pace_date: date
    show_suggested_pace: bool


def round_up(amount: Decimal) -> Decimal:
    return (amount / ROUNDING_STEP).to_integral_value(rounding=ROUND_CEILING) * ROUNDING_STEP


def estimate(
    *,
    target_amount: Decimal,
    current_amount: Decimal,
    minimum_income: Decimal,
    income_regularity: IncomeFrequency,
    target_date: date,
    today: date,
) -> Estimate:
    remaining = target_amount - current_amount
    period_days = PERIOD_DAYS[income_regularity]
    periods_until_target = max(1, math.floor((target_date - today).days / period_days))
    required = round_up(remaining / periods_until_target)

    suggested_pace = minimum_income * SUGGESTED_PACE_SHARE
    periods_at_suggested_pace = math.ceil(remaining / suggested_pace)
    suggested_pace_date = today + timedelta(days=round(periods_at_suggested_pace * period_days))

    return Estimate(
        frequency=SAVING_FREQUENCY[income_regularity],
        remaining=remaining,
        periods_until_target=periods_until_target,
        required=required,
        suggested_pace=suggested_pace,
        periods_at_suggested_pace=periods_at_suggested_pace,
        suggested_pace_date=suggested_pace_date,
        show_suggested_pace=required > suggested_pace,
    )
