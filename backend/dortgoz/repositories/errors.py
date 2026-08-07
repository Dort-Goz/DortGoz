"""Repository katmanının typed hata sınıfları."""


class RepositoryError(RuntimeError):
    """Beklenen repository hatalarının tabanı."""

    code = "REPOSITORY_ERROR"


class RepositoryNotFoundError(RepositoryError):
    code = "NOT_FOUND"


class RepositoryDuplicateError(RepositoryError):
    code = "DUPLICATE_RECORD"


class RepositoryConflictError(RepositoryError):
    code = "REVISION_CONFLICT"
