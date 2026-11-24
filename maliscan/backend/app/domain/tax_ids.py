"""Turkish tax identifier checks (TCKN / VKN)."""

from __future__ import annotations


def is_valid_tckn(value: str) -> bool:
    """T.C. Kimlik No: 11 digits, first non-zero, two check digits."""
    if len(value) != 11 or not value.isdigit() or value[0] == "0":
        return False
    d = [int(c) for c in value]
    d10 = ((d[0] + d[2] + d[4] + d[6] + d[8]) * 7 - (d[1] + d[3] + d[5] + d[7])) % 10
    d11 = sum(d[:10]) % 10
    return d[9] == d10 and d[10] == d11


def is_valid_vkn(value: str) -> bool:
    """Vergi Kimlik No (10 digits) — GİB checksum algorithm."""
    if len(value) != 10 or not value.isdigit():
        return False
    total = 0
    for i in range(9):
        tmp1 = (int(value[i]) + (9 - i)) % 10
        tmp2 = (tmp1 * (2 ** (9 - i))) % 9
        if tmp1 != 0 and tmp2 == 0:
            tmp2 = 9
        total += tmp2
    return (10 - total % 10) % 10 == int(value[9])


def make_valid_vkn(prefix9: str) -> str:
    """Append the check digit (used for synthetic test data)."""
    for last in "0123456789":
        if is_valid_vkn(prefix9 + last):
            return prefix9 + last
    raise ValueError(prefix9)


def make_valid_tckn(prefix9: str) -> str:
    d = [int(c) for c in prefix9]
    d10 = ((d[0] + d[2] + d[4] + d[6] + d[8]) * 7 - (d[1] + d[3] + d[5] + d[7])) % 10
    d11 = (sum(d) + d10) % 10
    return f"{prefix9}{d10}{d11}"
