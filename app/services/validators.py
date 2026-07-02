"""
Lightweight validators for Brazilian personal-data fields collected at
signup (CPF, telefone). Used to (a) reject obviously-fake input and
(b) normalize values before hashing/encryption so equivalent inputs
(with or without punctuation) dedupe correctly.
"""
import re


def only_digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def normalize_cpf(value: str | None) -> str:
    """Strip punctuation, return the 11-digit CPF string (or '' if not 11 digits)."""
    digits = only_digits(value)
    return digits if len(digits) == 11 else ""


def validate_cpf(value: str | None) -> bool:
    """
    Validate a CPF using the standard mod-11 checksum algorithm.
    Rejects malformed input and known-fake sequences (e.g. '11111111111').
    """
    cpf = normalize_cpf(value)
    if not cpf:
        return False
    if cpf == cpf[0] * 11:
        return False

    def _checksum(digits: str, weight_start: int) -> int:
        total = sum(int(d) * w for d, w in zip(digits, range(weight_start, 1, -1)))
        rest = (total * 10) % 11
        return 0 if rest == 10 else rest

    d1 = _checksum(cpf[:9], 10)
    d2 = _checksum(cpf[:9] + str(d1), 11)
    return cpf[-2:] == f"{d1}{d2}"


def format_cpf(value: str | None) -> str:
    cpf = normalize_cpf(value)
    if not cpf:
        return value or ""
    return f"{cpf[0:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:11]}"


def normalize_telefone(value: str | None) -> str:
    """Strip punctuation, return digits only (keeps leading country code if present)."""
    return only_digits(value)


def validate_telefone(value: str | None) -> bool:
    """
    Accepts Brazilian mobile/landline numbers: 10-11 digits (DDD + number),
    optionally prefixed with the '55' country code (12-13 digits total).
    """
    digits = normalize_telefone(value)
    if len(digits) in (12, 13) and digits.startswith("55"):
        digits = digits[2:]
    return len(digits) in (10, 11)


def normalize_cnpj(value: str | None) -> str:
    """Strip punctuation, return the 14-digit CNPJ string (or '' if not 14 digits)."""
    digits = only_digits(value)
    return digits if len(digits) == 14 else ""


def validate_cnpj(value: str | None) -> bool:
    """
    Validate a CNPJ using the standard mod-11 checksum algorithm.
    Rejects malformed input and known-fake sequences (e.g. all same digit).
    Catches most typos/transposed digits offline, with zero external dependency.
    """
    cnpj = normalize_cnpj(value)
    if not cnpj:
        return False
    if cnpj == cnpj[0] * 14:
        return False

    def _checksum(digits: str, weights: list[int]) -> int:
        total = sum(int(d) * w for d, w in zip(digits, weights))
        rest = total % 11
        return 0 if rest < 2 else 11 - rest

    d1 = _checksum(cnpj[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    d2 = _checksum(cnpj[:12] + str(d1), [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return cnpj[-2:] == f"{d1}{d2}"


def format_cnpj(value: str | None) -> str:
    cnpj = normalize_cnpj(value)
    if not cnpj:
        return value or ""
    return f"{cnpj[0:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:14]}"
