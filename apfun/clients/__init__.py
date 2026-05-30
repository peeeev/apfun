"""External API clients (one module per paid/rate-limited provider).

Each client centralizes auth + rate limiting + cost tracking for one provider so
those concerns aren't scattered through the consumers. See
`apfun/llm/client.py` for the same pattern applied to Anthropic.
"""
