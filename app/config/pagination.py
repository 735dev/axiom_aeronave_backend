"""Pagination limits shared by every listing."""

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_PAGE_NUMBER = 100_000
MAX_PENDING_PAGE_SIZE = (
    50  # GET /attachments/pending/: each row carries a reading and an image link
)
