"""Payload-free errors and strict scalar validation for the MT03 candidate."""

import math


class ContractError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def integer(value, minimum, maximum, code="invalid_integer"):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ContractError(code)
    return value


def seconds(value, *, minimum=0, maximum=1e12, code="invalid_time"):
    if (type(value) not in (int, float) or not minimum <= value <= maximum
            or not math.isfinite(value)):
        raise ContractError(code)
    return value


def octets(value, size, code="invalid_octets"):
    if type(value) is not bytes or len(value) != size:
        raise ContractError(code)
    return value


def exact_fields(value, fields, code="invalid_fields"):
    if type(value) is not dict or set(value) != set(fields):
        raise ContractError(code)
    return value
