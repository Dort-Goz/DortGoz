class RepositoryError(RuntimeError):

    code = "REPOSITORY_ERROR"


class RepositoryNotFoundError(RepositoryError):
    code = "NOT_FOUND"


class RepositoryDuplicateError(RepositoryError):
    code = "DUPLICATE_RECORD"


class RepositoryConflictError(RepositoryError):
    code = "REVISION_CONFLICT"
