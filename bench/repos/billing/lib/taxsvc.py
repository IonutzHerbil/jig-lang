"""Tax rate service client."""


class TaxServiceDown(Exception):
    """The tax service did not answer."""


RATES = {"US": 0.0, "DE": 19.0, "FR": 20.0, "GB": 20.0}


def rate_for(country: str) -> float:
    """The sales tax percent for a country code (0 for countries without one)."""
    if country == "XX":
        raise TaxServiceDown("tax service unavailable")
    return RATES.get(country, 0.0)
