"""Eski repository içe aktarımları için uyumluluk katmanı.

Hata sınıflarının tek kaynağı ``dortgoz.errors`` modülüdür. Geri bildirim
katmanındaki mevcut adapter'lar bu ince modül üzerinden aynı sınıfları kullanır.
"""

from ..errors import (
    RepositoryConflictError,
    RepositoryDuplicateError,
    RepositoryError,
    RepositoryNotFoundError,
)

__all__ = [
    "RepositoryConflictError",
    "RepositoryDuplicateError",
    "RepositoryError",
    "RepositoryNotFoundError",
]
