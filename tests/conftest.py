import pytest


@pytest.fixture
def symlink():
    """Skip only link-specific checks when Windows denies link creation."""

    def create(link, target):
        try:
            link.symlink_to(target)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("Windows user cannot create symbolic links (WinError 1314)")
            raise

    return create
