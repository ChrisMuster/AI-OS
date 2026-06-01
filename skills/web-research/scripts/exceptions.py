"""
Typed exceptions for the web-research skill.

Source adapters raise these instead of generic exceptions so that research.py
can distinguish between failure types and report them accurately.
"""


class ResearchSourceError(Exception):
    """Base class for all source-level errors."""
    pass


class QuotaExceededError(ResearchSourceError):
    """
    API quota or rate limit hit.

    Raised on HTTP 429 (Too Many Requests) or 402 (Payment Required).
    The source is unavailable for this run — either temporarily (rate limit)
    or until the quota resets (monthly cap). Either way, results from this
    source will be missing and the research package quality may be reduced.
    """
    pass


class AuthError(ResearchSourceError):
    """
    API key invalid, missing, or unauthorised.

    Raised on HTTP 401 or 403. This will not self-resolve — the key in .env
    needs to be checked or updated.
    """
    pass


class SourceUnavailableError(ResearchSourceError):
    """
    Source temporarily unavailable (5xx server error or connection timeout).

    May resolve on retry. Not a quota or auth issue.
    """
    pass


def check_response(resp, source_name):
    """
    Inspect an HTTP response and raise a typed exception for known error codes.
    Call this before resp.raise_for_status() in each source adapter.

    Args:
        resp: requests.Response object.
        source_name (str): Human-readable name for error messages.

    Raises:
        QuotaExceededError: on 429 or 402.
        AuthError: on 401 or 403.
        SourceUnavailableError: on 5xx.
    """
    code = resp.status_code

    if code == 429:
        raise QuotaExceededError(
            f'{source_name} rate limit hit (HTTP 429). '
            'Try again later or reduce query frequency.'
        )
    if code == 402:
        raise QuotaExceededError(
            f'{source_name} monthly quota exceeded (HTTP 402). '
            'Check your API plan or wait for the quota to reset.'
        )
    if code == 401:
        raise AuthError(
            f'{source_name} API key rejected (HTTP 401). '
            'Check the key in .env.'
        )
    if code == 403:
        raise AuthError(
            f'{source_name} access forbidden (HTTP 403). '
            'Key may be invalid, expired, or lacking permissions.'
        )
    if code >= 500:
        raise SourceUnavailableError(
            f'{source_name} server error (HTTP {code}). '
            'The service may be temporarily down.'
        )
