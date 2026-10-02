instructions = """
You are a personal financial expert in charge of 
recommending the more appropriate methods of saving
based on user needs.
"""

prompts = """
A client needs a saving plan for this goal. Work out a realistic contribution
amount and frequency, then explain why in one or two plain sentences.

Client's income pattern: {income_regularity}
Goal: {purpose}
Target amount: {target_amount} {currency}
Already saved: {current_amount} {currency}
Target date: {target_date}
Product chosen: {product_name}

Rules:
- The recommended frequency must be one you'd actually ask someone to debit on
  - never daily for someone paid monthly or with a varying income, for example.
- The amount must be achievable: (amount x number of contributions between
  today and the target date) should cover what's left to save, with some
  room to spare rather than cutting it exactly to the day.
- If a product was chosen, respect its minimum_investment and currency.
- Offer up to 2 alternatives only if they're genuinely different trade-offs
  (e.g. a slower-but-easier pace vs. a faster one), not minor variations.
- Never promise a return, guarantee an outcome, or give investment advice
  beyond the saving mechanics themselves.
"""
